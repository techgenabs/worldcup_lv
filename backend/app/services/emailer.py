import smtplib
import re
from email.message import EmailMessage
from pathlib import Path

from ..config import settings
# Use your app's built-in database context engine
from .database import get_db


def queue_notification(db, recipient: str, subject: str, body: str, user_id: int | None = None) -> int:
    try:
        cur = db.execute(
            "INSERT INTO notifications (user_id, recipient, subject, body) VALUES (?, ?, ?, ?)",
            (user_id, recipient, subject, body),
        )
        return int(cur.lastrowid)
    except Exception:
        return 0


def send_email(recipient: str, subject: str, body: str, attachments: list[str] | None = None) -> str:
    if not settings.enable_email:
        return "skipped"
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)
    for item in attachments or []:
        path = Path(item)
        if not path.exists():
            continue
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


def send_report_emails(db, users: list[dict], subject: str, body: str, attachments: list[str]) -> dict:
    sent = skipped = failed = 0
    details = []

    is_match_prediction = "vs" in body.lower()
    all_participants_block = ""

    if is_match_prediction:
        home_team, away_team = "Unknown", "Unknown"
        
        # Parse clean home/away team strings out of the incoming text body
        for line in body.splitlines():
            if "vs" in line.lower():
                clean_line = re.sub(r"(match|hi|hello|dear)[^:]*:\s*", "", line, flags=re.IGNORECASE)
                clean_line = clean_line.split("(")[0].split("result")[0].strip()
                if "vs" in clean_line.lower():
                    teams = re.split(r"\s+vs\s+", clean_line, flags=re.IGNORECASE)
                    if len(teams) >= 2:
                        home_team = teams[0].strip()
                        away_team = teams[1].strip()
                        break

        all_predictions = []
        
        # Use your native context manager to fetch from worldcup_ai
        with get_db() as active_conn:
            try:
                cursor = active_conn.execute(
                    """
                    SELECT u.name, p.predicted_home_score, p.predicted_away_score
                    FROM predictions p
                    JOIN users u ON p.user_id = u.id
                    JOIN matches m ON p.match_id = m.id
                    JOIN teams ht ON m.home_team_id = ht.id
                    JOIN teams at ON m.away_team_id = at.id
                    WHERE (ht.name LIKE ? AND at.name LIKE ?)
                    """,
                    (f"%{home_team}%", f"%{away_team}%")
                )
                
                rows = cursor.fetchall()
                if rows:
                    for r in rows:
                        # Handle row formatting differences dynamically
                        if isinstance(r, dict):
                            username = r.get("name")
                            h_score = r.get("predicted_home_score")
                            a_score = r.get("predicted_away_score")
                        else:
                            username = r[0]
                            h_score = r[1]
                            a_score = r[2]
                        
                        pred_score = f"{h_score} - {a_score}" if h_score is not None and a_score is not None else "—"
                        all_predictions.append({"name": username, "pred": pred_score})
            except Exception:
                pass

        # Fallback to single loop array data if queries returned empty
        if not all_predictions:
            for u in users:
                p_name = u.get("name") or u.get("username") or u.get("email", "Participant").split("@")[0]
                p_pred = u.get("predicted_score") or u.get("prediction") or "—"
                all_predictions.append({"name": p_name, "pred": p_pred})

        # Generate structural data display table
        if all_predictions:
            pred_lines = [
                "",
                f"All Predictions ({len(all_predictions)} participants):",
                f"{'-'*24}-+-{'-'*7}",
                f"{'Participant Name':<24} | {'Predict':<7}",
                f"{'-'*24}-+-{'-'*7}"
            ]
            for p in all_predictions:
                display_name = p['name'] or "User"
                if len(display_name) > 22:
                    display_name = display_name[:21] + "…"
                pred_lines.append(f"• {display_name:<22} | {p['pred']:<7}")
            pred_lines.append(f"{'-'*24}-+-{'-'*7}")
            all_participants_block = "\n".join(pred_lines)

    # Email Dispatch Loops
    for user in users:
        try:
            current_email = user["email"]
            user_name = user.get("name") or user.get("username") or current_email.split("@")[0]
            
            if is_match_prediction:
                clean_body = body.strip()
                if f"Hi {user_name}" in clean_body:
                    clean_body = clean_body.replace(f"Hi {user_name},", "").strip()
                
                clean_body = clean_body.split("Check the leaderboard:")[0].split("Good luck!")[0].strip()

                final_body = f"""Hi {user_name},

{clean_body}

{all_participants_block}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 Check the leaderboard live: https://worldcup-lv.onrender.com

Good luck!
WorldCup Prediction Team
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
            else:
                final_body = body

            status = send_email(current_email, subject, final_body, attachments)
            queue_notification(db, current_email, subject, final_body, user.get("id"))
            
            try:
                db.execute(
                    "UPDATE notifications SET status = ? WHERE id = (SELECT MAX(id) FROM notifications WHERE recipient = ?)",
                    (status, current_email),
                )
            except Exception:
                pass

            if status == "sent":
                sent += 1
            else:
                skipped += 1
            details.append({"email": current_email, "status": status})
        except Exception as exc:
            failed += 1
            details.append({"email": user.get("email", "unknown"), "status": "failed", "error": str(exc)})
            
    return {"sent": sent, "skipped": skipped, "failed": failed, "details": details}
