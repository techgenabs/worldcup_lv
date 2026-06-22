from .scoring import complete_match_workflow


def fetch_live_scores(db) -> int:
    """
    Provider adapter placeholder. Store external IDs on matches and map API-Football,
    CricAPI, FIFA, or league provider payloads into home_score/away_score/status here.
    """
    auto_matches = db.execute(
        "SELECT * FROM matches WHERE result_mode = 'auto' AND status IN ('locked', 'live')"
    ).fetchall()
    updated = 0
    for match in auto_matches:
        if not match["external_match_id"]:
            continue
        # Production provider call belongs here. We leave it non-destructive without credentials.
        updated += 0
    return updated


def mark_final_from_provider(db, match_id: int, home_score: int, away_score: int) -> dict:
    match = dict(db.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone())
    winner = loser = None
    if home_score > away_score:
        winner, loser = match["home_team_id"], match["away_team_id"]
    elif away_score > home_score:
        winner, loser = match["away_team_id"], match["home_team_id"]
    db.execute(
        """
        UPDATE matches
        SET home_score = ?, away_score = ?, winner_team_id = ?, loser_team_id = ?, status = 'completed'
        WHERE id = ?
        """,
        (home_score, away_score, winner, loser, match_id),
    )
    return complete_match_workflow(db, match_id)
