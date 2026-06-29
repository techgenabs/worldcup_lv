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
    """
    Intercepts the generic single-user body text, extracts the active match names,
    queries the database for ALL participant predictions for that specific game,
    and builds a beautiful, complete summary matrix for every recipient.
    """
    sent = skipped = failed = 0
    details = []

    # 1. PARSE THE MATCH DETAILS DIRECTLY FROM THE INCOMING BODY STRING
    home_team = "Unknown"
    away_team = "Unknown"
    
    # Extract match line (e.g., "Match: Netherlands vs Morocco (result pending)")
    match_line_match = re.search(r"Match:\s*([^\n\(\\r]+)", body, re.IGNORECASE)
    if match_line_match:
        match_text = match_line_match.group(1).strip()
        if "vs" in match_text:
            teams = match_text.split("vs")
            home_team = teams[0].strip()
            away_team = teams[1].strip()

    # 2. QUERY THE DATABASE TO FIND ALL PARTICIPANTS FOR THIS EXACT MATCH
    all_predictions = []
    game_no = "N/A"
    venue = "Tournament Stadium"
    kickoff = "Scheduled"
    final_score = "Pending"

    try:
        # Pulls every prediction line associated with either the game tokens or the teams
        cursor = db.execute(
            """
            SELECT p.user_name, p.predicted_score, p.status, g.game_no, g.venue, g.kickoff, g.final_score
            FROM predictions p
            JOIN games g ON p.game_id = g.id
            WHERE (g.home_team LIKE ? AND g.away_team LIKE ?)
               OR (g.match_name LIKE ? AND g.match_name LIKE ?)
            """,
            (f"%{home_team}%", f"%{away_team}%", f"%{home_team}%", f"%{away_team}%")
        )
        rows = cursor.fetchall()
        
        if rows:
            game_no = rows[0][3] or game_no
            venue = rows[0][4] or venue
            kickoff = rows[0][5] or kickoff
            final_score = rows[0][6] or final_score
            
            for r in rows:
                all_predictions.append({
                    "player_name": r[0],
                    "prediction": r[1],
                    "status": r[2] or "pending"
                })
    except Exception as db_err:
        print(f"Database lookup skipped or modified: {db_err}")

    # Fallback: If your schema differs, dynamically build the grid from the live users list
    if not all_predictions:
        for u in users:
            # Try to grab username/name or clean the email prefix
            p_name = u.get("username") or u.get("name") or u.get("email", "Participant").split("@")[0]
            p_pred = u.get("predicted_score") or u.get("prediction", "—")
            all_predictions.append({
                "player_name": p_name,
                "prediction": p_pred,
                "status": "pending"
            })

    # 3. CONSTRUCT A BALANCED, CLEAN ALIGNED TEXT GRID FOR MOBILE/DESKTOP
    pred_lines = [
        f"{'Participant Name':<24} | {'Predict':<7} | {'Status':<10}",
        f"{'-'*24}-+-{'-'*7}-+-{'-'*10}"
    ]
    for p in all_predictions:
        name = p['player_name']
        if len(name) > 22:
            name = name[:21] + "…"
        pred_lines.append(f"• {name:<22} | {p['prediction']:<7} | {p['status'].lower():<10}")
    
    all_participants_block = "\n".join(pred_lines)

    # 4. LOOP AND DISPATCH PERSONALIZED OVERVIEWS TO EACH PARTICIPANT
    for user in users:
        try:
            # Extract user attributes or apply safe fallbacks
            user_email = user.get("email")
            user_name = user.get("username") or user.get("name") or user_email.split("@")[0]
            user_pred = user.get("predicted_score") or user.get("prediction", "—")
            user_pts  = user.get("points_awarded", 0)
            score_status = "Pending"

            # Re-compile into the clean, multi-participant block format
            personalized_body = f"""Hi {user_name},

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏆 WORLD CUP 2026 — PREDICTION STATUS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Match: {home_team} vs {away_team} ({score_status.lower()})

📊 YOUR ENTRY DETAILS:
──────────────────────────────────────────────────
  • Your Prediction : {user_pred}
  • Points Awarded  : {user_pts}
  • Match Result    : {score_status}

📍 FIXTURE DETAILS:
──────────────────────────────────────────────────
  • Game No   : {game_no}
  • Venue     : {venue}
  • Kickoff   : {kickoff}
  • Score     : {final_score}

👥 TOURNAMENT MATRIX ({len(all_predictions)} Participants):
──────────────────────────────────────────────────
{all_participants_block}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Check the leaderboard live: https://worldcup-lv.onrender.com

Good luck!
WorldCup Prediction Team
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

            # 5. Send message and log notification state history
            status = send_email(user_email, subject, personalized_body, attachments)
            queue_notification(db, user_email, subject, personalized_body, user.get("id"))
            
            db.execute(
                "UPDATE notifications SET status = ? WHERE id = (SELECT MAX(id) FROM notifications WHERE recipient = ?)",
                (status, user_email),
            )
            if status == "sent":
                sent += 1
            else:
                skipped += 1
            details.append({"email": user_email, "status": status})
            
        except Exception as exc:
            failed += 1
            details.append({"email": user.get("email"), "status": "failed", "error": str(exc)})
            
    return {"sent": sent, "skipped": skipped, "failed": failed, "details": details}
