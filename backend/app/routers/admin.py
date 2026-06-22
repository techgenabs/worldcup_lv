# ─────────────────────────────────────────────────────────────────────────────
#  ADD THESE ROUTES TO YOUR backend/app/routers/admin.py
#  If admin.py doesn't exist, create it and add this + register in main.py:
#      from .routers import admin
#      app.include_router(admin.router, prefix="/api")
# ─────────────────────────────────────────────────────────────────────────────

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from ..database import get_db, row, rows
from ..deps import admin_user, current_user
from ..security import hash_password

router = APIRouter(prefix="/admin", tags=["admin"])


# ── GET all users ──────────────────────────────────────────────────────────
@router.get("/users")
def list_users(admin: dict = Depends(admin_user)):
    with get_db() as db:
        return rows(db.execute(
            """
            SELECT u.*, 
                   (SELECT COUNT(*) FROM predictions p WHERE p.user_id = u.id) AS predictions_count
            FROM users u
            ORDER BY u.created_at DESC
            """
        ))


# ── Toggle user active/inactive ───────────────────────────────────────────
@router.put("/users/{user_id}/toggle-active")
def toggle_active(user_id: int, admin: dict = Depends(admin_user)):
    with get_db() as db:
        user = row(db.execute("SELECT id, is_active, name FROM users WHERE id = ?", (user_id,)))
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        new_status = 0 if user["is_active"] else 1
        db.execute("UPDATE users SET is_active = ? WHERE id = ?", (new_status, user_id))
        return {"id": user_id, "is_active": bool(new_status), "name": user["name"]}


# ── Delete user + all their predictions ───────────────────────────────────
@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin: dict = Depends(admin_user)):
    with get_db() as db:
        user = row(db.execute("SELECT id, name, role FROM users WHERE id = ?", (user_id,)))
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if user["role"] == "admin":
            raise HTTPException(status_code=403, detail="Cannot delete an admin user")
        if user_id == admin["id"]:
            raise HTTPException(status_code=403, detail="Cannot delete yourself")
        # Cascade: delete predictions, leaderboard entries, then user
        db.execute("DELETE FROM predictions WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM leaderboards WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return {"status": "deleted", "user_id": user_id, "name": user["name"]}


# ── Reset user password ───────────────────────────────────────────────────
class PasswordReset(BaseModel):
    password: str

@router.put("/users/{user_id}/reset-password")
def reset_password(user_id: int, payload: PasswordReset, admin: dict = Depends(admin_user)):
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    with get_db() as db:
        user = row(db.execute("SELECT id, name FROM users WHERE id = ?", (user_id,)))
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        db.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(payload.password), user_id)
        )
        return {"status": "password_reset", "user_id": user_id, "name": user["name"]}


# ── DATA RESET ENDPOINTS ──────────────────────────────────────────────────

@router.delete("/reset/predictions")
def reset_predictions(admin: dict = Depends(admin_user)):
    """Delete all predictions and reset leaderboard points to 0."""
    with get_db() as db:
        count = db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        db.execute("DELETE FROM predictions")
        db.execute("UPDATE leaderboards SET total_points=0, exact_matches=0, winner_count=0, predictions_count=0, accuracy=0")
        return {"message": f"Cleared {count} predictions and reset leaderboard.", "deleted": count}


@router.delete("/reset/matches")
def reset_matches(admin: dict = Depends(admin_user)):
    """Delete all matches, teams, predictions and leaderboard entries."""
    with get_db() as db:
        pred_count  = db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        match_count = db.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        team_count  = db.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
        db.execute("DELETE FROM predictions")
        db.execute("DELETE FROM leaderboards")
        db.execute("DELETE FROM match_history")
        db.execute("DELETE FROM matches")
        db.execute("DELETE FROM teams")
        return {
            "message": f"Cleared {match_count} matches, {team_count} teams, {pred_count} predictions.",
            "matches": match_count, "teams": team_count, "predictions": pred_count,
        }


@router.delete("/reset/tournaments")
def reset_tournaments(admin: dict = Depends(admin_user)):
    """Delete everything except users."""
    with get_db() as db:
        counts = {
            "tournaments":  db.execute("SELECT COUNT(*) FROM tournaments").fetchone()[0],
            "matches":      db.execute("SELECT COUNT(*) FROM matches").fetchone()[0],
            "predictions":  db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0],
        }
        db.execute("DELETE FROM predictions")
        db.execute("DELETE FROM leaderboards")
        db.execute("DELETE FROM match_history")
        db.execute("DELETE FROM matches")
        db.execute("DELETE FROM teams")
        db.execute("DELETE FROM tournaments")
        return {"message": "All tournament data cleared. Users kept.", **counts}


@router.delete("/reset/leaderboard")
def reset_leaderboard(admin: dict = Depends(admin_user)):
    """Reset all leaderboard points to 0 without touching predictions."""
    with get_db() as db:
        count = db.execute("SELECT COUNT(*) FROM leaderboards").fetchone()[0]
        db.execute("UPDATE leaderboards SET total_points=0, exact_matches=0, winner_count=0, accuracy=0, rank=NULL, badges=''")
        return {"message": f"Reset {count} leaderboard entries.", "reset": count}


# ── Analytics ──────────────────────────────────────────────────────────────
@router.get("/analytics")
def analytics(admin: dict = Depends(admin_user)):
    with get_db() as db:
        return {
            "totals": {
                "users":             db.execute("SELECT COUNT(*) FROM users WHERE role='user'").fetchone()[0],
                "matches":           db.execute("SELECT COUNT(*) FROM matches").fetchone()[0],
                "predictions":       db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0],
                "completed_matches": db.execute("SELECT COUNT(*) FROM matches WHERE status='completed'").fetchone()[0],
            }
        }


# ── IMPORT FIFA WORLD CUP 2026 FIXTURES ───────────────────────────────────────
@router.post("/import-worldcup-fixtures/{tournament_id}")
def import_worldcup_fixtures(tournament_id: int, admin: dict = Depends(admin_user)):
    """
    Import the REAL FIFA World Cup 2026 fixtures: all 48 group-stage matches
    across the 12 confirmed groups (A-L), plus the knockout-stage placeholder
    slots (Round of 32 through Final). Skips matches that already exist
    (matched by game_no). Kickoff times are stored as UTC.

    Each knockout slot still uses "TBD" as a human-readable label in the
    table below, but every slot gets a UNIQUE team name behind the scenes
    (e.g. "TBD (R32-3 Home)") so two different empty slots never collide
    into the same team row (teams.name has a UNIQUE(tournament_id, name)
    constraint), and a match never ends up with home_team_id == away_team_id.
    Once a knockout matchup is decided, an admin can edit the match teams
    directly via Manage Matches.
    """
    from ..services.scoring import lock_time

    # game_no, round/group, date(UTC), stadium, home, away
    FIXTURES = [
        # ============================ GROUP STAGE (48 matches, Groups A-L) ============================
        # Group A: Mexico, South Korea, Czechia, South Africa
        ("G01","Group A","2026-06-11T18:00:00Z","Estadio Azteca, Mexico City",                 "Mexico","South Africa"),
        ("G02","Group A","2026-06-11T22:00:00Z","Estadio Akron, Guadalajara",                  "South Korea","Czechia"),
        ("G03","Group A","2026-06-18T16:00:00Z","Mercedes-Benz Stadium, Atlanta",               "Czechia","South Africa"),
        ("G04","Group A","2026-06-19T02:00:00Z","Estadio Akron, Guadalajara",                  "Mexico","South Korea"),
        ("G05","Group A","2026-06-25T01:00:00Z","Estadio Azteca, Mexico City",                 "Czechia","Mexico"),
        ("G06","Group A","2026-06-25T01:00:00Z","Estadio BBVA, Monterrey",                     "South Africa","South Korea"),
        # Group B: Canada, Bosnia and Herzegovina, Qatar, Switzerland
        ("G07","Group B","2026-06-12T17:00:00Z","BC Place, Vancouver",                         "Canada","Bosnia and Herzegovina"),
        ("G08","Group B","2026-06-13T20:00:00Z","Estadio Azteca, Mexico City",                 "Qatar","Switzerland"),
        ("G09","Group B","2026-06-18T22:00:00Z","SoFi Stadium, Los Angeles",                   "Switzerland","Bosnia and Herzegovina"),
        ("G10","Group B","2026-06-19T01:00:00Z","BC Place, Vancouver",                         "Canada","Qatar"),
        ("G11","Group B","2026-06-24T19:00:00Z","BC Place, Vancouver",                         "Switzerland","Canada"),
        ("G12","Group B","2026-06-24T19:00:00Z","Lumen Field, Seattle",                        "Bosnia and Herzegovina","Qatar"),
        # Group C: Brazil, Morocco, Haiti, Scotland
        ("G13","Group C","2026-06-13T20:00:00Z","Hard Rock Stadium, Miami",                    "Brazil","Morocco"),
        ("G14","Group C","2026-06-13T20:00:00Z","Gillette Stadium, Boston",                    "Haiti","Scotland"),
        ("G15","Group C","2026-06-19T22:00:00Z","Gillette Stadium, Boston",                    "Scotland","Morocco"),
        ("G16","Group C","2026-06-20T01:00:00Z","Lincoln Financial Field, Philadelphia",        "Brazil","Haiti"),
        ("G17","Group C","2026-06-24T22:00:00Z","Hard Rock Stadium, Miami",                    "Scotland","Brazil"),
        ("G18","Group C","2026-06-24T22:00:00Z","Mercedes-Benz Stadium, Atlanta",               "Morocco","Haiti"),
        # Group D: USA, Paraguay, Australia, Turkiye
        ("G19","Group D","2026-06-12T19:00:00Z","SoFi Stadium, Los Angeles",                   "USA","Paraguay"),
        ("G20","Group D","2026-06-13T20:00:00Z","Lumen Field, Seattle",                        "Australia","Turkiye"),
        ("G21","Group D","2026-06-19T19:00:00Z","Lumen Field, Seattle",                        "USA","Australia"),
        ("G22","Group D","2026-06-20T04:00:00Z","Levi's Stadium, San Francisco Bay Area",       "Turkiye","Paraguay"),
        ("G23","Group D","2026-06-26T02:00:00Z","SoFi Stadium, Los Angeles",                   "Turkiye","USA"),
        ("G24","Group D","2026-06-26T02:00:00Z","Levi's Stadium, San Francisco Bay Area",       "Paraguay","Australia"),
        # Group E: Germany, Curacao, Ivory Coast, Ecuador
        ("G25","Group E","2026-06-14T22:00:00Z","BMO Field, Toronto",                          "Germany","Curacao"),
        ("G26","Group E","2026-06-14T22:00:00Z","Arrowhead Stadium, Kansas City",               "Ivory Coast","Ecuador"),
        ("G27","Group E","2026-06-20T20:00:00Z","BMO Field, Toronto",                          "Germany","Ivory Coast"),
        ("G28","Group E","2026-06-21T00:00:00Z","Arrowhead Stadium, Kansas City",               "Ecuador","Curacao"),
        ("G29","Group E","2026-06-25T20:00:00Z","MetLife Stadium, New York/New Jersey",         "Ecuador","Germany"),
        ("G30","Group E","2026-06-25T20:00:00Z","Lincoln Financial Field, Philadelphia",        "Curacao","Ivory Coast"),
        # Group F: Netherlands, Japan, Sweden, Tunisia
        ("G31","Group F","2026-06-15T00:00:00Z","NRG Stadium, Houston",                        "Netherlands","Japan"),
        ("G32","Group F","2026-06-15T00:00:00Z","Estadio BBVA, Monterrey",                     "Sweden","Tunisia"),
        ("G33","Group F","2026-06-20T17:00:00Z","NRG Stadium, Houston",                        "Netherlands","Sweden"),
        ("G34","Group F","2026-06-21T04:00:00Z","Estadio BBVA, Monterrey",                     "Tunisia","Japan"),
        ("G35","Group F","2026-06-25T23:00:00Z","AT&T Stadium, Dallas",                        "Japan","Sweden"),
        ("G36","Group F","2026-06-25T23:00:00Z","Arrowhead Stadium, Kansas City",               "Tunisia","Netherlands"),
        # Group G: Belgium, Egypt, Iran, New Zealand
        ("G37","Group G","2026-06-15T00:00:00Z","SoFi Stadium, Los Angeles",                   "Iran","New Zealand"),
        ("G38","Group G","2026-06-16T03:00:00Z","Levi's Stadium, San Francisco Bay Area",       "Belgium","Egypt"),
        ("G39","Group G","2026-06-21T19:00:00Z","SoFi Stadium, Los Angeles",                   "Belgium","Iran"),
        ("G40","Group G","2026-06-22T01:00:00Z","BC Place, Vancouver",                         "New Zealand","Egypt"),
        ("G41","Group G","2026-06-27T03:00:00Z","Lumen Field, Seattle",                        "Egypt","Iran"),
        ("G42","Group G","2026-06-27T03:00:00Z","BC Place, Vancouver",                         "New Zealand","Belgium"),
        # Group H: Spain, Cape Verde, Saudi Arabia, Uruguay
        ("G43","Group H","2026-06-16T00:00:00Z","Mercedes-Benz Stadium, Atlanta",               "Spain","Cape Verde"),
        ("G44","Group H","2026-06-16T00:00:00Z","Estadio Akron, Guadalajara",                  "Saudi Arabia","Uruguay"),
        ("G45","Group H","2026-06-21T17:00:00Z","Mercedes-Benz Stadium, Atlanta",               "Spain","Saudi Arabia"),
        ("G46","Group H","2026-06-21T23:00:00Z","Hard Rock Stadium, Miami",                    "Uruguay","Cape Verde"),
        ("G47","Group H","2026-06-27T01:00:00Z","NRG Stadium, Houston",                        "Cape Verde","Saudi Arabia"),
        ("G48","Group H","2026-06-27T01:00:00Z","Estadio Akron, Guadalajara",                  "Uruguay","Spain"),
        # Group I: France, Senegal, Iraq, Norway
        ("G49","Group I","2026-06-16T18:00:00Z","MetLife Stadium, New York/New Jersey",         "France","Senegal"),
        ("G50","Group I","2026-06-17T03:00:00Z","Levi's Stadium, San Francisco Bay Area",       "Iraq","Norway"),
        ("G51","Group I","2026-06-22T21:00:00Z","Lincoln Financial Field, Philadelphia",        "France","Iraq"),
        ("G52","Group I","2026-06-23T00:00:00Z","MetLife Stadium, New York/New Jersey",         "Norway","Senegal"),
        ("G53","Group I","2026-06-26T19:00:00Z","Gillette Stadium, Boston",                    "Norway","France"),
        ("G54","Group I","2026-06-26T19:00:00Z","BMO Field, Toronto",                          "Senegal","Iraq"),
        # Group J: Argentina, Algeria, Austria, Jordan
        ("G55","Group J","2026-06-16T22:00:00Z","Arrowhead Stadium, Kansas City",               "Argentina","Algeria"),
        ("G56","Group J","2026-06-17T04:00:00Z","Levi's Stadium, San Francisco Bay Area",       "Austria","Jordan"),
        ("G57","Group J","2026-06-22T17:00:00Z","AT&T Stadium, Dallas",                        "Argentina","Austria"),
        ("G58","Group J","2026-06-22T23:00:00Z","Levi's Stadium, San Francisco Bay Area",       "Jordan","Algeria"),
        ("G59","Group J","2026-06-28T02:00:00Z","Arrowhead Stadium, Kansas City",               "Algeria","Austria"),
        ("G60","Group J","2026-06-28T02:00:00Z","AT&T Stadium, Dallas",                        "Jordan","Argentina"),
        # Group K: Portugal, DR Congo, Uzbekistan, Colombia
        ("G61","Group K","2026-06-17T17:00:00Z","NRG Stadium, Houston",                        "Portugal","Congo DR"),
        ("G62","Group K","2026-06-18T02:00:00Z","Estadio Azteca, Mexico City",                 "Uzbekistan","Colombia"),
        ("G63","Group K","2026-06-23T17:00:00Z","NRG Stadium, Houston",                        "Portugal","Uzbekistan"),
        ("G64","Group K","2026-06-24T02:00:00Z","Estadio Akron, Guadalajara",                  "Colombia","Congo DR"),
        ("G65","Group K","2026-06-27T23:30:00Z","Hard Rock Stadium, Miami",                    "Colombia","Portugal"),
        ("G66","Group K","2026-06-27T23:30:00Z","Mercedes-Benz Stadium, Atlanta",               "Congo DR","Uzbekistan"),
        # Group L: England, Croatia, Ghana, Panama
        ("G67","Group L","2026-06-17T20:00:00Z","AT&T Stadium, Dallas",                        "England","Croatia"),
        ("G68","Group L","2026-06-17T23:00:00Z","BMO Field, Toronto",                          "Ghana","Panama"),
        ("G69","Group L","2026-06-23T20:00:00Z","Gillette Stadium, Boston",                    "England","Ghana"),
        ("G70","Group L","2026-06-23T23:00:00Z","BMO Field, Toronto",                          "Panama","Croatia"),
        ("G71","Group L","2026-06-28T02:00:00Z","Lincoln Financial Field, Philadelphia",        "Croatia","Ghana"),
        ("G72","Group L","2026-06-28T02:00:00Z","MetLife Stadium, New York/New Jersey",         "Panama","England"),

        # ============================ ROUND OF 32 (16 matches, placeholders) ============================
        ("R32-1", "Round of 32","2026-06-28T22:00:00Z","MetLife Stadium, New York/NJ",          "TBD","TBD"),
        ("R32-2", "Round of 32","2026-06-29T01:00:00Z","AT&T Stadium, Dallas",                  "TBD","TBD"),
        ("R32-3", "Round of 32","2026-06-29T19:00:00Z","Rose Bowl, Los Angeles",                "TBD","TBD"),
        ("R32-4", "Round of 32","2026-06-29T22:00:00Z","NRG Stadium, Houston",                  "TBD","TBD"),
        ("R32-5", "Round of 32","2026-06-30T01:00:00Z","BC Place Stadium, Vancouver",           "TBD","TBD"),
        ("R32-6", "Round of 32","2026-06-30T19:00:00Z","Hard Rock Stadium, Miami",              "TBD","TBD"),
        ("R32-7", "Round of 32","2026-06-30T22:00:00Z","SoFi Stadium, Los Angeles",             "TBD","TBD"),
        ("R32-8", "Round of 32","2026-07-01T01:00:00Z","Lincoln Financial Field, Philadelphia", "TBD","TBD"),
        ("R32-9", "Round of 32","2026-07-01T19:00:00Z","Estadio Azteca, Mexico City",           "TBD","TBD"),
        ("R32-10","Round of 32","2026-07-01T22:00:00Z","Arrowhead Stadium, Kansas City",        "TBD","TBD"),
        ("R32-11","Round of 32","2026-07-02T01:00:00Z","Gillette Stadium, Boston",              "TBD","TBD"),
        ("R32-12","Round of 32","2026-07-02T19:00:00Z","Levi's Stadium, San Francisco",         "TBD","TBD"),
        ("R32-13","Round of 32","2026-07-02T22:00:00Z","Estadio BBVA, Monterrey",               "TBD","TBD"),
        ("R32-14","Round of 32","2026-07-03T01:00:00Z","AT&T Stadium, Dallas",                  "TBD","TBD"),
        ("R32-15","Round of 32","2026-07-03T19:00:00Z","MetLife Stadium, New York/NJ",          "TBD","TBD"),
        ("R32-16","Round of 32","2026-07-03T22:00:00Z","Rose Bowl, Los Angeles",                "TBD","TBD"),

        # ============================ ROUND OF 16 (8 matches) ============================
        ("R16-1","Round of 16","2026-07-04T17:00:00Z","NRG Stadium, Houston",                   "TBD","TBD"),
        ("R16-2","Round of 16","2026-07-04T21:00:00Z","Lincoln Financial Field, Philadelphia",  "TBD","TBD"),
        ("R16-3","Round of 16","2026-07-05T20:00:00Z","MetLife Stadium, New York/NJ",           "TBD","TBD"),
        ("R16-4","Round of 16","2026-07-06T00:00:00Z","Estadio Azteca, Mexico City",            "TBD","TBD"),
        ("R16-5","Round of 16","2026-07-06T19:00:00Z","AT&T Stadium, Dallas",                   "TBD","TBD"),
        ("R16-6","Round of 16","2026-07-07T00:00:00Z","Lumen Field, Seattle",                   "TBD","TBD"),
        ("R16-7","Round of 16","2026-07-07T16:00:00Z","Mercedes-Benz Stadium, Atlanta",         "TBD","TBD"),
        ("R16-8","Round of 16","2026-07-07T20:00:00Z","BC Place Stadium, Vancouver",            "TBD","TBD"),

        # ============================ QUARTER-FINALS (4 matches) ============================
        ("QF-1","Quarter-Final","2026-07-09T20:00:00Z","Gillette Stadium, Boston",              "TBD","TBD"),
        ("QF-2","Quarter-Final","2026-07-10T19:00:00Z","SoFi Stadium, Los Angeles",              "TBD","TBD"),
        ("QF-3","Quarter-Final","2026-07-11T21:00:00Z","Hard Rock Stadium, Miami",               "TBD","TBD"),
        ("QF-4","Quarter-Final","2026-07-12T01:00:00Z","Arrowhead Stadium, Kansas City",         "TBD","TBD"),

        # ============================ SEMI-FINALS (2 matches) ============================
        ("SF-1","Semi-Final","2026-07-14T19:00:00Z","AT&T Stadium, Dallas",                     "TBD","TBD"),
        ("SF-2","Semi-Final","2026-07-15T19:00:00Z","Mercedes-Benz Stadium, Atlanta",            "TBD","TBD"),

        # ============================ 3RD PLACE & FINAL ============================
        ("3P-1","3rd Place","2026-07-18T21:00:00Z","Hard Rock Stadium, Miami",                  "TBD","TBD"),
        ("FINAL","Final","2026-07-19T19:00:00Z","MetLife Stadium, New York/New Jersey",          "TBD","TBD"),
    ]

    KNOWN_FLAGS = {
        "Mexico":"\U0001F1F2\U0001F1FD","South Korea":"\U0001F1F0\U0001F1F7","Czechia":"\U0001F1E8\U0001F1FF","South Africa":"\U0001F1FF\U0001F1E6",
        "Canada":"\U0001F1E8\U0001F1E6","Bosnia and Herzegovina":"\U0001F1E7\U0001F1E6","Qatar":"\U0001F1F6\U0001F1E6","Switzerland":"\U0001F1E8\U0001F1ED",
        "Brazil":"\U0001F1E7\U0001F1F7","Morocco":"\U0001F1F2\U0001F1E6","Haiti":"\U0001F1ED\U0001F1F9","Scotland":"\U0001F3F4",
        "USA":"\U0001F1FA\U0001F1F8","Paraguay":"\U0001F1F5\U0001F1FE","Australia":"\U0001F1E6\U0001F1FA","Turkiye":"\U0001F1F9\U0001F1F7",
        "Germany":"\U0001F1E9\U0001F1EA","Curacao":"\U0001F1E8\U0001F1FC","Ivory Coast":"\U0001F1E8\U0001F1EE","Ecuador":"\U0001F1EA\U0001F1E8",
        "Netherlands":"\U0001F1F3\U0001F1F1","Japan":"\U0001F1EF\U0001F1F5","Sweden":"\U0001F1F8\U0001F1EA","Tunisia":"\U0001F1F9\U0001F1F3",
        "Belgium":"\U0001F1E7\U0001F1EA","Egypt":"\U0001F1EA\U0001F1EC","Iran":"\U0001F1EE\U0001F1F7","New Zealand":"\U0001F1F3\U0001F1FF",
        "Spain":"\U0001F1EA\U0001F1F8","Cape Verde":"\U0001F1E8\U0001F1FB","Saudi Arabia":"\U0001F1F8\U0001F1E6","Uruguay":"\U0001F1FA\U0001F1FE",
        "France":"\U0001F1EB\U0001F1F7","Senegal":"\U0001F1F8\U0001F1F3","Iraq":"\U0001F1EE\U0001F1F6","Norway":"\U0001F1F3\U0001F1F4",
        "Argentina":"\U0001F1E6\U0001F1F7","Algeria":"\U0001F1E9\U0001F1FF","Austria":"\U0001F1E6\U0001F1F9","Jordan":"\U0001F1EF\U0001F1F4",
        "Portugal":"\U0001F1F5\U0001F1F9","Congo DR":"\U0001F1E8\U0001F1E9","Uzbekistan":"\U0001F1FA\U0001F1FF","Colombia":"\U0001F1E8\U0001F1F4",
        "England":"\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F","Croatia":"\U0001F1ED\U0001F1F7","Ghana":"\U0001F1EC\U0001F1ED","Panama":"\U0001F1F5\U0001F1E6",
    }

    with get_db() as db:
        tournament = row(db.execute("SELECT id FROM tournaments WHERE id = ?", (tournament_id,)))
        if not tournament:
            raise HTTPException(status_code=404, detail="Tournament not found")

        def get_or_create_team(name: str) -> int:
            """Look up a team by (tournament_id, name); create it if missing."""
            existing = row(db.execute(
                "SELECT id FROM teams WHERE tournament_id=? AND name=?",
                (tournament_id, name)
            ))
            if existing:
                return existing["id"]
            cur = db.execute(
                "INSERT INTO teams (tournament_id, name, country, flag, ranking, strength_score) VALUES (?,?,?,?,50,50)",
                (tournament_id, name, name, KNOWN_FLAGS.get(name, "\U0001F3C6"))
            )
            return cur.lastrowid

        imported = 0
        skipped  = 0
        errors   = []

        for game_no, round_name, date, stadium, home, away in FIXTURES:
            # Skip if this exact game_no already exists
            exists = row(db.execute(
                "SELECT id FROM matches WHERE tournament_id=? AND game_no=?",
                (tournament_id, game_no)
            ))
            if exists:
                skipped += 1
                continue

            # Make placeholder names unique per slot. A literal "TBD" would
            # collide across many different empty knockout slots (teams.name
            # has a UNIQUE(tournament_id, name) constraint), and could even
            # make home == away for the same match. Give each unresolved
            # side its own unique label tied to the game_no instead.
            home_name = home if home != "TBD" else f"TBD ({game_no} Home)"
            away_name = away if away != "TBD" else f"TBD ({game_no} Away)"

            try:
                home_id = get_or_create_team(home_name)
                away_id = get_or_create_team(away_name)

                if home_id == away_id:
                    errors.append(f"{game_no}: home and away resolved to the same team, skipped")
                    skipped += 1
                    continue

                # ALSO skip if a different game_no already has this exact
                # home/away/date combo — guards against duplicates created
                # by a previous import that used different game_no values
                # for the same real fixture (e.g. across schema revisions).
                same_fixture = row(db.execute(
                    """
                    SELECT id FROM matches
                    WHERE tournament_id=? AND home_team_id=? AND away_team_id=? AND match_date=?
                    """,
                    (tournament_id, home_id, away_id, date)
                ))
                if same_fixture:
                    skipped += 1
                    continue

                db.execute(
                    """
                    INSERT INTO matches
                        (tournament_id, game_no, sport, round, match_date, lock_at,
                         stadium, home_team_id, away_team_id, status, predictions_open)
                    VALUES (?,?,?,?,?,?,?,?,?,'scheduled',0)
                    """,
                    (tournament_id, game_no, "FIFA World Cup", round_name,
                     date, lock_time(date), stadium, home_id, away_id)
                )
                imported += 1
            except Exception as e:
                errors.append(f"{game_no}: {e}")
                skipped += 1

    return {
        "imported": imported,
        "skipped":  skipped,
        "total":    imported + skipped,
        "errors":   errors,
        "source":   "FIFA World Cup 2026 Official Fixtures (48 Group Stage + Knockout Bracket)",
    }


@router.post("/upload-schedule/{tournament_id}")
async def upload_schedule(
    tournament_id: int,
    file: UploadFile = File(...),
    admin: dict = Depends(admin_user),
):
    """
    Upload an Excel file with columns:
    game_no, team_a, team_b, venue, match_date
    Optional: sport, round
    """
    from ..services.scoring import lock_time
    import io

    try:
        import openpyxl
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl not installed. Run: pip install openpyxl")

    content = await file.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content))
        ws = wb.active
        headers = [str(c.value).strip().lower() if c.value else "" for c in next(ws.iter_rows(min_row=1, max_row=1))]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read Excel file: {e}")

    required = {"team_a", "team_b", "match_date"}
    missing  = required - set(headers)
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing columns: {missing}. Required: game_no, team_a, team_b, venue, match_date")

    def col(row, name):
        try:
            idx = headers.index(name)
            return str(row[idx].value).strip() if row[idx].value is not None else ""
        except (ValueError, IndexError):
            return ""

    imported = 0
    skipped  = 0
    errors   = []

    with get_db() as db:
        tournament = row(db.execute("SELECT id FROM tournaments WHERE id=?", (tournament_id,)))
        if not tournament:
            raise HTTPException(status_code=404, detail="Tournament not found")

        def get_or_create_team(name: str) -> int:
            if not name:
                raise ValueError("Team name is empty")
            existing = row(db.execute("SELECT id FROM teams WHERE tournament_id=? AND name=?", (tournament_id, name)))
            if existing:
                return existing["id"]
            cur = db.execute(
                "INSERT INTO teams (tournament_id, name, country, flag, ranking, strength_score) VALUES (?,?,?,?,50,50)",
                (tournament_id, name, name, "🏆")
            )
            return cur.lastrowid

        for i, row_data in enumerate(ws.iter_rows(min_row=2), start=2):
            try:
                team_a    = col(row_data, "team_a")
                team_b    = col(row_data, "team_b")
                date_val  = col(row_data, "match_date")
                game_no   = col(row_data, "game_no")   or f"G{i}"
                venue     = col(row_data, "venue")      or "TBD"
                sport     = col(row_data, "sport")      or "FIFA World Cup"
                round_name= col(row_data, "round")      or "Group Stage"

                if not team_a or not team_b or not date_val:
                    skipped += 1
                    continue

                exists = row(db.execute(
                    "SELECT id FROM matches WHERE tournament_id=? AND game_no=?",
                    (tournament_id, game_no)
                ))
                if exists:
                    skipped += 1
                    continue

                home_id = get_or_create_team(team_a)
                away_id = get_or_create_team(team_b)
                db.execute(
                    """
                    INSERT INTO matches
                        (tournament_id, game_no, sport, round, match_date, lock_at,
                         stadium, home_team_id, away_team_id, status, predictions_open)
                    VALUES (?,?,?,?,?,?,?,?,?,'scheduled',0)
                    """,
                    (tournament_id, game_no, sport, round_name,
                     date_val, lock_time(date_val), venue, home_id, away_id)
                )
                imported += 1
            except Exception as e:
                errors.append(f"Row {i}: {e}")

    return {"imported": imported, "skipped": skipped, "errors": errors}


# ── GENERATE EXCEL REPORTS ────────────────────────────────────────────────────
@router.post("/exports/{tournament_id}")
def generate_exports(tournament_id: int, admin: dict = Depends(admin_user)):
    """Generate Excel reports for a tournament and save to export directory."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl not installed. Run: pip install openpyxl")

    with get_db() as db:
        # Predictions report
        preds = rows(db.execute(
            """
            SELECT u.name AS player, m.game_no, ht.name AS home_team, at.name AS away_team,
                   p.predicted_home_score, p.predicted_away_score,
                   m.home_score, m.away_score, m.status,
                   p.points_awarded, p.scoring_reason, p.created_at
            FROM predictions p
            JOIN users u ON u.id = p.user_id
            JOIN matches m ON m.id = p.match_id
            JOIN teams ht ON ht.id = m.home_team_id
            JOIN teams at ON at.id = m.away_team_id
            WHERE m.tournament_id = ?
            ORDER BY m.match_date, u.name
            """,
            (tournament_id,)
        ))
        # Leaderboard
        lb = rows(db.execute(
            """
            SELECT l.rank, u.name, u.country, l.total_points AS points,
                   l.exact_matches, l.winner_count, l.predictions_count, l.accuracy, l.badges
            FROM leaderboards l
            JOIN users u ON u.id = l.user_id
            ORDER BY l.rank
            """
        ))

    export_dir = settings.export_dir
    export_dir.mkdir(parents=True, exist_ok=True)

    files = {}

    # Predictions file
    wb1 = openpyxl.Workbook()
    ws1 = wb1.active
    ws1.title = "Predictions"
    if preds:
        ws1.append(list(preds[0].keys()))
        for p in preds:
            ws1.append(list(p.values()))
    path1 = export_dir / f"predictions_{tournament_id}.xlsx"
    wb1.save(path1)
    files["predictions"] = str(path1)

    # Leaderboard file
    wb2 = openpyxl.Workbook()
    ws2 = wb2.active
    ws2.title = "Leaderboard"
    if lb:
        ws2.append(list(lb[0].keys()))
        for l in lb:
            ws2.append(list(l.values()))
    path2 = export_dir / f"leaderboard_{tournament_id}.xlsx"
    wb2.save(path2)
    files["leaderboard"] = str(path2)

    return {"status": "generated", **files}


# ── EMAIL REPORTS ─────────────────────────────────────────────────────────────
@router.post("/email-reports")
def email_reports(payload: dict, admin: dict = Depends(admin_user)):
    """Send a general email/report notification to all or selected users."""
    from ..services.emailer import send_email, queue_notification

    subject = payload.get("subject") or "WorldCup 2026 update"
    message = payload.get("message") or "There's an update on WorldCup 2026."

    with get_db() as db:
        if payload.get("select_all"):
            recipients = rows(db.execute(
                "SELECT id, name, email FROM users WHERE role='user' AND is_active=1 AND email IS NOT NULL"
            ))
        else:
            user_ids = payload.get("user_ids", [])
            if not user_ids:
                return {"sent": 0, "skipped": 0, "failed": 0, "recipients": "none"}
            placeholders = ",".join("?" * len(user_ids))
            recipients = rows(db.execute(
                f"SELECT id, name, email FROM users WHERE id IN ({placeholders}) AND email IS NOT NULL",
                user_ids
            ))

        sent, skipped, failed = 0, 0, 0
        for r in recipients:
            try:
                status = send_email(r["email"], subject, message)  # actually sends via SMTP
                queue_notification(db, r["email"], subject, message, r["id"])  # logs to notifications table
                if status == "sent":
                    sent += 1
                else:
                    skipped += 1  # email sending is disabled (ENABLE_EMAIL=false) or attachment missing
            except Exception:
                failed += 1

    return {
        "sent":       sent,
        "skipped":    skipped,
        "failed":     failed,
        "recipients": ", ".join(r["email"] for r in recipients) or "none",
    }


# ── MATCH KICKOFF REMINDER — admin-triggered, all or selected users ───────────
@router.post("/notify/kickoff/{match_id}")
def notify_kickoff(match_id: int, payload: dict, admin: dict = Depends(admin_user)):
    """
    Sends a 'match is about to kick off' reminder for one match, to all
    active users or a selected subset. Admin triggers this manually (e.g.
    from Manage Matches) — nothing fires automatically on a timer.
    """
    from ..services.emailer import send_email, queue_notification

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

        if payload.get("select_all"):
            recipients = rows(db.execute(
                "SELECT id, name, email FROM users WHERE role='user' AND is_active=1 AND email IS NOT NULL"
            ))
        else:
            user_ids = payload.get("user_ids", [])
            if not user_ids:
                return {"sent": 0, "skipped": 0, "failed": 0, "recipients": "none"}
            placeholders = ",".join("?" * len(user_ids))
            recipients = rows(db.execute(
                f"SELECT id, name, email FROM users WHERE id IN ({placeholders}) AND email IS NOT NULL",
                user_ids
            ))

        subject = f"Kickoff soon: {match['home_team']} vs {match['away_team']}"
        body = (
            f"{match['home_team']} vs {match['away_team']} ({match.get('round','')}) "
            f"is about to kick off at {match.get('match_date','')}.\n\n"
            f"Venue: {match.get('stadium','')}\n"
            f"Make sure your prediction is in before predictions close!"
        )

        sent, skipped, failed = 0, 0, 0
        for r in recipients:
            try:
                status = send_email(r["email"], subject, body)
                queue_notification(db, r["email"], subject, body, r["id"])
                if status == "sent":
                    sent += 1
                else:
                    skipped += 1
            except Exception:
                failed += 1

    return {"sent": sent, "skipped": skipped, "failed": failed, "recipients": ", ".join(r["email"] for r in recipients) or "none"}


# ── MATCH RESULT NOTIFICATION — admin-triggered, all or selected users ────────
@router.post("/notify/result/{match_id}")
def notify_result(match_id: int, payload: dict, admin: dict = Depends(admin_user)):
    """
    Sends a 'result is in' notification for one completed match, to all
    active users or a selected subset.
    """
    from ..services.emailer import send_email, queue_notification

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
        if match["status"] != "completed":
            raise HTTPException(status_code=400, detail="Match result hasn't been posted yet.")

        if payload.get("select_all"):
            recipients = rows(db.execute(
                "SELECT id, name, email FROM users WHERE role='user' AND is_active=1 AND email IS NOT NULL"
            ))
        else:
            user_ids = payload.get("user_ids", [])
            if not user_ids:
                return {"sent": 0, "failed": 0, "recipients": "none"}
            placeholders = ",".join("?" * len(user_ids))
            recipients = rows(db.execute(
                f"SELECT id, name, email FROM users WHERE id IN ({placeholders}) AND email IS NOT NULL",
                user_ids
            ))

        subject = f"Result: {match['home_team']} {match['home_score']}-{match['away_score']} {match['away_team']}"
        body = (
            f"Final score — {match['home_team']} {match['home_score']}-{match['away_score']} {match['away_team']}\n\n"
            f"Check your prediction points and the latest leaderboard on WorldCup 2026."
        )

        sent, skipped, failed = 0, 0, 0
        for r in recipients:
            try:
                # Personalize with each user's own prediction points if they made one
                pred = row(db.execute(
                    "SELECT points_awarded, predicted_home_score, predicted_away_score FROM predictions WHERE match_id=? AND user_id=?",
                    (match_id, r["id"])
                ))
                personal_body = body
                if pred:
                    personal_body += (
                        f"\n\nYour prediction: {pred['predicted_home_score']}-{pred['predicted_away_score']} "
                        f"→ {pred['points_awarded'] or 0} points"
                    )
                status = send_email(r["email"], subject, personal_body)
                if status == "sent":
                    sent += 1
                else:
                    skipped += 1
            except Exception:
                failed += 1

    return {"sent": sent, "skipped": skipped, "failed": failed, "recipients": ", ".join(r["email"] for r in recipients) or "none"}


# ── EMAIL THIS MATCH'S PARTICIPANTS — used by the "📧 Email" button next to ──
# ── "⬇️ Export This Game" in the admin Prediction List / Reports view.       ──
# Every recipient gets the FULL participant breakdown for this match (same
# data as the Excel export — every player, their prediction, and points),
# not just their own single line.
@router.post("/notify/match-participants/{match_id}")
def notify_match_participants(match_id: int, admin: dict = Depends(admin_user)):
    from ..services.emailer import send_email, queue_notification

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

        # Every participant — used to build the shared breakdown table (everyone,
        # even if they have no email on file, so the table itself is complete).
        all_participants = rows(db.execute(
            """
            SELECT u.id, u.name, u.email,
                   p.predicted_home_score, p.predicted_away_score,
                   p.points_awarded, p.scoring_reason, p.created_at
            FROM predictions p
            JOIN users u ON u.id = p.user_id
            WHERE p.match_id = ?
            ORDER BY p.points_awarded DESC, p.created_at ASC
            """,
            (match_id,),
        ))
        if not all_participants:
            return {"sent": 0, "skipped": 0, "failed": 0, "recipients": "none"}

        # Only participants with a real email actually receive anything
        participants = [p for p in all_participants if p.get("email")]
        if not participants:
            return {"sent": 0, "skipped": 0, "failed": 0, "recipients": "none"}

        is_completed = match["status"] == "completed"
        match_label  = f"{match['home_team']} vs {match['away_team']}"

        if is_completed:
            subject = f"Result: {match['home_team']} {match['home_score']}-{match['away_score']} {match['away_team']}"
        else:
            subject = f"Update: {match_label}"

        # ── Build the shared breakdown block — same data as "Export This Game" ──
        header_lines = [
            match_label,
            f"Game No: {match.get('game_no') or '—'}   Round: {match.get('round') or '—'}",
            f"Venue: {match.get('stadium') or '—'}",
            f"Kickoff: {match.get('match_date') or '—'}",
        ]
        if is_completed:
            header_lines.append(f"Final Score: {match['home_score']}-{match['away_score']}")
        else:
            header_lines.append("Final Score: Pending")

        table_lines = ["", f"All Predictions ({len(all_participants)} participant{'s' if len(all_participants) != 1 else ''}):", ""]
        for p in all_participants:
            outcome = (p.get("scoring_reason") or ("pending" if not is_completed else "—")).replace("_", " ")
            points  = f"{p.get('points_awarded') or 0} pts" if is_completed else "—"
            name    = str(p.get("name") or "Unknown")
            pred    = f"{p.get('predicted_home_score') or 0}-{p.get('predicted_away_score') or 0}"
            table_lines.append(
                f"  • {name:<20} {pred:<6} {outcome:<25} {points}"
            )

        full_breakdown = "\n".join(header_lines + table_lines)

        sent, skipped, failed = 0, 0, 0
        errors = []
        for r in participants:
            try:
                my_outcome = (r.get("scoring_reason") or ("pending" if not is_completed else "—")).replace("_", " ")
                my_points  = f"{r.get('points_awarded') or 0} points" if is_completed else "pending"
                my_pred    = f"{r.get('predicted_home_score') or 0}-{r.get('predicted_away_score') or 0}"
                personal_line = f"Your prediction: {my_pred} ({my_outcome}) — {my_points}\n"
                body = personal_line + "\n" + full_breakdown
                status = send_email(r["email"], subject, body)
                queue_notification(db, r["email"], subject, body, r["id"])
                if status == "sent":
                    sent += 1
                else:
                    skipped += 1
            except Exception as e:
                failed += 1
                errors.append(f"{r.get('email','?')}: {str(e)}")

    return {
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
        "recipients": ", ".join(r["email"] for r in participants),
        "errors": errors,   # shows exactly what went wrong if failed > 0
    }


# ── REGISTRATION SETTINGS ────────────────────────────────────────────────────
@router.get("/registration-settings")
def get_registration_settings(user: dict = Depends(current_user)):
    with get_db() as db:
        setting = row(db.execute(
            "SELECT value FROM app_settings WHERE key='registration_requirements'"
        ))
    import json
    return json.loads(setting["value"]) if setting else {"email_required": True, "mobile_required": False}


@router.put("/registration-settings")
def update_registration_settings(payload: dict, admin: dict = Depends(admin_user)):
    import json
    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES ('registration_requirements', ?, CURRENT_TIMESTAMP)",
            (json.dumps(payload),)
        )
    return payload


# ─────────────────────────────────────────────────────────────────────────────
#  AUTO-FETCH LIVE SCORE  —  pulls result from API-Football (free tier)
#  Sign up free at https://dashboard.api-football.com/register (no card needed)
#  Then set the API key as an environment variable: API_FOOTBALL_KEY
# ─────────────────────────────────────────────────────────────────────────────
import os
from difflib import SequenceMatcher

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

API_FOOTBALL_KEY  = os.environ.get("API_FOOTBALL_KEY", "")
API_FOOTBALL_HOST = "v3.football.api-sports.io"
API_FOOTBALL_BASE = "https://v3.football.api-sports.io"


def _name_similarity(a: str, b: str) -> float:
    """Fuzzy match team names (handles 'USA' vs 'United States' etc)."""
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


@router.get("/matches/{match_id}/fetch-live-score")
def fetch_live_score(match_id: int, admin: dict = Depends(admin_user)):
    """
    Auto-fetch the live/final score for a match from API-Football.
    Matches by team names + date (fuzzy matching handles name variations
    like 'USA' vs 'United States').

    Returns the found score WITHOUT saving it — admin reviews and confirms
    via the normal /matches/{id}/score endpoint.
    """
    if not _HTTPX_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="httpx is not installed on the server. Run: pip install httpx"
        )

    if not API_FOOTBALL_KEY:
        raise HTTPException(
            status_code=503,
            detail="API_FOOTBALL_KEY not configured. Sign up free at "
                   "https://dashboard.api-football.com/register and set the "
                   "API_FOOTBALL_KEY environment variable."
        )

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
    if not match["match_date"]:
        raise HTTPException(status_code=400, detail="Match has no date set")

    # Extract just the date part (API-Football wants YYYY-MM-DD)
    match_date_str = str(match["match_date"])[:10]

    headers = {
        "x-rapidapi-host": API_FOOTBALL_HOST,
        "x-rapidapi-key":  API_FOOTBALL_KEY,
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"{API_FOOTBALL_BASE}/fixtures",
                headers=headers,
                params={"date": match_date_str},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"API-Football request failed: {e}")

    fixtures = data.get("response", [])
    if not fixtures:
        return {
            "found": False,
            "message": f"No fixtures found for {match_date_str}. The match may not be in API-Football's database yet, or check the date.",
        }

    # Find best matching fixture by team name similarity
    best_match  = None
    best_score  = 0.0
    for fx in fixtures:
        home_api = fx["teams"]["home"]["name"]
        away_api = fx["teams"]["away"]["name"]

        # Try both orientations (home/away could be swapped)
        score_normal  = _name_similarity(match["home_team"], home_api) + _name_similarity(match["away_team"], away_api)
        score_swapped = _name_similarity(match["home_team"], away_api) + _name_similarity(match["away_team"], home_api)

        score = max(score_normal, score_swapped)
        if score > best_score:
            best_score = score
            best_match = fx
            best_match["_swapped"] = score_swapped > score_normal

    # Require reasonably confident match (each team ~70%+ similar on average)
    if not best_match or best_score < 1.3:
        return {
            "found": False,
            "message": f"Could not confidently match '{match['home_team']} vs {match['away_team']}' "
                       f"to any fixture on {match_date_str}. Best guess was "
                       f"'{best_match['teams']['home']['name']} vs {best_match['teams']['away']['name']}' "
                       f"({best_score/2*100:.0f}% confidence)." if best_match else
                       f"No fixtures found matching teams on {match_date_str}.",
        }

    goals = best_match.get("goals", {})
    fixture_status = best_match.get("fixture", {}).get("status", {}).get("short", "")

    home_score = goals.get("away") if best_match.get("_swapped") else goals.get("home")
    away_score = goals.get("home") if best_match.get("_swapped") else goals.get("away")

    # Map API-Football status codes to our statuses
    status_map = {
        "NS": "scheduled",  # Not Started
        "1H": "live", "2H": "live", "HT": "live", "ET": "live", "P": "live", "LIVE": "live",
        "FT": "completed", "AET": "completed", "PEN": "completed",
        "PST": "scheduled", "CANC": "scheduled", "ABD": "scheduled",
    }
    suggested_status = status_map.get(fixture_status, "live")

    return {
        "found": True,
        "confidence": round(best_score / 2 * 100, 1),
        "home_team_matched": best_match["teams"]["home" if not best_match.get("_swapped") else "away"]["name"],
        "away_team_matched": best_match["teams"]["away" if not best_match.get("_swapped") else "home"]["name"],
        "home_score": home_score,
        "away_score": away_score,
        "fixture_status": fixture_status,
        "suggested_status": suggested_status,
        "kickoff": best_match.get("fixture", {}).get("date"),
        "venue": best_match.get("fixture", {}).get("venue", {}).get("name"),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  REMOVE DUPLICATE MATCHES
#  Finds matches in the same tournament that share the same home team, away
#  team, AND match_date (i.e. clearly the same fixture imported more than
#  once, even if game_no differs across import attempts). Keeps the EARLIEST
#  row (lowest id) and deletes the rest. Matches that already have real
#  predictions are reported but NOT deleted automatically — admin can pass
#  force=true to delete them anyway (their predictions are deleted too).
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/matches/{tournament_id}/duplicates")
def find_duplicate_matches(tournament_id: int, admin: dict = Depends(admin_user)):
    """Preview duplicate matches without deleting anything."""
    with get_db() as db:
        all_matches = rows(db.execute(
            """
            SELECT m.id, m.game_no, m.round, m.match_date, m.home_team_id, m.away_team_id,
                   ht.name AS home_team, at.name AS away_team,
                   (SELECT COUNT(*) FROM predictions p WHERE p.match_id = m.id) AS prediction_count
            FROM matches m
            JOIN teams ht ON ht.id = m.home_team_id
            JOIN teams at ON at.id = m.away_team_id
            WHERE m.tournament_id = ?
            ORDER BY m.id
            """,
            (tournament_id,)
        ))

    groups = {}
    for m in all_matches:
        key = (m["home_team_id"], m["away_team_id"], m["match_date"])
        groups.setdefault(key, []).append(m)

    duplicate_groups = [g for g in groups.values() if len(g) > 1]
    total_extra = sum(len(g) - 1 for g in duplicate_groups)

    return {
        "duplicate_groups": len(duplicate_groups),
        "extra_matches_to_remove": total_extra,
        "groups": duplicate_groups,
    }


@router.delete("/matches/{tournament_id}/duplicates")
def remove_duplicate_matches(tournament_id: int, force: bool = False, admin: dict = Depends(admin_user)):
    """
    Delete duplicate matches, keeping the earliest (lowest id) row per
    (home_team_id, away_team_id, match_date) group.
    By default, skips any duplicate that already has predictions attached
    (to avoid silently wiping real user data). Pass ?force=true to delete
    those too (their predictions are removed as well).
    """
    with get_db() as db:
        all_matches = rows(db.execute(
            """
            SELECT m.id, m.home_team_id, m.away_team_id, m.match_date,
                   (SELECT COUNT(*) FROM predictions p WHERE p.match_id = m.id) AS prediction_count
            FROM matches m
            WHERE m.tournament_id = ?
            ORDER BY m.id
            """,
            (tournament_id,)
        ))

        groups = {}
        for m in all_matches:
            key = (m["home_team_id"], m["away_team_id"], m["match_date"])
            groups.setdefault(key, []).append(m)

        deleted = []
        skipped_with_predictions = []

        for key, group in groups.items():
            if len(group) <= 1:
                continue
            # Keep the first (lowest id, i.e. earliest created); consider the rest for deletion
            keep, *rest = group
            for dup in rest:
                if dup["prediction_count"] > 0 and not force:
                    skipped_with_predictions.append(dup["id"])
                    continue
                db.execute("DELETE FROM predictions WHERE match_id = ?", (dup["id"],))
                db.execute("DELETE FROM match_history WHERE match_id = ?", (dup["id"],))
                db.execute("DELETE FROM matches WHERE id = ?", (dup["id"],))
                deleted.append(dup["id"])

    return {
        "deleted_match_ids": deleted,
        "deleted_count": len(deleted),
        "skipped_with_predictions": skipped_with_predictions,
        "message": (
            f"Removed {len(deleted)} duplicate match(es)."
            + (f" {len(skipped_with_predictions)} duplicate(s) were skipped because they already "
               f"have predictions — re-run with force=true to remove those too."
               if skipped_with_predictions else "")
        ),
    }
