import csv
from pathlib import Path

import pandas as pd

from ..config import settings


def export_tournament(db, tournament_id: int) -> dict:
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    base = Path(settings.export_dir) / f"tournament_{tournament_id}"
    matches = [
        dict(row)
        for row in db.execute(
            """
            SELECT m.id, m.round, m.match_date, m.stadium, ht.name AS home_team, at.name AS away_team,
                   m.home_score, m.away_score, m.status, wt.name AS winner
            FROM matches m
            JOIN teams ht ON ht.id = m.home_team_id
            JOIN teams at ON at.id = m.away_team_id
            LEFT JOIN teams wt ON wt.id = m.winner_team_id
            WHERE m.tournament_id = ?
            ORDER BY m.match_date, m.id
            """,
            (tournament_id,),
        ).fetchall()
    ]
    csv_path = base.with_suffix(".csv")
    txt_path = base.with_suffix(".txt")
    xlsx_path = base.with_suffix(".xlsx")
    if matches:
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=matches[0].keys())
            writer.writeheader()
            writer.writerows(matches)
        pd.DataFrame(matches).to_excel(xlsx_path, index=False)
    else:
        csv_path.write_text("No matches yet\n", encoding="utf-8")
        pd.DataFrame([]).to_excel(xlsx_path, index=False)
    txt_path.write_text("\n".join(str(match) for match in matches) or "No history yet", encoding="utf-8")
    return {"csv": str(csv_path), "excel": str(xlsx_path), "txt": str(txt_path)}
