from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..config import settings
from .emailer import queue_notification
from .reports import export_reports


LOCK_MINUTES = 5


def app_tz() -> ZoneInfo:
    return ZoneInfo(settings.app_timezone)


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=app_tz())
    except ValueError:
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def lock_deadline(match: dict) -> datetime | None:
    return parse_dt(match.get("lock_at")) or parse_dt(lock_time(match.get("match_date")))


def lock_time(match_date: str | None) -> str | None:
    kickoff = parse_dt(match_date)
    if not kickoff:
        return None
    return (kickoff - timedelta(minutes=LOCK_MINUTES)).isoformat()


def is_prediction_locked(match: dict) -> bool:
    if match.get("status") in {"locked", "live", "completed", "cancelled"}:
        return True
    lock_at = lock_deadline(match)
    if not lock_at:
        return False
    return datetime.now(lock_at.tzinfo) >= lock_at


def confidence_bonus(level: str | None, base_points: int) -> int:
    if (level or "").lower() == "high" and base_points > 0:
        return 2
    return 0


def score_prediction(prediction: dict, match: dict) -> tuple[int, int, str]:
    ph = int(prediction.get("predicted_home_score") or 0)
    pa = int(prediction.get("predicted_away_score") or 0)
    hs = int(match.get("home_score") or 0)
    away_score = int(match.get("away_score") or 0)
    pred_delta = ph - pa
    actual_delta = hs - away_score
    exact = ph == hs and pa == away_score
    if exact:
        base, reason = 10, "Exact Score"
    elif pred_delta == actual_delta and pred_delta != 0:
        base, reason = 7, "Correct Winner + Goal Difference"
    elif pred_delta == 0 and actual_delta == 0:
        base, reason = 5, "Correct Draw"
    elif (pred_delta > 0 and actual_delta > 0) or (pred_delta < 0 and actual_delta < 0):
        base, reason = 5, "Correct Winner"
    else:
        base, reason = 0, "Wrong Prediction"
    bonus = confidence_bonus(prediction.get("confidence_level"), base)
    if bonus:
        reason = f"{reason} + High Confidence Bonus"
    return base + bonus, 1 if base else 0, reason


def _safe_fetchone(cursor) -> dict:
    result = cursor.fetchone()
    if result is None:
        return {}
    if isinstance(result, dict):
        return result
    if hasattr(cursor, '_cur') and cursor._cur.description:
        cols = [d[0] for d in cursor._cur.description]
        return dict(zip(cols, result))
    if hasattr(cursor, 'description') and cursor.description:
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, result))
    return {}


def _safe_fetchall(cursor) -> list[dict]:
    results = cursor.fetchall()
    if not results:
        return []
    if isinstance(results[0], dict):
        return results
    if hasattr(cursor, '_cur') and cursor._cur.description:
        cols = [d[0] for d in cursor._cur.description]
        return [dict(zip(cols, r)) for r in results]
    if hasattr(cursor, 'description') and cursor.description:
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, r)) for r in results]
    return [dict(r) if hasattr(r, 'keys') else {} for r in results]


def _upsert_leaderboard(db, item: dict, season: str, index: int, badges: list) -> None:
    from ..database import _is_postgres
    if _is_postgres():
        raw_conn = db._conn
        cur = raw_conn.cursor()
        cur.execute(
            """
            INSERT INTO leaderboards
                (user_id, season, total_points, exact_matches, winner_count,
                 predictions_count, accuracy, rank, badges, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id, season) DO UPDATE
                SET total_points      = EXCLUDED.total_points,
                    exact_matches     = EXCLUDED.exact_matches,
                    winner_count      = EXCLUDED.winner_count,
                    predictions_count = EXCLUDED.predictions_count,
                    accuracy          = EXCLUDED.accuracy,
                    rank              = EXCLUDED.rank,
                    badges            = EXCLUDED.badges,
                    updated_at        = CURRENT_TIMESTAMP
            """,
            (
                item["user_id"], season,
                item["total_points"], item["exact_matches"],
                item["winner_count"], item["predictions_count"],
                item["accuracy"], index, ", ".join(badges),
            ),
        )
    else:
        db.execute(
            """
            INSERT INTO leaderboards
                (user_id, season, total_points, exact_matches, winner_count,
                 predictions_count, accuracy, rank, badges, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, season)
            DO UPDATE SET total_points = excluded.total_points,
                          exact_matches = excluded.exact_matches,
                          winner_count = excluded.winner_count,
                          predictions_count = excluded.predictions_count,
                          accuracy = excluded.accuracy,
                          rank = excluded.rank,
                          badges = excluded.badges,
                          updated_at = CURRENT_TIMESTAMP
            """,
            (
                item["user_id"], season,
                item["total_points"], item["exact_matches"],
                item["winner_count"], item["predictions_count"],
                item["accuracy"], index, ", ".join(badges),
            ),
        )


def lock_due_predictions(db) -> int:
    reopen = _safe_fetchall(
        db.execute(
            """
            SELECT * FROM matches
            WHERE status = 'locked' AND match_date IS NOT NULL AND predictions_open = 1
            """
        )
    )
    for match in reopen:
        deadline = lock_deadline(match)
        if deadline and datetime.now(deadline.tzinfo) < deadline:
            db.execute(
                "UPDATE matches SET status = 'scheduled', locked_at = NULL WHERE id = ?",
                (match["id"],),
            )
            db.execute(
                "UPDATE predictions SET locked_at = NULL WHERE match_id = ? AND scored_at IS NULL",
                (match["id"],),
            )

    matches = _safe_fetchall(
        db.execute(
            "SELECT * FROM matches WHERE status = 'scheduled' AND match_date IS NOT NULL"
        )
    )
    locked = 0
    for match in matches:
        if not is_prediction_locked(match):
            continue
        timestamp = now_iso()
        db.execute(
            "UPDATE matches SET status = 'locked', locked_at = COALESCE(locked_at, ?) WHERE id = ?",
            (timestamp, match["id"])
        )
        db.execute(
            "UPDATE predictions SET locked_at = COALESCE(locked_at, ?) WHERE match_id = ?",
            (timestamp, match["id"])
        )
        users = _safe_fetchall(
            db.execute(
                """
                SELECT u.id, u.email, p.predicted_home_score, p.predicted_away_score
                FROM predictions p
                JOIN users u ON u.id = p.user_id
                WHERE p.match_id = ?
                """,
                (match["id"],),
            )
        )
        for item in users:
            queue_notification(
                db,
                item["email"],
                "Prediction locked",
                f"Your prediction for game {match.get('game_no') or match['id']} is locked: {item['predicted_home_score']}-{item['predicted_away_score']}.",
                item["id"],
            )
        locked += 1
    return locked


def calculate_match_points(db, match_id: int) -> int:
    match = _safe_fetchone(
        db.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
    )
    predictions = _safe_fetchall(
        db.execute("SELECT * FROM predictions WHERE match_id = ?", (match_id,))
    )
    updated = 0
    timestamp = now_iso()
    for prediction in predictions:
        points, correct, reason = score_prediction(prediction, match)
        db.execute(
            """
            UPDATE predictions
            SET points_awarded = ?, is_correct = ?, scoring_reason = ?, scored_at = ?
            WHERE id = ?
            """,
            (points, correct, reason, timestamp, prediction["id"]),
        )
        updated += 1
    update_leaderboard(db)
    return updated


def update_leaderboard(db, season: str = "2026") -> list[dict]:
    users = _safe_fetchall(
        db.execute(
            "SELECT id, name FROM users WHERE role = 'user' AND is_active = 1"
        )
    )
    result_rows = []
    for user in users:
        stats = _safe_fetchone(
            db.execute(
                """
                SELECT COALESCE(SUM(points_awarded), 0) AS total_points,
                       SUM(CASE WHEN scoring_reason LIKE 'Exact Score%' THEN 1 ELSE 0 END) AS exact_matches,
                       SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) AS winner_count,
                       COUNT(*) AS predictions_count
                FROM predictions
                WHERE user_id = ? AND scored_at IS NOT NULL
                """,
                (user["id"],),
            )
        )
        total_points = int(stats.get("total_points") or 0)
        exact_matches = int(stats.get("exact_matches") or 0)
        winner_count = int(stats.get("winner_count") or 0)
        predictions_count = int(stats.get("predictions_count") or 0)
        accuracy = round((winner_count / predictions_count * 100), 1) if predictions_count else 0

        row = {
            "user_id": user["id"],
            "name": user["name"],
            "total_points": total_points,
            "exact_matches": exact_matches,
            "winner_count": winner_count,
            "predictions_count": predictions_count,
            "accuracy": accuracy,
        }
        result_rows.append(row)

    result_rows.sort(
        key=lambda item: (item["total_points"], item["exact_matches"], item["accuracy"]),
        reverse=True
    )

    best_accuracy = max([r["accuracy"] for r in result_rows], default=0)

    for index, item in enumerate(result_rows, start=1):
        badges = []
        if index == 1 and item["total_points"] > 0:
            badges.append("Top Predictor")
        if item["accuracy"] == best_accuracy and item["predictions_count"] > 0:
            badges.append("Best Accuracy")
        if item["exact_matches"] >= 3:
            badges.append("Champion Predictor")

        _upsert_leaderboard(db, item, season, index, badges)

        item["rank"] = index
        item["badges"] = badges

    return result_rows


def complete_match_workflow(db, match_id: int) -> dict:
    count = calculate_match_points(db, match_id)
    reports = export_reports(db, season="2026")
    match = _safe_fetchone(
        db.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
    )
    users = _safe_fetchall(
        db.execute("SELECT id, email FROM users WHERE is_active = 1")
    )
    for user in users:
        queue_notification(
            db,
            user["email"],
            "Match result published",
            f"Game {match.get('game_no') or match_id} is final. Reports are ready.",
            user["id"]
        )
    return {"scored_predictions": count, "reports": reports}
