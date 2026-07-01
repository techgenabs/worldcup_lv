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

def get_all_match_predictions(home_team: str = None, away_team: str = None, match_id: int = None) -> dict:
    """
    Fetch match metadata and all participant predictions from the SQLite DB.
    Can query by match_id (preferred) or by home/away team names.
    """
    db_path = "worldcup_ai.db"
    result_data = {
        "match":       "Unknown vs Unknown",
        "game_no":     "—",
        "round":       "—",
        "date_npt":    "—",
        "venue":       "—",
        "final_score": "Pending",
        "predictions": [],
    }

    if not os.path.exists(db_path):
        return result_data

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = lambda cursor, row: {
            col[0]: row[idx] for idx, col in enumerate(cursor.description)
        }

        if match_id:
            where_clause = "m.id = ?"
            params = (match_id,)
        else:
            where_clause = "ht.name LIKE ? AND at.name LIKE ?"
            params = (f"%{home_team}%", f"%{away_team}%")

        query = f"""
            SELECT
                u.name            AS player_name,
                u.email           AS player_email,
                p.predicted_home_score,
                p.predicted_away_score,
                p.points_awarded,
                p.scoring_reason,
                m.id              AS match_id,
                m.game_no,
                m.round,
                m.match_date,
                m.stadium,
                m.status          AS match_status,
                m.home_score,
                m.away_score,
                ht.name           AS home_team,
                at.name           AS away_team
            FROM predictions p
            JOIN users   u  ON p.user_id    = u.id
            JOIN matches m  ON p.match_id   = m.id
            JOIN teams   ht ON m.home_team_id = ht.id
            JOIN teams   at ON m.away_team_id = at.id
            WHERE {where_clause}
        """

        rows = conn.execute(query, params).fetchall()

        if rows:
            first = rows[0]
            home = first.get("home_team") or "?"
            away = first.get("away_team") or "?"
            result_data["match"]    = f"{home} vs {away}"
            result_data["game_no"]  = first.get("game_no")   or "—"
            result_data["round"]    = first.get("round")     or "—"
            result_data["date_npt"] = first.get("match_date") or "—"
            result_data["venue"]    = first.get("stadium")   or "—"

            h_score = first.get("home_score")
            a_score = first.get("away_score")
            status  = (first.get("match_status") or "").lower()
            if status == "completed" and h_score is not None and a_score is not None:
                result_data["final_score"] = f"{h_score}-{a_score}"

            for r in rows:
                ph = r.get("predicted_home_score")
                pa = r.get("predicted_away_score")
                predicted    = f"{ph}-{pa}" if ph is not None and pa is not None else "—"
                points       = r.get("points_awarded") or 0
                reason       = (r.get("scoring_reason") or "").lower().replace("_", " ")
                match_status = (r.get("match_status") or "").lower()

                # Derive outcome label — mirrors the JSX OutcomePill logic
                if match_status in ("scheduled", "live", "pending") or not reason:
                    outcome = "Pending"
                elif "exact" in reason:
                    outcome = "Exact Score"
                elif "correct" in reason or "winner" in reason:
                    outcome = "Correct Winner"
                else:
                    outcome = "Wrong Prediction"

                result_data["predictions"].append({
                    "name":        r.get("player_name") or "User",
                    "email":       r.get("player_email") or "",
                    "predicted":   predicted,
                    "final_score": result_data["final_score"],
                    "outcome":     outcome,
                    "points":      int(points),
                })

        conn.close()
    except Exception:
        pass

    return result_data


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
