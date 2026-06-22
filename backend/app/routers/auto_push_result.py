# ─────────────────────────────────────────────────────────────────────────────
#  NEW FILE: backend/app/routers/auto_push_result.py
#
#  PURPOSE
#  Admin-only "Auto Result Post" tab. Scans all scheduled/locked/live matches
#  in a tournament, checks football-data.org for each one on its match date,
#  and reports back which results are available to post. Nothing is written
#  to the database until the admin explicitly confirms — this is a two-step
#  "check then post" flow, never a silent auto-write.
#
#  SETUP REQUIRED
#  1. Free API key: https://www.football-data.org/client/register (no card)
#  2. Add to your .env file:
#       FOOTBALL_DATA_API_KEY=your_key_here
#     This is read via Settings (config.py), the same way as your other
#     secrets like SMTP_PASSWORD — make sure config.py has a matching
#     `football_data_api_key: str = ""` field declared on the Settings class.
#  3. pip install httpx   (if not already installed)
#
#  REGISTER THIS ROUTER in backend/app/main.py:
#       from .routers import auto_push_result
#       app.include_router(auto_push_result.router, prefix="/api")
# ─────────────────────────────────────────────────────────────────────────────

from difflib import SequenceMatcher
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException

from ..config import settings
from ..database import get_db, row, rows
from ..deps import admin_user
from ..services.audit import audit
from ..services.scoring import complete_match_workflow

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

router = APIRouter(prefix="/auto-result", tags=["auto-result"])

FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"

# FIFA World Cup competition code on football-data.org
COMPETITION_CODE = "WC"


def _name_similarity(a: str, b: str) -> float:
    """Fuzzy match team names — handles 'USA' vs 'United States' etc."""
    a, b = (a or "").lower().strip(), (b or "").lower().strip()
    aliases = {
        "usa": "united states", "us": "united states",
        "south korea": "korea republic", "ivory coast": "côte d'ivoire",
        "uk": "england", "turkiye": "turkey",
    }
    a = aliases.get(a, a)
    b = aliases.get(b, b)
    if a == b:
        return 100.0
    if a in b or b in a:
        return 90.0
    return round(SequenceMatcher(None, a, b).ratio() * 100, 1)


def _match_one_fixture(match: dict, fixtures: list[dict]) -> tuple[dict | None, float, bool]:
    """Find the best-matching football-data.org fixture for one of our matches."""
    best_fixture, best_score, best_swapped = None, 0.0, False
    for fx in fixtures:
        home_api = fx.get("homeTeam", {}).get("name", "") or ""
        away_api = fx.get("awayTeam", {}).get("name", "") or ""

        score_normal  = (_name_similarity(match["home_team"], home_api) +
                          _name_similarity(match["away_team"], away_api)) / 2
        score_swapped = (_name_similarity(match["home_team"], away_api) +
                          _name_similarity(match["away_team"], home_api)) / 2

        score = max(score_normal, score_swapped)
        if score > best_score:
            best_score = score
            best_fixture = fx
            best_swapped = score_swapped > score_normal
    return best_fixture, best_score, best_swapped


@router.get("/check/{tournament_id}")
def check_results(tournament_id: int, admin: dict = Depends(admin_user)):
    """
    STEP 1 — scan every scheduled/locked/live match in this tournament and
    check football-data.org for a result. Returns a preview list; nothing is
    written to the database here.
    """
    if not _HTTPX_AVAILABLE:
        raise HTTPException(status_code=503, detail="httpx is not installed on the server. Run: pip install httpx")
    if not settings.football_data_api_key:
        raise HTTPException(
            status_code=503,
            detail="FOOTBALL_DATA_API_KEY not configured. Sign up free at "
                   "https://www.football-data.org/client/register and set "
                   "FOOTBALL_DATA_API_KEY in your .env file.",
        )

    with get_db() as db:
        matches = rows(db.execute(
            """
            SELECT m.*, ht.name AS home_team, at.name AS away_team
            FROM matches m
            JOIN teams ht ON ht.id = m.home_team_id
            JOIN teams at ON at.id = m.away_team_id
            WHERE m.tournament_id = ? AND m.status IN ('scheduled', 'locked', 'live')
            ORDER BY m.match_date
            """,
            (tournament_id,),
        ))

    if not matches:
        return {"checked": 0, "results": [], "message": "No scheduled, locked, or live matches to check."}

    # Pull the full competition fixture list once (date-filtered) rather
    # than one API call per match — keeps us well under the free-tier
    # rate limit (10 calls/minute) even for a 104-match tournament.
    dates = [m["match_date"] for m in matches if m.get("match_date")]
    if not dates:
        return {"checked": 0, "results": [], "message": "No matches have dates set."}

    date_from = min(d[:10] for d in dates)
    date_to   = (datetime.fromisoformat(max(d[:10] for d in dates)) + timedelta(days=1)).strftime("%Y-%m-%d")

    headers = {"X-Auth-Token": settings.football_data_api_key}
    url = f"{FOOTBALL_DATA_BASE}/competitions/{COMPETITION_CODE}/matches"

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url, headers=headers, params={"dateFrom": date_from, "dateTo": date_to})
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Network error reaching football-data.org: {e}")

    if resp.status_code == 429:
        raise HTTPException(status_code=429, detail="Rate limit hit (10 calls/min on free tier). Wait a moment and try again.")
    if resp.status_code == 403:
        raise HTTPException(status_code=403, detail="API key invalid, or the World Cup competition isn't available on your plan.")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"football-data.org returned {resp.status_code}: {resp.text[:200]}")

    fixtures = resp.json().get("matches", [])

    status_map = {
        "SCHEDULED": "scheduled", "TIMED": "scheduled",
        "IN_PLAY": "live", "PAUSED": "live",
        "FINISHED": "completed", "AWARDED": "completed",
        "POSTPONED": "scheduled", "CANCELLED": "scheduled", "SUSPENDED": "live",
    }

    results = []
    for m in matches:
        best_fixture, best_score, swapped = _match_one_fixture(m, fixtures)

        if not best_fixture or best_score < 55:
            results.append({
                "match_id": m["id"], "game_no": m["game_no"], "round": m["round"],
                "home_team": m["home_team"], "away_team": m["away_team"],
                "match_date": m["match_date"], "found": False,
                "message": f"No confident match found (best similarity: {best_score:.0f}%).",
            })
            continue

        full_time = best_fixture.get("score", {}).get("fullTime", {})
        home_goals, away_goals = full_time.get("home"), full_time.get("away")
        if swapped:
            home_goals, away_goals = away_goals, home_goals

        api_status = best_fixture.get("status", "SCHEDULED")
        suggested_status = status_map.get(api_status, "scheduled")

        results.append({
            "match_id": m["id"], "game_no": m["game_no"], "round": m["round"],
            "home_team": m["home_team"], "away_team": m["away_team"],
            "match_date": m["match_date"],
            "found": home_goals is not None and away_goals is not None,
            "confidence": best_score,
            "matched_as": f"{best_fixture.get('homeTeam', {}).get('name','')} vs {best_fixture.get('awayTeam', {}).get('name','')}",
            "fixture_status": api_status,
            "suggested_status": suggested_status,
            "home_score": home_goals,
            "away_score": away_goals,
            "ready_to_post": suggested_status == "completed" and home_goals is not None and away_goals is not None,
        })

    ready_count = sum(1 for r in results if r.get("ready_to_post"))
    return {
        "checked": len(matches),
        "ready_to_post": ready_count,
        "results": results,
    }


@router.post("/post/{match_id}")
def post_result(match_id: int, home_score: int, away_score: int, admin: dict = Depends(admin_user)):
    """
    STEP 2 — admin confirms a single match result found in the check step.
    Writes the score, marks the match completed, and runs the same scoring
    workflow a manual result entry would (points, leaderboard, notifications).
    """
    with get_db() as db:
        match = row(db.execute(
            """
            SELECT m.*, ht.name AS home_team, at.name AS away_team
            FROM matches m
            JOIN teams ht ON ht.id = m.home_team_id
            JOIN teams at ON at.id = m.away_team_id
            WHERE m.id = ?
            """,
            (match_id,),
        ))
        if not match:
            raise HTTPException(status_code=404, detail="Match not found")
        if match["status"] == "completed":
            raise HTTPException(status_code=400, detail="Match is already completed.")

        winner = loser = None
        if home_score > away_score:
            winner, loser = match["home_team_id"], match["away_team_id"]
        elif away_score > home_score:
            winner, loser = match["away_team_id"], match["home_team_id"]

        db.execute(
            """
            UPDATE matches
            SET home_score = ?, away_score = ?, status = 'completed',
                result_mode = 'auto', winner_team_id = ?, loser_team_id = ?
            WHERE id = ?
            """,
            (home_score, away_score, winner, loser, match_id),
        )
        db.execute(
            "INSERT INTO match_history (match_id, tournament_id, payload) VALUES (?, ?, ?)",
            (match_id, match["tournament_id"],
             f'{{"home_score": {home_score}, "away_score": {away_score}, "source": "auto_push_result"}}'),
        )
        audit(db, "auto_result_post", "match", match_id, admin["id"],
              {"home_score": home_score, "away_score": away_score, "source": "football-data.org"})
        complete_match_workflow(db, match_id)
        return row(db.execute("SELECT * FROM matches WHERE id = ?", (match_id,)))


@router.post("/post-all/{tournament_id}")
def post_all_ready(tournament_id: int, admin: dict = Depends(admin_user)):
    """
    Convenience bulk action: re-runs the check, then posts every match that
    came back 'ready_to_post' in one go. Still requires the admin to have
    explicitly clicked a "Post All Ready Results" button — never runs on
    its own.
    """
    preview = check_results(tournament_id, admin)
    posted, failed = [], []
    for r in preview["results"]:
        if not r.get("ready_to_post"):
            continue
        try:
            post_result(r["match_id"], r["home_score"], r["away_score"], admin)
            posted.append(r["match_id"])
        except HTTPException as e:
            failed.append({"match_id": r["match_id"], "error": e.detail})
    return {"posted": posted, "failed": failed, "posted_count": len(posted)}
