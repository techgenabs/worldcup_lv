from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..config import settings
from ..database import rows, row
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

    # ── STRICT RULE (current): ONLY an exact score match earns points. ──
    # Anything else — correct winner, correct draw, correct goal difference,
    # or a wrong prediction — scores ZERO. This matches the finance rule:
    # if nobody hits the exact score, the game is cancelled and no fee is
    # collected, so partial credit here would contradict that rule.
    if exact:
        base, reason = 10, "Exact Score"
    else:
        base, reason = 0, "Wrong Prediction"

    # ── OLD TIERED RULE (kept for reference / easy revert) ──────────────────
    # Uncomment the block below and remove the strict if/else above to
    # restore partial credit for correct winner / correct draw / goal
    # difference predictions instead of all-or-nothing scoring.
    #
    # if exact:
    #     base, reason = 10, "Exact Score"
    # elif pred_delta == actual_delta and pred_delta != 0:
    #     base, reason = 7, "Correct Winner + Goal Difference"
    # elif pred_delta == 0 and actual_delta == 0:
    #     base, reason = 5, "Correct Draw"
    # elif (pred_delta > 0 and actual_delta > 0) or (pred_delta < 0 and actual_delta < 0):
    #     base, reason = 5, "Correct Winner"
    # else:
    #     base, reason = 0, "Wrong Prediction"

    # ── Confidence bonus DISABLED ─────────────────────────────────────────────
    # Previously, "High Confidence" predictions earned +2 bonus points, which
    # would make an exact-score prediction worth 12 instead of 10. Since the
    # finance/winner rule checks for points == 10 exactly, the bonus is now
    # turned off so an Exact Score is always exactly 10 points, never more.
    # To re-enable: uncomment the two lines below.
    #
    # bonus = confidence_bonus(prediction.get("confidence_level"), base)
    # if bonus:
    #     reason = f"{reason} + High Confidence Bonus"
    bonus = 0

    return base + bonus, 1 if base else 0, reason


def lock_due_predictions(db) -> int:
    """
    Uses the standard db.execute(sql, params) interface — works on both
    SQLite and Postgres because PgWrapper auto-converts '?' placeholders
    and dict-row access transparently. Do NOT bypass this with raw
    psycopg2 cursors; it breaks the abstraction database.py provides.
    """
    reopen = rows(db.execute(
        "SELECT * FROM matches WHERE status = 'locked' AND match_date IS NOT NULL AND predictions_open = 1"
    ))

    for match in reopen:
        deadline = lock_deadline(match)
        if deadline and datetime.now(deadline.tzinfo) < deadline:
            db.execute(
                "UPDATE matches SET status = 'scheduled', locked_at = NULL WHERE id = ?",
                (match["id"],)
            )
            db.execute(
                "UPDATE predictions SET locked_at = NULL WHERE match_id = ? AND scored_at IS NULL",
                (match["id"],)
            )

    matches = rows(db.execute(
        "SELECT * FROM matches WHERE status = 'scheduled' AND match_date IS NOT NULL"
    ))

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
        users = rows(db.execute(
            """
            SELECT u.id, u.email, p.predicted_home_score, p.predicted_away_score
            FROM predictions p
            JOIN users u ON u.id = p.user_id
            WHERE p.match_id = ?
            """,
            (match["id"],)
        ))
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
    match = row(db.execute("SELECT * FROM matches WHERE id = ?", (match_id,)))
    predictions = rows(db.execute("SELECT * FROM predictions WHERE match_id = ?", (match_id,)))

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
            (points, correct, reason, timestamp, prediction["id"])
        )
        updated += 1

    # Scope leaderboard rebuild to just this match's tournament, so completing
    # a game in one tournament doesn't recompute/mix rankings across tournaments.
    update_leaderboard(db, tournament_id=match.get("tournament_id"))
    return updated


def update_leaderboard(db, season: str = "2026", tournament_id: int | None = None) -> list[dict]:
    """
    Rebuilds the leaderboard. If tournament_id is given, only points scored
    on matches belonging to that tournament are counted — this is what makes
    the leaderboard correctly separate "FIFA World Cup 2026" from any other
    tournament (e.g. "my test fifa") instead of mixing everyone's totals.
    """
    users = rows(db.execute(
        "SELECT id, name FROM users WHERE role = ? AND is_active = ?",
        ("user", 1)
    ))

    result_rows = []
    for user in users:
        if tournament_id:
            stats = row(db.execute(
                """
                SELECT COALESCE(SUM(p.points_awarded), 0) AS total_points,
                       SUM(CASE WHEN p.scoring_reason LIKE ? THEN 1 ELSE 0 END) AS exact_matches,
                       SUM(CASE WHEN p.is_correct = 1 THEN 1 ELSE 0 END) AS winner_count,
                       COUNT(*) AS predictions_count
                FROM predictions p
                JOIN matches m ON m.id = p.match_id
                WHERE p.user_id = ? AND p.scored_at IS NOT NULL AND m.tournament_id = ?
                """,
                ("Exact Score%", user["id"], tournament_id)
            )) or {}
        else:
            stats = row(db.execute(
                """
                SELECT COALESCE(SUM(points_awarded), 0) AS total_points,
                       SUM(CASE WHEN scoring_reason LIKE ? THEN 1 ELSE 0 END) AS exact_matches,
                       SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) AS winner_count,
                       COUNT(*) AS predictions_count
                FROM predictions
                WHERE user_id = ? AND scored_at IS NOT NULL
                """,
                ("Exact Score%", user["id"])
            )) or {}

        total_points = int(stats.get("total_points") or 0)
        exact_matches = int(stats.get("exact_matches") or 0)
        winner_count = int(stats.get("winner_count") or 0)
        predictions_count = int(stats.get("predictions_count") or 0)
        accuracy = round((winner_count / predictions_count * 100), 1) if predictions_count else 0

        result_rows.append({
            "user_id": user["id"],
            "name": user["name"],
            "total_points": total_points,
            "exact_matches": exact_matches,
            "winner_count": winner_count,
            "predictions_count": predictions_count,
            "accuracy": accuracy,
        })

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
            )
        )
        item["rank"] = index
        item["badges"] = badges

    return result_rows


def complete_match_workflow(db, match_id: int) -> dict:
    count = calculate_match_points(db, match_id)
    match = row(db.execute("SELECT * FROM matches WHERE id = ?", (match_id,)))

    # Scope the export to just this match's tournament, so completing a game
    # in "my test fifa" doesn't generate reports mixed with "FIFA World Cup 2026".
    reports = export_reports(db, season="2026", tournament_id=match.get("tournament_id"))

    users = rows(db.execute("SELECT id, email FROM users WHERE is_active = ?", (1,)))
    for user in users:
        queue_notification(
            db,
            user["email"],
            "Match result published",
            f"Game {match.get('game_no') or match_id} is final. Reports are ready.",
            user["id"]
        )
    return {"scored_predictions": count, "reports": reports}
