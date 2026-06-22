from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from ..config import settings


def _season_dir(season: str) -> Path:
    path = Path(settings.export_dir) / season
    path.mkdir(parents=True, exist_ok=True)
    return path


def _frame(db, sql: str, params=()) -> pd.DataFrame:
    return pd.DataFrame([dict(item) for item in db.execute(sql, params).fetchall()])


def export_reports(db, season: str = "2026") -> dict:
    base = _season_dir(season)
    predictions = _frame(
        db,
        """
        SELECT p.id, u.name AS user, u.email, m.game_no, ht.name AS team_a, at.name AS team_b,
               p.predicted_home_score AS predicted_team_a_score,
               p.predicted_away_score AS predicted_team_b_score,
               p.confidence_level, p.points_awarded, p.scoring_reason, p.locked_at, p.scored_at
        FROM predictions p
        JOIN users u ON u.id = p.user_id
        JOIN matches m ON m.id = p.match_id
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        ORDER BY m.match_date, u.name
        """,
    )
    results = _frame(
        db,
        """
        SELECT m.game_no, m.sport, m.round, m.match_date, m.stadium, ht.name AS team_a, at.name AS team_b,
               m.home_score AS team_a_score, m.away_score AS team_b_score, m.status, wt.name AS winner
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        LEFT JOIN teams wt ON wt.id = m.winner_team_id
        ORDER BY m.match_date
        """,
    )
    leaderboard = _frame(
        db,
        """
        SELECT l.rank, u.name, u.email, u.country, l.total_points, l.exact_matches,
               l.winner_count, l.predictions_count, l.accuracy, l.badges
        FROM leaderboards l
        JOIN users u ON u.id = l.user_id
        WHERE l.season = ?
        ORDER BY l.rank
        """,
        (season,),
    )
    audit = _frame(db, "SELECT * FROM audit_logs ORDER BY created_at DESC")
    files = {
        "predictions": base / "predictions.xlsx",
        "results": base / "results.xlsx",
        "leaderboard": base / "leaderboard.xlsx",
        "audit_log": base / "audit_log.xlsx",
    }
    predictions.to_excel(files["predictions"], index=False)
    results.to_excel(files["results"], index=False)
    leaderboard.to_excel(files["leaderboard"], index=False)
    audit.to_excel(files["audit_log"], index=False)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    archive = base / f"archive_{stamp}.xlsx"
    with pd.ExcelWriter(archive, engine="openpyxl") as writer:
        predictions.to_excel(writer, sheet_name="predictions", index=False)
        results.to_excel(writer, sheet_name="results", index=False)
        leaderboard.to_excel(writer, sheet_name="leaderboard", index=False)
        audit.to_excel(writer, sheet_name="audit_log", index=False)
    files["archive"] = archive
    return {name: str(path) for name, path in files.items()}


def export_prediction_participants(db, season: str = "2026") -> dict:
    base = _season_dir(season)
    rows = [
        dict(item)
        for item in db.execute(
            """
            SELECT u.id AS user_id, u.name, u.email, u.mobile, u.country,
                   m.game_no, m.match_date, ht.name AS team_a, at.name AS team_b,
                   p.predicted_home_score AS team_a_prediction_goals,
                   p.predicted_away_score AS team_b_prediction_goals,
                   p.points_awarded, p.scoring_reason, p.updated_at
            FROM predictions p
            JOIN users u ON u.id = p.user_id
            JOIN matches m ON m.id = p.match_id
            JOIN teams ht ON ht.id = m.home_team_id
            JOIN teams at ON at.id = m.away_team_id
            ORDER BY p.updated_at DESC
            """
        ).fetchall()
    ]
    nepal = ZoneInfo("Asia/Kathmandu")
    for item in rows:
        parsed = pd.to_datetime(item["match_date"], errors="coerce")
        if not pd.isna(parsed):
            if parsed.tzinfo is None:
                parsed = parsed.tz_localize(nepal)
            else:
                parsed = parsed.tz_convert(nepal)
            item["nepali_date"] = parsed.strftime("%Y-%m-%d")
            item["nepali_time"] = parsed.strftime("%I:%M %p NPT")
        else:
            item["nepali_date"] = ""
            item["nepali_time"] = item["match_date"] or ""
    path = base / "prediction_participants.xlsx"
    pd.DataFrame(rows).to_excel(path, index=False)
    return {"participants": str(path), "rows": len(rows)}
