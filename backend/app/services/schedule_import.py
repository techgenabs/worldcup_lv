from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd

from .official_fixtures import FLAG_BY_TEAM
from .scoring import lock_time


REQUIRED_COLUMNS = {"game_no", "team_a", "team_b", "venue", "match_date"}


def _clean(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def _team_id(db, tournament_id: int, name: str) -> int:
    team = db.execute(
        "SELECT id FROM teams WHERE tournament_id = ? AND name = ?",
        (tournament_id, name),
    ).fetchone()
    if team:
        return int(team["id"])
    # Uploaded schedules may include new teams, so create them with neutral defaults.
    cur = db.execute(
        """
        INSERT INTO teams (tournament_id, name, country, flag, ranking, home_advantage, strength_score)
        VALUES (?, ?, ?, ?, 50, 0, 50)
        """,
        (tournament_id, name, name, FLAG_BY_TEAM.get(name, "🏆")),
    )
    return int(cur.lastrowid)


def import_schedule_excel(db, tournament_id: int, filename: str, content: bytes) -> dict:
    suffix = Path(filename).suffix or ".xlsx"
    with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        frame = pd.read_excel(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    normalized = {str(column).strip().lower(): column for column in frame.columns}
    missing = sorted(REQUIRED_COLUMNS - set(normalized))
    if missing:
        return {"imported": 0, "skipped": 0, "errors": [f"Missing columns: {', '.join(missing)}"]}

    imported = skipped = 0
    errors: list[str] = []
    for index, source_row in frame.iterrows():
        game_no = _clean(source_row[normalized["game_no"]])
        team_a = _clean(source_row[normalized["team_a"]])
        team_b = _clean(source_row[normalized["team_b"]])
        venue = _clean(source_row[normalized["venue"]])
        raw_date = source_row[normalized["match_date"]]
        match_date = raw_date.isoformat() if hasattr(raw_date, "isoformat") else _clean(raw_date)
        sport = _clean(source_row[normalized["sport"]]) if "sport" in normalized else "FIFA World Cup"
        round_name = _clean(source_row[normalized["round"]]) if "round" in normalized else "Group Stage"

        if not game_no or not team_a or not team_b or not match_date:
            errors.append(f"Row {index + 2}: game_no, team_a, team_b, and match_date are required")
            continue
        existing = db.execute(
            "SELECT id FROM matches WHERE tournament_id = ? AND game_no = ?",
            (tournament_id, game_no),
        ).fetchone()
        if existing:
            skipped += 1
            continue
        team_a_id = _team_id(db, tournament_id, team_a)
        team_b_id = _team_id(db, tournament_id, team_b)
        # Uploaded rows are added as extra scheduled matches; they never replace existing matches.
        db.execute(
            """
            INSERT INTO matches (tournament_id, game_no, sport, round, match_date, lock_at, stadium,
                                 home_team_id, away_team_id, status, predictions_open)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', 1)
            """,
            (tournament_id, game_no, sport, round_name, match_date, lock_time(match_date), venue, team_a_id, team_b_id),
        )
        imported += 1
    return {"imported": imported, "skipped": skipped, "errors": errors}
