# ─────────────────────────────────────────────────────────────────────────────
#  ADD THIS ROUTE TO backend/app/routers/predictions.py
#  (paste it anywhere inside the file, after the router = APIRouter(...) line,
#   alongside your existing @router.get("/leaderboard") route)
#
#  PURPOSE — Two types of "winner" the app now supports:
#
#  TYPE A — Per-game winner (THIS endpoint)
#    For each individual completed match, who predicted it best?
#    Ranked: exact score first, then correct outcome (win/draw/loss) as the
#    tiebreak group, then earliest submission time as the final tiebreak.
#    This lets you announce "who won today's Brazil vs Morocco prediction".
#
#  TYPE B — Overall tournament winner (already exists)
#    Your existing GET /predictions/leaderboard route — cumulative points
#    across every match in the tournament. This stays unchanged; it's the
#    "who's leading overall" view.
#
#  Both are exposed in the UI as two tabs: "🎯 Per-Game Winners" and
#  "🏆 Overall Leaderboard" under one Leaderboard page.
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/match-winners")
def match_winners(tournament_id: int | None = None, user: dict = Depends(current_user)):
    """
    Returns, for every COMPLETED match, the prediction(s) that scored best.
    Ranking within a match: exact_score > correct_winner > wrong (by
    scoring_reason / points_awarded), tiebreak by earliest updated_at.

    Regular (non-admin) users only see matches they personally participated
    in — matching the same "only participants can see completed results"
    rule used elsewhere in the app. Admins see every completed match.
    """
    with get_db() as db:
        base_sql = """
            SELECT p.*, u.id AS uid, u.name AS user_name, u.country AS user_country,
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

    # Group by match, pick the winner(s) — i.e. everyone tied at the top
    # points_awarded value for that match (handles genuine ties fairly).
    by_match: dict[int, list[dict]] = {}
    for r in all_rows:
        by_match.setdefault(r["mid"], []).append(r)

    results = []
    for mid, preds in by_match.items():
        top_points = max(p["points_awarded"] or 0 for p in preds)
        winners = [p for p in preds if (p["points_awarded"] or 0) == top_points]
        sample = preds[0]
        results.append({
            "match_id":   mid,
            "game_no":    sample["game_no"],
            "round":      sample["round"],
            "match_date": sample["match_date"],
            "home_team":  sample["home_team"],
            "away_team":  sample["away_team"],
            "home_score": sample["home_score"],
            "away_score": sample["away_score"],
            "total_participants": len(preds),
            "winning_points": top_points,
            "winners": [
                {
                    "user_id":      w["uid"],
                    "user_name":    w["user_name"],
                    "user_country": w["user_country"],
                    "predicted_home_score": w["predicted_home_score"],
                    "predicted_away_score": w["predicted_away_score"],
                    "points_awarded": w["points_awarded"],
                    "scoring_reason": w["scoring_reason"],
                }
                for w in winners
            ],
        })

    # Most recent completed match first (already true from SQL order, but
    # dict grouping above can shuffle — resort explicitly to be safe)
    results.sort(key=lambda r: r["match_date"] or "", reverse=True)

    return {"matches": results, "total_completed_matches": len(results)}
