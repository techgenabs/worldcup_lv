from fastapi import APIRouter, Depends, HTTPException

from ..database import get_db, row, rows
from ..deps import admin_user, current_user
from ..schemas import PredictionIn, PredictionUpdate
from ..services.audit import audit
from ..services.scoring import is_prediction_locked, lock_due_predictions, update_leaderboard

router = APIRouter(prefix="/predictions", tags=["predictions"])


def _auto_update_match_statuses(db) -> None:
    db.execute(
        """
        UPDATE matches
        SET status = 'live'
        WHERE status = 'scheduled'
          AND match_date IS NOT NULL
          AND match_date::timestamp < NOW()
        """
    )


def _match_for_prediction(db, match_id: int) -> dict:
    match = row(
        db.execute(
            """
            SELECT m.*, ht.name AS home_team, at.name AS away_team
            FROM matches m
            JOIN teams ht ON ht.id = m.home_team_id
            JOIN teams at ON at.id = m.away_team_id
            WHERE m.id = %s
            """,
            (match_id,),
        )
    )
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    if not match.get("predictions_open", 1):
        raise HTTPException(status_code=423, detail="Admin has closed predictions for this match")
    if is_prediction_locked(match):
        raise HTTPException(status_code=423, detail="Predictions are locked for this match")
    return match


@router.post("")
def create_prediction(payload: PredictionIn, user: dict = Depends(current_user)):
    with get_db() as db:
        lock_due_predictions(db)
        match = _match_for_prediction(db, payload.match_id)
        predicted_draw = 1 if payload.predicted_home_score == payload.predicted_away_score else 0
        predicted_team_id = None
        if payload.predicted_home_score > payload.predicted_away_score:
            predicted_team_id = match["home_team_id"]
        elif payload.predicted_away_score > payload.predicted_home_score:
            predicted_team_id = match["away_team_id"]
        db.execute(
            """
            INSERT INTO predictions (user_id, match_id, predicted_team_id, predicted_draw,
                                     predicted_home_score, predicted_away_score, confidence_level, confidence, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, match_id)
            DO UPDATE SET predicted_team_id = excluded.predicted_team_id,
                          predicted_draw = excluded.predicted_draw,
                          predicted_home_score = excluded.predicted_home_score,
                          predicted_away_score = excluded.predicted_away_score,
                          confidence_level = excluded.confidence_level,
                          confidence = excluded.confidence,
                          updated_at = CURRENT_TIMESTAMP
            """,
            (
                user["id"],
                payload.match_id,
                predicted_team_id,
                predicted_draw,
                payload.predicted_home_score,
                payload.predicted_away_score,
                payload.confidence_level,
                {"Low": 30, "Medium": 60, "High": 90}[payload.confidence_level],
            ),
        )
        audit(db, "prediction_create_or_update", "prediction", payload.match_id, user["id"], payload.model_dump())
        return row(db.execute(
            "SELECT * FROM predictions WHERE user_id = %s AND match_id = %s",
            (user["id"], payload.match_id)
        ))


@router.put("/{prediction_id}")
def update_prediction(prediction_id: int, payload: PredictionUpdate, user: dict = Depends(current_user)):
    with get_db() as db:
        prediction = row(db.execute(
            "SELECT * FROM predictions WHERE id = %s AND user_id = %s",
            (prediction_id, user["id"])
        ))
        if not prediction:
            raise HTTPException(status_code=404, detail="Prediction not found")
        match = _match_for_prediction(db, prediction["match_id"])
        predicted_draw = 1 if payload.predicted_home_score == payload.predicted_away_score else 0
        predicted_team_id = None
        if payload.predicted_home_score > payload.predicted_away_score:
            predicted_team_id = match["home_team_id"]
        elif payload.predicted_away_score > payload.predicted_home_score:
            predicted_team_id = match["away_team_id"]
        db.execute(
            """
            UPDATE predictions
            SET predicted_team_id = %s, predicted_draw = %s,
                predicted_home_score = %s, predicted_away_score = %s,
                confidence_level = %s, confidence = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (
                predicted_team_id,
                predicted_draw,
                payload.predicted_home_score,
                payload.predicted_away_score,
                payload.confidence_level,
                {"Low": 30, "Medium": 60, "High": 90}[payload.confidence_level],
                prediction_id,
            ),
        )
        audit(db, "prediction_update", "prediction", prediction_id, user["id"], payload.model_dump())
        return row(db.execute("SELECT * FROM predictions WHERE id = %s", (prediction_id,)))


@router.delete("/{prediction_id}")
def delete_prediction(prediction_id: int, user: dict = Depends(current_user)):
    with get_db() as db:
        prediction = row(db.execute(
            "SELECT * FROM predictions WHERE id = %s AND user_id = %s",
            (prediction_id, user["id"])
        ))
        if not prediction:
            raise HTTPException(status_code=404, detail="Prediction not found")
        _match_for_prediction(db, prediction["match_id"])
        db.execute("DELETE FROM predictions WHERE id = %s", (prediction_id,))
        audit(db, "prediction_delete", "prediction", prediction_id, user["id"])
    return {"status": "deleted"}


@router.get("/mine")
def mine(user: dict = Depends(current_user)):
    with get_db() as db:
        _auto_update_match_statuses(db)
        lock_due_predictions(db)
        return rows(
            db.execute(
                """
                SELECT p.*, m.game_no, m.match_date, m.lock_at, m.status,
                       ht.name AS home_team, at.name AS away_team
                FROM predictions p
                JOIN matches m ON m.id = p.match_id
                JOIN teams ht ON ht.id = m.home_team_id
                JOIN teams at ON at.id = m.away_team_id
                WHERE p.user_id = %s
                ORDER BY m.match_date DESC
                """,
                (user["id"],),
            )
        )


@router.get("/leaderboard")
def leaderboard(country: str | None = None, user: dict = Depends(current_user)):
    with get_db() as db:
        update_leaderboard(db)
        sql = """
            SELECT l.rank, u.id, u.name, u.country, l.total_points AS points, l.exact_matches,
                   l.winner_count, l.predictions_count AS predictions, l.accuracy, l.badges
            FROM leaderboards l
            JOIN users u ON u.id = l.user_id
        """
        params = ()
        if country:
            sql += " WHERE u.country = %s"
            params = (country,)
        sql += " ORDER BY l.rank LIMIT 50"
        return rows(db.execute(sql, params))


@router.get("/match/{match_id}")
def match_predictions(match_id: int, user: dict = Depends(current_user)):
    with get_db() as db:
        _auto_update_match_statuses(db)
        match = row(db.execute("SELECT * FROM matches WHERE id = %s", (match_id,)))
        if not match:
            raise HTTPException(status_code=404, detail="Match not found")

        if user["role"] == "admin":
            return rows(
                db.execute(
                    """
                    SELECT p.*,
                           u.name  AS user_name,
                           u.email AS user_email,
                           u.country AS user_country
                    FROM predictions p
                    JOIN users u ON u.id = p.user_id
                    WHERE p.match_id = %s
                    ORDER BY p.points_awarded DESC, p.updated_at
                    """,
                    (match_id,),
                )
            )

        if match["status"] in ("locked", "live", "completed"):
            return rows(
                db.execute(
                    """
                    SELECT p.*,
                           u.name    AS user_name,
                           u.country AS user_country
                    FROM predictions p
                    JOIN users u ON u.id = p.user_id
                    WHERE p.match_id = %s
                    ORDER BY p.points_awarded DESC, p.updated_at
                    """,
                    (match_id,),
                )
            )

        return rows(
            db.execute(
                "SELECT * FROM predictions WHERE match_id = %s AND user_id = %s",
                (match_id, user["id"]),
            )
        )


@router.get("/admin/all")
def all_predictions(admin: dict = Depends(admin_user)):
    with get_db() as db:
        _auto_update_match_statuses(db)
        return rows(
            db.execute(
                """
                SELECT p.*,
                       u.name    AS user_name,
                       u.email   AS user_email,
                       u.country AS user_country,
                       m.game_no, m.match_date, m.status AS match_status,
                       m.home_score, m.away_score,
                       ht.name AS home_team,
                       at.name AS away_team
                FROM predictions p
                JOIN users  u  ON u.id  = p.user_id
                JOIN matches m ON m.id  = p.match_id
                JOIN teams  ht ON ht.id = m.home_team_id
                JOIN teams  at ON at.id = m.away_team_id
                ORDER BY m.match_date DESC, u.name
                """
            )
        )


@router.get("/all")
def all_predictions_alias(admin: dict = Depends(admin_user)):
    return all_predictions(admin)


@router.get("/match-winners")
def match_winners(tournament_id: int | None = None, user: dict = Depends(current_user)):
    with get_db() as db:
        base_sql = """
            SELECT p.id, p.points_awarded, p.predicted_home_score, p.predicted_away_score,
                   p.updated_at,
                   u.id AS uid, u.name AS user_name, u.country AS user_country,
                   m.id AS mid, m.game_no, m.round, m.match_date, m.tournament_id,
                   m.home_score, m.away_score,
                   ht.name AS home_team, at.name AS away_team
            FROM predictions p
            JOIN users u ON u.id = p.user_id
            JOIN matches m ON m.id = p.match_id
            JOIN teams ht ON ht.id = m.home_team_id
            JOIN teams at ON at.id = m.away_team_id
            WHERE m.status = 'completed'
        """
        params: list = []
        if tournament_id:
            base_sql += " AND m.tournament_id = %s"
            params.append(tournament_id)

        if user["role"] != "admin":
            base_sql += " AND m.id IN (SELECT match_id FROM predictions WHERE user_id = %s)"
            params.append(user["id"])

        base_sql += " ORDER BY m.match_date DESC, p.points_awarded DESC, p.updated_at ASC"

        all_rows = rows(db.execute(base_sql, tuple(params)))

    by_match: dict[int, list[dict]] = {}
    for r in all_rows:
        by_match.setdefault(r["mid"], []).append(r)

    results = []
    for mid, preds in by_match.items():
        top_points = max(p["points_awarded"] or 0 for p in preds)
        winners = [p for p in preds if (p["points_awarded"] or 0) == top_points]
        sample = preds[0]
        results.append({
            "match_id":           mid,
            "game_no":            sample["game_no"],
            "round":              sample["round"],
            "match_date":         sample["match_date"],
            "home_team":          sample["home_team"],
            "away_team":          sample["away_team"],
            "home_score":         sample["home_score"],
            "away_score":         sample["away_score"],
            "total_participants": len(preds),
            "winning_points":     top_points,
            "winners": [
                {
                    "user_id":              w["uid"],
                    "user_name":            w["user_name"],
                    "user_country":         w["user_country"],
                    "predicted_home_score": w["predicted_home_score"],
                    "predicted_away_score": w["predicted_away_score"],
                    "points_awarded":       w["points_awarded"],
                }
                for w in winners
            ],
        })

    results.sort(key=lambda r: r["match_date"] or "", reverse=True)
    return {"matches": results, "total_completed_matches": len(results)}
