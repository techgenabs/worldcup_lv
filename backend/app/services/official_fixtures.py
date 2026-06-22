from .scoring import lock_time


FIFA_2026_SOURCE_URL = "https://vod.fifa.com/organisation/media-releases/updated-world-cup-2026-match-schedule-venues-kick-off-times-104-matches"


FLAG_BY_TEAM = {
    "Brazil": "🇧🇷",
    "Canada": "🇨🇦",
    "Croatia": "🇭🇷",
    "Curacao": "🇨🇼",
    "England": "🏴",
    "Germany": "🇩🇪",
    "Japan": "🇯🇵",
    "Mexico": "🇲🇽",
    "Morocco": "🇲🇦",
    "Paraguay": "🇵🇾",
    "South Africa": "🇿🇦",
    "Tunisia": "🇹🇳",
    "USA": "🇺🇸",
    "European Play-off Winner": "🏆",
}


OFFICIAL_FIFA_2026_FIXTURES = [
    {
        "game_no": "FIFA-001",
        "team_a": "Mexico",
        "team_b": "South Africa",
        "venue": "Mexico City Stadium",
        "match_date": "2026-06-11T13:00:00-06:00",
        "source_note": "Opening match published by FIFA.",
    },
    {
        "game_no": "FIFA-CAN-001",
        "team_a": "Canada",
        "team_b": "European Play-off Winner",
        "venue": "Toronto Stadium",
        "match_date": "2026-06-12T15:00:00-04:00",
        "source_note": "Canada opening fixture published by FIFA.",
    },
    {
        "game_no": "FIFA-USA-001",
        "team_a": "USA",
        "team_b": "Paraguay",
        "venue": "Los Angeles Stadium",
        "match_date": "2026-06-12T18:00:00-07:00",
        "source_note": "USA opening fixture published by FIFA.",
    },
    {
        "game_no": "FIFA-010",
        "team_a": "Curacao",
        "team_b": "Germany",
        "venue": "Houston Stadium",
        "match_date": "2026-06-14T12:00:00-05:00",
        "source_note": "Match 10 detail published by FIFA.",
    },
    {
        "game_no": "FIFA-BRA-MAR",
        "team_a": "Brazil",
        "team_b": "Morocco",
        "venue": "New York New Jersey Stadium",
        "match_date": "2026-06-13T18:00:00-04:00",
        "source_note": "Group-stage fixture highlighted by FIFA.",
    },
    {
        "game_no": "FIFA-ENG-CRO",
        "team_a": "England",
        "team_b": "Croatia",
        "venue": "Dallas Stadium",
        "match_date": "2026-06-17T15:00:00-05:00",
        "source_note": "Group L fixture highlighted by FIFA.",
    },
    {
        "game_no": "FIFA-1000",
        "team_a": "Tunisia",
        "team_b": "Japan",
        "venue": "Monterrey Stadium",
        "match_date": "2026-06-20T22:00:00-06:00",
        "source_note": "1,000th FIFA World Cup match published by FIFA.",
    },
]


def _team_id(db, tournament_id: int, name: str) -> int:
    team = db.execute(
        "SELECT id FROM teams WHERE tournament_id = ? AND name = ?",
        (tournament_id, name),
    ).fetchone()
    if team:
        return int(team["id"])
    # Imported teams are intentionally neutral defaults; admins can edit ranking/flags later.
    cur = db.execute(
        """
        INSERT INTO teams (tournament_id, name, country, flag, ranking, home_advantage, strength_score)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (tournament_id, name, name, FLAG_BY_TEAM.get(name, "🏆"), 50, 0, 50),
    )
    return int(cur.lastrowid)


def import_official_fifa_2026_fixtures(db, tournament_id: int) -> dict:
    imported = skipped = 0
    for fixture in OFFICIAL_FIFA_2026_FIXTURES:
        existing = db.execute(
            "SELECT id FROM matches WHERE tournament_id = ? AND game_no = ?",
            (tournament_id, fixture["game_no"]),
        ).fetchone()
        if existing:
            skipped += 1
            continue
        team_a_id = _team_id(db, tournament_id, fixture["team_a"])
        team_b_id = _team_id(db, tournament_id, fixture["team_b"])
        # Store the official local kickoff as an ISO datetime with offset, so lock time is exact.
        db.execute(
            """
            INSERT INTO matches (tournament_id, game_no, sport, round, match_date, lock_at, stadium,
                                 home_team_id, away_team_id, status, live_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?)
            """,
            (
                tournament_id,
                fixture["game_no"],
                "FIFA World Cup",
                "Group Stage",
                fixture["match_date"],
                lock_time(fixture["match_date"]),
                fixture["venue"],
                team_a_id,
                team_b_id,
                FIFA_2026_SOURCE_URL,
            ),
        )
        imported += 1
    return {"imported": imported, "skipped": skipped, "source": FIFA_2026_SOURCE_URL}
