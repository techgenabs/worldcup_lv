import smtplib
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


def send_report_emails(db, users: list[dict], subject: str, match_info: dict, all_predictions: list[dict], attachments: list[str]) -> dict:
    """
    Sends beautifully structured, highly readable text emails to all participants.
    
    match_info: {'home_team', 'away_team', 'game_no', 'round', 'venue', 'kickoff', 'final_score'}
    all_predictions: list of {'player_name', 'prediction', 'status'}
    """
    sent = skipped = failed = 0
    details = []
    
    # 1. Generate a perfectly aligned grid for All Predictions
    # Header row for the participant table block
    pred_lines = [
        f"{'Participant Name':<24} | {'Predict':<7} | {'Status':<10}",
        f"{'-'*24}-+-{'-'*7}-+-{'-'*10}"
    ]
    
    for p in all_predictions:
        name = p.get('player_name', 'Unknown')
        pred = p.get('prediction', '—')
        status = p.get('status', 'pending').lower()
        
        # Truncate long names slightly to keep the mobile line-wraps clean
        if len(name) > 22:
            name = name[:21] + "…"
            
        pred_lines.append(f"• {name:<22} | {pred:<7} | {status:<10}")
    
    all_participants_block = "\n".join(pred_lines)

    # 2. Loop through and send out to individual users
    for user in users:
        try:
            user_pred = user.get("predicted_score", "—")
            user_pts  = user.get("points_awarded", 0)
            score_status = user.get("prediction_status", "Pending").capitalize()

            # Beautiful, clean text template with clear visual grouping
            personalized_body = f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏆 WORLD CUP 2026 — PREDICTION STATUS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Match: {match_info['home_team']} vs {match_info['away_team']} ({score_status.lower()})

📊 YOUR ENTRY DETAILS:
──────────────────────────────────────────────────
  • Your Prediction : {user_pred}
  • Points Awarded  : {user_pts}
  • Match Result    : {score_status}

📍 FIXTURE DETAILS:
──────────────────────────────────────────────────
  • Game No   : {match_info.get('game_no', 'N/A')} ({match_info.get('round', 'N/A')})
  • Venue     : {match_info.get('venue', 'N/A')}
  • Kickoff   : {match_info.get('kickoff', 'N/A')}
  • Score     : {match_info.get('final_score', 'Pending')}

👥 TOURNAMENT MATRIX ({len(all_predictions)} Participants):
──────────────────────────────────────────────────
{all_participants_block}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Thank you for participating! Good luck with your picks.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

            # 3. Handle processing and execution tracking
            status = send_email(user["email"], subject, personalized_body, attachments)
            queue_notification(db, user["email"], subject, personalized_body, user["id"])
            
            db.execute(
                "UPDATE notifications SET status = ? WHERE id = (SELECT MAX(id) FROM notifications WHERE recipient = ?)",
                (status, user["email"]),
            )
            if status == "sent":
                sent += 1
            else:
                skipped += 1
            details.append({"email": user["email"], "status": status})
            
        except Exception as exc:
            failed += 1
            details.append({"email": user["email"], "status": "failed", "error": str(exc)})
            
    return {"sent": sent, "skipped": skipped, "failed": failed, "details": details}
