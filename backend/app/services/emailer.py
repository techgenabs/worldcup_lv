import smtplib
import re
from email.message import EmailMessage
from pathlib import Path

from ..config import settings


def queue_notification(db, recipient: str, subject: str, body: str, user_id: int | None = None) -> int:
    cur = db.execute(
        "INSERT INTO notifications (user_id, recipient, subject, body) VALUES (?, ?, ?, ?)",
        (user_id, recipient, subject, body),
    )
    return int(cur.lastrowid)


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

    # 1. BULLETPROOF DETECTION: If the text contains "vs" or "VS", it's a match notification!
    is_match_prediction = "vs" in body.lower()
    all_participants_block = ""

    if is_match_prediction:
        home_team, away_team = "Unknown", "Unknown"
        
        # Look for any line containing "vs" or "VS" to find the teams
        for line in body.splitlines():
            if "vs" in line.lower():
                # Clean up prefixes like "Match:" or "Hi absingh," if they are on the same line
                clean_line = re.sub(r"(match|hi|hello|dear)[^:]*:\s*", "", line, flags=re.IGNORECASE)
                # Clean up trailing notes like "(result pending)"
                clean_line = clean_line.split("(")[0].split("result")[0].strip()
                
                if "vs" in clean_line.lower():
                    # Split by "vs" ignoring case
                    teams = re.split(r"\s+vs\s+", clean_line, flags=re.IGNORECASE)
                    if len(teams) >= 2:
                        home_team = teams[0].strip()
                        away_team = teams[1].strip()
                        break

        # Fetch all participant prediction entries for this specific fixture from the DB
        all_predictions = []
        if home_team != "Unknown":
            try:
                cursor = db.execute(
                    """
                    SELECT p.user_name, p.predicted_score, p.status 
                    FROM predictions p
                    JOIN games g ON p.game_id = g.id
                    WHERE (g.home_team LIKE ? AND g.away_team LIKE ?)
                       OR (g.match_name LIKE ? AND g.match_name LIKE ?)
                    """,
                    (f"%{home_team}%", f"%{away_team}%", f"%{home_team}%", f"%{away_team}%")
                )
                rows = cursor.fetchall()
                for r in rows:
                    all_predictions.append({"name": r[0], "pred": r[1], "status": r[2] or "pending"})
            except Exception:
                pass

        # Fallback: If database fails or is empty, use the active recipient queue data
        if not all_predictions:
            for u in users:
                p_name = u.get("username") or u.get("name") or u.get("email", "Participant").split("@")[0]
                p_pred = u.get("predicted_score") or u.get("prediction") or "—"
                all_predictions.append({"name": p_name, "pred": p_pred, "status": "pending"})

        # Construct the cleanly aligned plain-text matrix grid table
        pred_lines = [
            "",
            f"All Predictions ({len(all_predictions)} participants):",
            ""
        ]
        for p in all_predictions:
            name = p['name']
            if len(name) > 22:
                name = name[:21] + "…"
            pred_lines.append(f"  • {name:<22}   {p['pred']:<6}   {p['status'].lower()}")
        
        all_participants_block = "\n".join(pred_lines)

    # 2. DISPATCH EMAIL QUEUE LOOPS
    for user in users:
        try:
            current_email = user["email"]
            
            if is_match_prediction:
                user_name = user.get("username") or user.get("name") or current_email.split("@")[0]
                
                # Assemble the finalized dynamic template matching your exact text pattern
                final_body = f"""Hi {user_name},

{body.strip()}

{all_participants_block}

Good luck!
WorldCup Prediction Team"""
            else:
                # If this is your financial/payment report overview, keep it exactly as-is!
                final_body = body

            status = send_email(current_email, subject, final_body, attachments)
            queue_notification(db, current_email, subject, final_body, user.get("id"))
            
            db.execute(
                "UPDATE notifications SET status = ? WHERE id = (SELECT MAX(id) FROM notifications WHERE recipient = ?)",
                (status, current_email),
            )
            if status == "sent":
                sent += 1
            else:
                skipped += 1
            details.append({"email": current_email, "status": status})
        except Exception as exc:
            failed += 1
            details.append({"email": user.get("email", "unknown"), "status": "failed", "error": str(exc)})
            
    return {"sent": sent, "skipped": skipped, "failed": failed, "details": details}
