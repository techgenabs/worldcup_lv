from .util import cricket_nrr, football_goal_diff


def standings(db, tournament_id: int) -> list[dict]:
    teams = [
        dict(row)
        for row in db.execute(
            "SELECT id, name, country, flag, ranking, strength_score FROM teams WHERE tournament_id = ?",
            (tournament_id,),
        ).fetchall()
    ]
    table = {
        team["id"]: {
            **team,
            "played": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "scored": 0,
            "conceded": 0,
            "points": 0,
            "goal_difference": 0,
            "net_run_rate": 0,
        }
        for team in teams
    }
    matches = db.execute(
        """
        SELECT m.*, t.sport
        FROM matches m
        JOIN tournaments t ON t.id = m.tournament_id
        WHERE m.tournament_id = ? AND m.status = 'completed'
        """,
        (tournament_id,),
    ).fetchall()
    sport = "football"
    for match in matches:
        sport = match["sport"]
        home = table[match["home_team_id"]]
        away = table[match["away_team_id"]]
        hs = match["home_score"] or 0
        away_score = match["away_score"] or 0
        for item, scored, conceded in ((home, hs, away_score), (away, away_score, hs)):
            item["played"] += 1
            item["scored"] += scored
            item["conceded"] += conceded
        if hs > away_score:
            home["wins"] += 1
            away["losses"] += 1
            home["points"] += 3 if sport == "football" else 2
        elif hs < away_score:
            away["wins"] += 1
            home["losses"] += 1
            away["points"] += 3 if sport == "football" else 2
        else:
            home["draws"] += 1
            away["draws"] += 1
            home["points"] += 1
            away["points"] += 1
    for item in table.values():
        item["goal_difference"] = football_goal_diff(item["scored"], item["conceded"])
        item["net_run_rate"] = cricket_nrr(item["scored"], item["conceded"], item["played"])
    return sorted(
        table.values(),
        key=lambda x: (x["points"], x["goal_difference"], x["scored"], -x["ranking"]),
        reverse=True,
    )
