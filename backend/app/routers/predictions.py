from fastapi import APIRouter, Depends, HTTPException

from ..database import get_db, row, rows
from ..deps import admin_user, current_user
from ..schemas import PredictionIn, PredictionUpdate
from ..services.audit import audit
from ..services.scoring import is_prediction_locked, lock_due_predictions, update_leaderboard

router = APIRouter(prefix="/predictions", tags=["predictions"])


def _auto_update_match_statuses(db) -> None:
    """
    Automatically move matches from 'scheduled' to 'live'
    when their kickoff time has passed.
    Called on every predictions fetch so the UI stays in sync
    even if the scheduler hasn't run yet.
    """
    db.execute(
        """
        UPDATE matches
        SET status = 'live'
        WHERE status = 'scheduled'
          AND match_date IS NOT NULL
          AND match_date < datetime('now')
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
            WHERE m.id = ?
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
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
        return row(db.execute("SELECT * FROM predictions WHERE user_id = ? AND match_id = ?", (user["id"], payload.match_id)))


@router.put("/{prediction_id}")
def update_prediction(prediction_id: int, payload: PredictionUpdate, user: dict = Depends(current_user)):
    with get_db() as db:
        prediction = row(db.execute("SELECT * FROM predictions WHERE id = ? AND user_id = ?", (prediction_id, user["id"])))
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
            SET predicted_team_id = ?, predicted_draw = ?, predicted_home_score = ?, predicted_away_score = ?,
                confidence_level = ?, confidence = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
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
        return row(db.execute("SELECT * FROM predictions WHERE id = ?", (prediction_id,)))


@router.delete("/{prediction_id}")
def delete_prediction(prediction_id: int, user: dict = Depends(current_user)):
    with get_db() as db:
        prediction = row(db.execute("SELECT * FROM predictions WHERE id = ? AND user_id = ?", (prediction_id, user["id"])))
        if not prediction:
            raise HTTPException(status_code=404, detail="Prediction not found")
        _match_for_prediction(db, prediction["match_id"])
        db.execute("DELETE FROM predictions WHERE id = ?", (prediction_id,))
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
                WHERE p.user_id = ?
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
            sql += " WHERE u.country = ?"
            params = (country,)
        sql += " ORDER BY l.rank LIMIT 50"
        return rows(db.execute(sql, params))


# ─────────────────────────────────────────────────────────────────────────────
#  /predictions/match/{match_id}
#  Used by the Prediction List tab to load all participants' picks for a match.
#  - Admin  → always returns ALL predictions for that match
#  - User   → returns ALL predictions once match is locked/live/completed
#             returns only their own prediction while still scheduled
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/match/{match_id}")
def match_predictions(match_id: int, user: dict = Depends(current_user)):
    with get_db() as db:
        _auto_update_match_statuses(db)
        match = row(db.execute("SELECT * FROM matches WHERE id = ?", (match_id,)))
        if not match:
            raise HTTPException(status_code=404, detail="Match not found")

        # Admin always sees all predictions
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
                    WHERE p.match_id = ?
                    ORDER BY p.points_awarded DESC, p.updated_at
                    """,
                    (match_id,),
                )
            )

        # Users: reveal everyone's predictions once the match is LOCKED or
        # COMPLETED — i.e. as soon as predictions have closed for this game,
        # any participant can see how everyone else predicted it, even
        # before the final result is entered. Only while the match is still
        # "scheduled" (predictions open, kickoff hasn't happened) does a
        # user see only their own pick — this is what prevents bias while
        # people are still allowed to change their prediction.
        if match["status"] in ("locked", "live", "completed"):
            return rows(
                db.execute(
                    """
                    SELECT p.*,
                           u.name    AS user_name,
                           u.country AS user_country
                    FROM predictions p
                    JOIN users u ON u.id = p.user_id
                    WHERE p.match_id = ?
                    ORDER BY p.points_awarded DESC, p.updated_at
                    """,
                    (match_id,),
                )
            )

        # Scheduled only: user sees only their own prediction
        return rows(
            db.execute(
                "SELECT * FROM predictions WHERE match_id = ? AND user_id = ?",
                (match_id, user["id"]),
            )
        )


# ─────────────────────────────────────────────────────────────────────────────
#  /predictions/admin/all
#  Admin-only: returns every prediction across all matches with user + match info.
#  This is the primary source for the admin Prediction List tab.
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
#  /predictions/all
#  Alias of admin/all — kept for frontend compatibility.
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/all")
def all_predictions_alias(admin: dict = Depends(admin_user)):
    return all_predictions(admin)


# ─────────────────────────────────────────────────────────────────────────────
#  /predictions/match-winners
#  TYPE A — Per-game winner: for each individual COMPLETED match, who
#  predicted it best? Ranked purely by points_awarded (already computed by
#  your scoring service), tiebreak by earliest submission (updated_at).
#
#  TYPE B — Overall tournament winner is the existing /predictions/leaderboard
#  route above — cumulative points across the whole tournament. Unchanged.
#
#  Visibility: non-admin users only see matches they personally participated
#  in, matching the same "hidden until completed, then participants-only"
#  rule enforced in /predictions/match/{match_id} above. Admins see every
#  completed match.
# ─────────────────────────────────────────────────────────────────────────────
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
            base_sql += " AND m.tournament_id = ?"
            params.append(tournament_id)

        # Non-admins only see matches they participated in
        if user["role"] != "admin":
            base_sql += " AND m.id IN (SELECT match_id FROM predictions WHERE user_id = ?)"
            params.append(user["id"])

        base_sql += " ORDER BY m.match_date DESC, p.points_awarded DESC, p.updated_at ASC"

        all_rows = rows(db.execute(base_sql, tuple(params)))

    # Group by match, then pick everyone tied at the top points_awarded
    # value for that match (handles genuine ties fairly).
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
                    "user_id":             w["uid"],
                    "user_name":           w["user_name"],
                    "user_country":        w["user_country"],
                    "predicted_home_score": w["predicted_home_score"],
                    "predicted_away_score": w["predicted_away_score"],
                    "points_awarded":       w["points_awarded"],
                }
                for w in winners
            ],
        })

    results.sort(key=lambda r: r["match_date"] or "", reverse=True)

    return {"matches": results, "total_completed_matches": len(results)}
