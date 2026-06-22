from fastapi import APIRouter, Depends, HTTPException

from ..database import get_db, row, rows
from ..deps import admin_user, current_user
from ..schemas import TeamIn, TournamentIn
from ..services.ai import tournament_winner_forecast
from ..services.points import standings
from ..services.scoring import lock_time
from ..services.util import fixture_date, round_robin_pairings

router = APIRouter(prefix="/tournaments", tags=["tournaments"])


@router.get("")
def list_tournaments(user: dict = Depends(current_user)):
    with get_db() as db:
        return rows(db.execute("SELECT * FROM tournaments ORDER BY created_at DESC"))


@router.post("")
def create_tournament(payload: TournamentIn, user: dict = Depends(admin_user)):
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO tournaments (name, sport, country, start_date, end_date) VALUES (?, ?, ?, ?, ?)",
            (payload.name, payload.sport, payload.country, payload.start_date, payload.end_date),
        )
        db.execute(
            "INSERT INTO audit_logs (actor_user_id, action, entity_type, entity_id, detail) VALUES (?, ?, ?, ?, ?)",
            (user["id"], "create", "tournament", cur.lastrowid, payload.model_dump_json()),
        )
        return row(db.execute("SELECT * FROM tournaments WHERE id = ?", (cur.lastrowid,)))


@router.post("/{tournament_id}/teams")
def add_team(tournament_id: int, payload: TeamIn, user: dict = Depends(admin_user)):
    with get_db() as db:
        cur = db.execute(
            """
            INSERT INTO teams (tournament_id, name, country, flag, ranking, home_advantage, strength_score)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (tournament_id, payload.name, payload.country, payload.flag, payload.ranking, payload.home_advantage, max(1, 100 - payload.ranking)),
        )
        return row(db.execute("SELECT * FROM teams WHERE id = ?", (cur.lastrowid,)))


@router.get("/{tournament_id}/teams")
def list_teams(tournament_id: int, user: dict = Depends(current_user)):
    with get_db() as db:
        return rows(db.execute("SELECT * FROM teams WHERE tournament_id = ? ORDER BY ranking", (tournament_id,)))


@router.post("/{tournament_id}/fixtures")
def generate_fixtures(tournament_id: int, user: dict = Depends(admin_user)):
    with get_db() as db:
        tournament = row(db.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)))
        if not tournament:
            raise HTTPException(status_code=404, detail="Tournament not found")
        team_ids = [item["id"] for item in rows(db.execute("SELECT id FROM teams WHERE tournament_id = ? ORDER BY id", (tournament_id,)))]
        if len(team_ids) < 2:
            raise HTTPException(status_code=400, detail="At least two teams are required")
        created = []
        for round_no, home, away in round_robin_pairings(team_ids):
            cur = db.execute(
                """
                INSERT INTO matches (tournament_id, game_no, sport, round, match_date, lock_at, stadium, home_team_id, away_team_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tournament_id,
                    f"G{round_no}-{len(created) + 1}",
                    tournament["sport"] or "FIFA World Cup",
                    f"Group Round {round_no}",
                    fixture_date(tournament["start_date"], round_no - 1),
                    lock_time(fixture_date(tournament["start_date"], round_no - 1)),
                    f"WorldCup Arena {round_no}",
                    home,
                    away,
                ),
            )
            created.append(cur.lastrowid)
        db.execute("UPDATE tournaments SET status = 'scheduled' WHERE id = ?", (tournament_id,))
    return {"created_matches": len(created), "match_ids": created}


@router.get("/{tournament_id}/standings")
def get_standings(tournament_id: int, user: dict = Depends(current_user)):
    with get_db() as db:
        return standings(db, tournament_id)


@router.get("/{tournament_id}/forecast")
def get_forecast(tournament_id: int, user: dict = Depends(current_user)):
    with get_db() as db:
        return tournament_winner_forecast(db, tournament_id)
