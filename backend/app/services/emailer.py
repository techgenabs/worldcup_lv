import smtplib
import os
import sqlite3
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from ..config import settings


# ─────────────────────────────────────────────────────────────────────────────
#  DATABASE — fetch full match + all participants' predictions
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
#  NOTIFY MATCH PARTICIPANTS  — uses the FastAPI db connection (PostgreSQL)
# ─────────────────────────────────────────────────────────────────────────────

def notify_match_participants(db, match_id: int) -> dict:
    """
    Uses the passed-in db (PostgreSQL via FastAPI get_db) — no separate
    SQLite connection needed. Sends full HTML group summary to every participant.
    """
    from ..database import rows, row

    # Fetch match metadata
    match = row(db.execute("""
        SELECT m.*, ht.name AS home_team, at.name AS away_team
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        WHERE m.id = ?
    """, (match_id,)))

    if not match:
        return {"sent": 0, "skipped": 0, "failed": 0,
                "details": [], "note": "Match not found."}

    # Fetch all predictions for this match
    participants = rows(db.execute("""
        SELECT u.name AS player_name, u.email AS player_email,
               p.predicted_home_score, p.predicted_away_score,
               p.points_awarded, p.scoring_reason
        FROM predictions p
        JOIN users u ON u.id = p.user_id
        WHERE p.match_id = ?
    """, (match_id,)))

    if not participants:
        return {"sent": 0, "skipped": 0, "failed": 0,
                "details": [], "note": "No predictions found for this match."}

    # Build match data dict
    home = match.get("home_team") or "?"
    away = match.get("away_team") or "?"
    h_score = match.get("home_score")
    a_score = match.get("away_score")
    status  = (match.get("status") or "").lower()
    final_score = f"{h_score}-{a_score}" if status == "completed" and h_score is not None and a_score is not None else "Pending"

    predictions = []
    for p in participants:
        ph = p.get("predicted_home_score")
        pa = p.get("predicted_away_score")
        predicted = f"{ph}-{pa}" if ph is not None and pa is not None else "—"
        points    = p.get("points_awarded") or 0
        reason    = (p.get("scoring_reason") or "").lower().replace("_", " ")

        if not reason or status in ("scheduled", "live", "pending"):
            outcome = "Pending"
        elif "exact" in reason:
            outcome = "Exact Score"
        elif "correct" in reason or "winner" in reason:
            outcome = "Correct Winner"
        else:
            outcome = "Wrong Prediction"

        predictions.append({
            "name":        p.get("player_name") or "User",
            "email":       p.get("player_email") or "",
            "predicted":   predicted,
            "final_score": final_score,
            "outcome":     outcome,
            "points":      int(points),
        })

    match_data = {
        "match":       f"{home} vs {away}",
        "game_no":     match.get("game_no") or "—",
        "round":       match.get("round")   or "—",
        "date_npt":    match.get("match_date") or "—",
        "venue":       match.get("stadium") or "—",
        "final_score": final_score,
        "predictions": predictions,
    }

    html_body  = build_email_html(match_data)
    subject    = f"Prediction Results: {match_data['match']} ({match_data['game_no']})"
    plain_body = (
        f"Match: {match_data['match']}\n"
        f"Game No: {match_data['game_no']} | Round: {match_data['round']}\n"
        f"Date: {match_data['date_npt']}\n"
        f"Venue: {match_data['venue']}\n\n"
        f"Total Predictions: {len(predictions)}\n"
        f"Total Winners (Exact Score): {sum(1 for p in predictions if p['outcome'] == 'Exact Score')}\n\n"
        "Open this email in an HTML-capable client to see the full table.\n"
        "Leaderboard: https://worldcup-lv.onrender.com"
    )

    sent = skipped = failed = 0
    details = []

    for pred in predictions:
        email = (pred.get("email") or "").strip()
        if not email or "@" not in email:
            failed += 1
            details.append({"email": email or "—", "status": "failed",
                             "error": "invalid or missing email"})
            continue
        try:
            status_result = send_email(email, subject, plain_body, html_body=html_body)
            queue_notification(db, email, subject, plain_body, user_id=None)
            try:
                db.execute(
                    "UPDATE notifications SET status = ? "
                    "WHERE id = (SELECT MAX(id) FROM notifications WHERE recipient = ?)",
                    (status_result, email),
                )
            except Exception:
                pass
            if status_result == "sent":
                sent += 1
            else:
                skipped += 1
            details.append({"email": email, "status": status_result})
        except Exception as exc:
            failed += 1
            details.append({"email": email, "status": "failed", "error": str(exc)})

    return {"sent": sent, "skipped": skipped, "failed": failed, "details": details}


# ─────────────────────────────────────────────────────────────────────────────
#  HTML EMAIL BUILDER  — same format as send_predictions.py
# ─────────────────────────────────────────────────────────────────────────────

def build_email_html(match_data: dict) -> str:
    match       = match_data["match"]
    game_no     = match_data["game_no"]
    round_name  = match_data["round"]
    date_npt    = match_data["date_npt"]
    venue       = match_data["venue"]
    predictions = match_data["predictions"]

    # Sort: Exact Score first, then rest
    exact_rows = [p for p in predictions if p["outcome"] == "Exact Score"]
    other_rows = [p for p in predictions if p["outcome"] != "Exact Score"]
    sorted_preds = exact_rows + other_rows

    total_predictions = len(sorted_preds)
    total_winners     = len(exact_rows)

    rows_html = ""
    for p in sorted_preds:
        is_exact = p["outcome"] == "Exact Score"
        if is_exact:
            row_style      = "background:#fffbe6;"
            points_display = "10 🏆"
            outcome_html   = f'<b style="color:#b8860b;">Exact Score</b>'
        else:
            row_style      = ""
            points_display = "—"
            outcome_html   = p["outcome"]

        rows_html += f"""
        <tr style="{row_style}">
          <td style="padding:8px;border:1px solid #ddd;">{p['name']}</td>
          <td style="padding:8px;border:1px solid #ddd;">{p['predicted']}</td>
          <td style="padding:8px;border:1px solid #ddd;">{p['final_score']}</td>
          <td style="padding:8px;border:1px solid #ddd;">{outcome_html}</td>
          <td style="padding:8px;border:1px solid #ddd;text-align:center;">{points_display}</td>
        </tr>"""

    html = f"""
    <html>
    <body style="font-family:Arial,sans-serif;color:#222;">
      <h2 style="margin-bottom:4px;">{match}</h2>
      <p style="margin:2px 0;color:#555;">
        <b>Game No:</b> {game_no} &nbsp;|&nbsp;
        <b>Round:</b> {round_name}<br/>
        <b>Date:</b> {date_npt}<br/>
        <b>Venue:</b> {venue}
      </p>
      <table style="border-collapse:collapse;margin-top:10px;width:auto;">
        <tr>
          <td style="padding:6px 20px 6px 0;font-size:14px;">
            📊 <b>Total Predictions:</b> {total_predictions}
          </td>
          <td style="padding:6px 0;font-size:14px;">
            🏆 <b>Total Winners (Exact Score):</b> {total_winners}
          </td>
        </tr>
      </table>
      <table style="border-collapse:collapse;margin-top:10px;width:100%;max-width:620px;">
        <thead>
          <tr style="background:#1a3c6e;color:#fff;">
            <th style="padding:8px;border:1px solid #ddd;text-align:left;">Player</th>
            <th style="padding:8px;border:1px solid #ddd;text-align:left;">Predicted</th>
            <th style="padding:8px;border:1px solid #ddd;text-align:left;">Final Score</th>
            <th style="padding:8px;border:1px solid #ddd;text-align:left;">Outcome</th>
            <th style="padding:8px;border:1px solid #ddd;text-align:center;">Points</th>
          </tr>
        </thead>
        <tbody>{rows_html}
        </tbody>
      </table>
      <p style="margin-top:16px;">
        📊 Check the leaderboard live:
        <a href="https://worldcup-lv.onrender.com">worldcup-lv.onrender.com</a>
      </p>
      <p style="margin-top:4px;color:#888;font-size:12px;">
        WorldCup LV Predictions — Automated result mailer
      </p>
    </body>
    </html>
    """
    return html


# ─────────────────────────────────────────────────────────────────────────────
#  SEND — single email (HTML)
# ─────────────────────────────────────────────────────────────────────────────

def send_email(recipient: str, subject: str, body: str,
               attachments: list[str] | None = None,
               html_body: str | None = None) -> str:
    if not settings.enable_email:
        return "skipped"

    if html_body:
        msg = MIMEMultipart("alternative")
        msg["From"]    = settings.smtp_from
        msg["To"]      = recipient
        msg["Subject"] = subject
        msg.attach(MIMEText(body,      "plain"))
        msg.attach(MIMEText(html_body, "html"))
    else:
        msg = EmailMessage()
        msg["From"]    = settings.smtp_from
        msg["To"]      = recipient
        msg["Subject"] = subject
        msg.set_content(body)

    for item in attachments or []:
        path = Path(item)
        if not path.exists():
            continue
        if hasattr(msg, "add_attachment"):
            msg.add_attachment(
                path.read_bytes(),
                maintype="application",
                subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename=path.name,
            )

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
        smtp.starttls()
        smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)
    return "sent"


# ─────────────────────────────────────────────────────────────────────────────
#  QUEUE helper (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

def queue_notification(db, recipient: str, subject: str, body: str,
                       user_id: int | None = None) -> int:
    try:
        cur = db.execute(
            "INSERT INTO notifications (user_id, recipient, subject, body) VALUES (?, ?, ?, ?)",
            (user_id, recipient, subject, body),
        )
        return int(cur.lastrowid)
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────────────────────
#  NOTIFY MATCH PARTICIPANTS  — called by /admin/notify/match-participants/{id}
#  Sends the full group HTML summary to every participant of that match.
# ─────────────────────────────────────────────────────────────────────────────

def notify_match_participants(db, match_id: int) -> dict:
    """
    Called by the 'Send Email' button per match in the admin panel.
    Fetches all predictions for that match and emails the full HTML
    group summary (identical format to send_predictions.py) to every
    participant.
    """
    match_data  = get_all_match_predictions(match_id=match_id)
    predictions = match_data["predictions"]

    if not predictions:
        return {"sent": 0, "skipped": 0, "failed": 0,
                "details": [], "note": "No predictions found for this match."}

    html_body   = build_email_html(match_data)
    subject     = f"Prediction Results: {match_data['match']} ({match_data['game_no']})"
    plain_body  = (
        f"Match: {match_data['match']}\n"
        f"Game No: {match_data['game_no']} | Round: {match_data['round']}\n"
        f"Date: {match_data['date_npt']}\n"
        f"Venue: {match_data['venue']}\n\n"
        f"Total Predictions: {len(predictions)}\n"
        f"Total Winners (Exact Score): {sum(1 for p in predictions if p['outcome'] == 'Exact Score')}\n\n"
        "Open this email in an HTML-capable client to see the full formatted table.\n"
        "Leaderboard: https://worldcup-lv.onrender.com"
    )

    sent = skipped = failed = 0
    details = []

    for pred in predictions:
        email = (pred.get("email") or "").strip()
        if not email or "@" not in email:
            failed += 1
            details.append({"email": email or "—", "status": "failed",
                             "error": "invalid or missing email"})
            continue

        try:
            status = send_email(email, subject, plain_body, html_body=html_body)
            queue_notification(db, email, subject, plain_body,
                               user_id=None)
            try:
                db.execute(
                    "UPDATE notifications SET status = ? "
                    "WHERE id = (SELECT MAX(id) FROM notifications WHERE recipient = ?)",
                    (status, email),
                )
            except Exception:
                pass

            if status == "sent":
                sent += 1
            else:
                skipped += 1
            details.append({"email": email, "status": status})

        except Exception as exc:
            failed += 1
            details.append({"email": email, "status": "failed", "error": str(exc)})

    return {"sent": sent, "skipped": skipped, "failed": failed, "details": details}


# ─────────────────────────────────────────────────────────────────────────────
#  SEND REPORT EMAILS  — called by /admin/email-reports (bulk report mailer)
#  Kept working exactly as before for the Reports tab.
# ─────────────────────────────────────────────────────────────────────────────

def send_report_emails(db, users: list[dict], subject: str, body: str,
                       attachments: list[str]) -> dict:
    sent = skipped = failed = 0
    details = []

    for user in users:
        try:
            email     = user["email"]
            user_name = user.get("name") or user.get("username") or email.split("@")[0]
            final_body = f"Hi {user_name},\n\n{body}\n\nWorldCup Prediction Team"

            status = send_email(email, subject, final_body, attachments)
            queue_notification(db, email, subject, final_body, user.get("id"))

            try:
                db.execute(
                    "UPDATE notifications SET status = ? "
                    "WHERE id = (SELECT MAX(id) FROM notifications WHERE recipient = ?)",
                    (status, email),
                )
            except Exception:
                pass

            if status == "sent":
                sent += 1
            else:
                skipped += 1
            details.append({"email": email, "status": status})

        except Exception as exc:
            failed += 1
            details.append({"email": user.get("email", "unknown"),
                             "status": "failed", "error": str(exc)})

    return {"sent": sent, "skipped": skipped, "failed": failed, "details": details}
