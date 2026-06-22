from fastapi import APIRouter, Depends, HTTPException

from ..database import get_db, row, rows
from ..deps import admin_user, current_user
from ..schemas import MatchIn, MatchPredictionStatus, ScoreIn
from ..services.ai import commentary, predict_match
from ..services.audit import audit
from ..services.scoring import complete_match_workflow, lock_due_predictions, lock_time
from ..services.util import to_json

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("")
def list_matches(tournament_id: int | None = None, user: dict = Depends(current_user)):
    sql = """
        SELECT m.*, ht.name AS home_team, ht.flag AS home_flag, at.name AS away_team, at.flag AS away_flag
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
    """
    params = ()
    if tournament_id:
        sql += " WHERE m.tournament_id = ?"
        params = (tournament_id,)
    sql += " ORDER BY m.match_date, m.id"
    with get_db() as db:
        lock_due_predictions(db)
        return rows(db.execute(sql, params))


@router.get("/upcoming")
def upcoming_matches(tournament_id: int | None = None, limit: int = 10, user: dict = Depends(current_user)):
    sql = """
        SELECT m.id, m.game_no, m.sport, m.match_date, m.lock_at, m.stadium, m.status, m.predictions_open,
               ht.name AS home_team, ht.flag AS home_flag, at.name AS away_team, at.flag AS away_flag
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        WHERE m.status IN ('scheduled', 'locked', 'live')
    """
    params: list = []
    if tournament_id:
        sql += " AND m.tournament_id = ?"
        params.append(tournament_id)
    sql += " ORDER BY m.match_date, m.id LIMIT ?"
    params.append(limit)
    with get_db() as db:
        # Keep the list truthful by applying the 5-minute lock rule before reading upcoming rows.
        lock_due_predictions(db)
        return rows(db.execute(sql, tuple(params)))


@router.put("/{match_id}/prediction-status")
def update_prediction_status(match_id: int, payload: MatchPredictionStatus, user: dict = Depends(admin_user)):
    with get_db() as db:
        match = row(db.execute("SELECT id, status FROM matches WHERE id = ?", (match_id,)))
        if not match:
            raise HTTPException(status_code=404, detail="Match not found")
        # When admin closes predictions → mark match as "locked" so users
        # can no longer submit/change picks and the UI reflects the correct state.
        # When admin reopens predictions → revert to "scheduled" so the match
        # appears open again. Completed matches are never touched here.
        if match["status"] not in ("completed",):
            new_status = "scheduled" if payload.predictions_open else "locked"
            db.execute(
                "UPDATE matches SET predictions_open = ?, status = ? WHERE id = ?",
                (1 if payload.predictions_open else 0, new_status, match_id)
            )
        else:
            # Completed match — only toggle the flag, never change status
            db.execute(
                "UPDATE matches SET predictions_open = ? WHERE id = ?",
                (1 if payload.predictions_open else 0, match_id)
            )
        audit(db, "update_prediction_status", "match", match_id, user["id"], payload.model_dump())
        return row(db.execute("SELECT * FROM matches WHERE id = ?", (match_id,)))


@router.post("")
def create_match(payload: MatchIn, user: dict = Depends(admin_user)):
    with get_db() as db:
        cur = db.execute(
            """
            INSERT INTO matches (tournament_id, home_team_id, away_team_id, game_no, sport, round, match_date,
                                 lock_at, stadium, result_mode, external_match_id, live_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.tournament_id,
                payload.home_team_id,
                payload.away_team_id,
                payload.game_no,
                payload.sport,
                payload.round,
                payload.match_date,
                lock_time(payload.match_date),
                payload.stadium,
                payload.result_mode,
                payload.external_match_id,
                payload.live_source,
            ),
        )
        match = row(db.execute("SELECT * FROM matches WHERE id = ?", (cur.lastrowid,)))
        audit(db, "create_match", "match", match["id"], user["id"], payload.model_dump())
        return match


@router.get("/{match_id}/predict")
def ai_predict(match_id: int, user: dict = Depends(current_user)):
    with get_db() as db:
        match = row(db.execute("SELECT * FROM matches WHERE id = ?", (match_id,)))
        if not match:
            raise HTTPException(status_code=404, detail="Match not found")
        prediction = predict_match(db, match["home_team_id"], match["away_team_id"])
        db.execute(
            "UPDATE matches SET ai_home_probability = ?, ai_away_probability = ?, ai_draw_probability = ? WHERE id = ?",
            (prediction["home_probability"], prediction["away_probability"], prediction["draw_probability"], match_id),
        )
    return prediction


@router.put("/{match_id}/score")
def update_score(match_id: int, payload: ScoreIn, user: dict = Depends(admin_user)):
    with get_db() as db:
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
        winner = loser = None
        if payload.home_score > payload.away_score:
            winner, loser = match["home_team_id"], match["away_team_id"]
        elif payload.home_score < payload.away_score:
            winner, loser = match["away_team_id"], match["home_team_id"]
        text = commentary(match["home_team"], match["away_team"], payload.home_score, payload.away_score)
        db.execute(
            """
            UPDATE matches
            SET home_score = ?, away_score = ?, status = 'completed', result_mode = ?, winner_team_id = ?, loser_team_id = ?, commentary = ?
            WHERE id = ?
            """,
            (payload.home_score, payload.away_score, payload.result_mode, winner, loser, text, match_id),
        )
        db.execute(
            "INSERT INTO match_history (match_id, tournament_id, payload) VALUES (?, ?, ?)",
            (match_id, match["tournament_id"], to_json({"home_score": payload.home_score, "away_score": payload.away_score, "commentary": text})),
        )
        audit(db, "result_update", "match", match_id, user["id"], payload.model_dump())
        complete_match_workflow(db, match_id)
        return row(db.execute("SELECT * FROM matches WHERE id = ?", (match_id,)))


@router.post("/lock-due")
def lock_due(user: dict = Depends(admin_user)):
    with get_db() as db:
        locked = lock_due_predictions(db)
        audit(db, "lock_due_predictions", "match", None, user["id"], {"locked": locked})
    return {"locked_matches": locked}
