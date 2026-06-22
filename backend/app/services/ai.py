from math import exp


def _logistic(x: float) -> float:
    return 1 / (1 + exp(-x))


def team_strength(db, team_id: int) -> float:
    team = db.execute("SELECT ranking, home_advantage, strength_score FROM teams WHERE id = ?", (team_id,)).fetchone()
    if not team:
        return 50
    matches = db.execute(
        """
        SELECT home_team_id, away_team_id, home_score, away_score
        FROM matches
        WHERE status = 'completed' AND (home_team_id = ? OR away_team_id = ?)
        """,
        (team_id, team_id),
    ).fetchall()
    wins = played = goal_delta = 0
    for match in matches:
        home = match["home_team_id"] == team_id
        own = match["home_score"] if home else match["away_score"]
        opp = match["away_score"] if home else match["home_score"]
        played += 1
        goal_delta += (own or 0) - (opp or 0)
        if (own or 0) > (opp or 0):
            wins += 1
    win_ratio = wins / played if played else 0.5
    ranking_component = max(0, 100 - float(team["ranking"] or 50))
    form_component = win_ratio * 60 + goal_delta * 2
    return max(1, min(99, ranking_component * 0.4 + form_component * 0.5 + float(team["home_advantage"] or 0)))


def predict_match(db, home_team_id: int, away_team_id: int) -> dict:
    home_strength = team_strength(db, home_team_id) + 4
    away_strength = team_strength(db, away_team_id)
    delta = (home_strength - away_strength) / 22
    home_prob = _logistic(delta)
    away_prob = 1 - home_prob
    draw_prob = max(0.08, 0.22 - abs(home_prob - away_prob) * 0.16)
    home_prob *= 1 - draw_prob
    away_prob *= 1 - draw_prob
    total = home_prob + away_prob + draw_prob
    return {
        "home_probability": round(home_prob / total * 100, 1),
        "away_probability": round(away_prob / total * 100, 1),
        "draw_probability": round(draw_prob / total * 100, 1),
        "confidence": round(max(home_prob, away_prob, draw_prob) / total * 100, 1),
        "model": "hybrid-logistic-strength",
    }


def tournament_winner_forecast(db, tournament_id: int) -> list[dict]:
    teams = db.execute("SELECT id, name, flag FROM teams WHERE tournament_id = ?", (tournament_id,)).fetchall()
    scored = [{"id": t["id"], "name": t["name"], "flag": t["flag"], "strength": team_strength(db, t["id"])} for t in teams]
    total = sum(item["strength"] for item in scored) or 1
    for item in scored:
        item["winning_probability"] = round(item["strength"] / total * 100, 1)
    return sorted(scored, key=lambda x: x["winning_probability"], reverse=True)


def commentary(home: str, away: str, home_score: int, away_score: int) -> str:
    if home_score == away_score:
        return f"{home} and {away} shared the points after a tense {home_score}-{away_score} finish."
    winner = home if home_score > away_score else away
    loser = away if home_score > away_score else home
    margin = abs(home_score - away_score)
    tone = "commanding" if margin >= 3 else "narrow"
    return f"{winner} earned a {tone} win over {loser}, turning pressure into a {home_score}-{away_score} result."
