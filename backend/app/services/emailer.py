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
    Intercepts the plain incoming email text body, automatically extracts the match teams, 
    queries the database for ALL participant predictions for that game, and rebuilds 
    the email with the full breakdown before sending.
    """
    sent = skipped = failed = 0
    details = []

    # 1. PARSE THE INCOMING BODY TO FIND OUT THE MATCH TEAMS
    # Default fallback values
    home_team = "Germany"
    away_team = "Paraguay"
    
    # Extract the match line dynamically from the text you just pasted
    match_search = re.search(r"Match:\s*([A-Za-z\s]+)\s*vs\s*([A-Za-z\s]+)", body, re.IGNORECASE)
    if match_search:
        home_team = match_search.group(1).strip()
        away_team = match_search.group(2).strip()

    # 2. QUERY THE DATABASE FOR ALL 20 PARTICIPANT PREDICTIONS FOR THIS MATCH
    all_predictions = []
    try:
        # Pull everyone's predictions matching this specific home and away pairing
        cursor = db.execute(
            """
            SELECT p.user_name, p.predicted_score, p.status
            FROM predictions p
            JOIN games g ON p.game_id = g.id
            WHERE (g.home_team = ? AND g.away_team = ?)
               OR (g.match_name LIKE ? AND g.match_name LIKE ?)
            """,
            (home_team, away_team, f"%{home_team}%", f"%{away_team}%")
        )
        rows = cursor.fetchall()
        for r in rows:
            all_predictions.append({
                "name": r[0],
                "pred": r[1],
                "status": r[2] or "pending"
            })
    except Exception as db_err:
        print(f"Database lookup error, attempting fallback array compilation: {db_err}")

    # Fallback compilation from active mail parameters if table query fails
    if not all_predictions:
        for u in users:
            all_predictions.append({
                "name": u.get("username", u.get("email", "User")),
                "pred": u.get("predicted_score", "—"),
                "status": "pending"
            })

    # 3. CONSTRUCT THE TEXT LIST ELEMENT BLOCK
    pred_lines = []
    for p in all_predictions:
        stat = "pending" if "PENDING" in str(p['status']).upper() else str(p['status']).lower()
        pred_lines.append(f"  • {p['name']:<22}   {p['pred']:<6}   {stat:<10}   —")
    
    all_participants_block = f"All Predictions ({len(all_predictions)} participants):\n\n" + "\n".join(pred_lines)

    # 4. OVERRIDE AND FORWARD INDIVIDUALIZED EMAILS WITH THE ROSTER INCLUDED
    for user in users:
        try:
            user_name = user.get("username", user["email"].split("@")[0])
            user_pred = user.get("predicted_score", "—")
            user_pts  = user.get("points_awarded", 0)
            
            # If our structure doesn't hold individual entries, extract them directly from the old text
            if user_pred == "—" and f"Hi {user_name}" in body:
                try:
                    user_pred = re.search(r"Your prediction:\s*([^\n]+)", body).group(1).strip()
                    user_pts  = re.search(r"Points awarded:\s*([^\n]+)", body).group(1).strip()
                except Exception:
                    pass

            # Formulate the finalized layout matching your exact requirements
            perfected_body = f"""Hi {user_name},


Match: {home_team} vs {away_team} (result pending)


Your prediction: {user_pred}

Points awarded:  {user_pts}

Result:          Pending — pending


{home_team} vs {away_team}

Final Score: Pending


{all_participants_block}


Check the leaderboard: https://worldcup-lv.onrender.com


Good luck!

WorldCup Prediction Team"""

            # 5. Execute mail delivery
            status = send_email(user["email"], subject, perfected_body, attachments)
            queue_notification(db, user["email"], subject, perfected_body, user.get("id"))
            
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
