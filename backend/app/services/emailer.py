import smtplib
import re
import os
import sqlite3
from email.message import EmailMessage
from pathlib import Path

from ..config import settings


def get_all_match_predictions(home_team: str, away_team: str) -> dict:
    """
    Connects directly to the live worldcup_ai SQLite database file.
    Gathers detailed match metadata and all corresponding participant predictions.
    """
    db_path = "worldcup_ai.db"
    result_data = {
        "game_no": "—",
        "round": "—",
        "date_npt": "—",
        "venue": "—",
        "predictions": []
    }
    
    if not os.path.exists(db_path):
        return result_data
        
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
        
        # Extended query to fetch Game No, Round, Date, and Venue (Stadium)
        query = """
            SELECT 
                u.name, 
                p.predicted_home_score, 
                p.predicted_away_score,
                p.is_correct,
                p.points_awarded,
                m.game_no,
                m.round,
                m.match_date,
                m.stadium,
                m.status as match_status
            FROM predictions p
            JOIN users u ON p.user_id = u.id
            JOIN matches m ON p.match_id = m.id
            JOIN teams ht ON m.home_team_id = ht.id
            JOIN teams at ON m.away_team_id = at.id
            WHERE (ht.name LIKE ? AND at.name LIKE ?)
        """
        cursor = conn.execute(query, (f"%{home_team}%", f"%{away_team}%"))
        rows = cursor.fetchall()
        
        if rows:
            # Extract match metadata from the first returned row
            first_row = rows[0]
            result_data["game_no"] = first_row.get("game_no") or "—"
            result_data["round"] = first_row.get("round") or "—"
            result_data["date_npt"] = first_row.get("match_date") or "—"
            result_data["venue"] = first_row.get("stadium") or "—"
            
            for r in rows:
                username = r.get("name") or "User"
                h_score = r.get("predicted_home_score")
                a_score = r.get("predicted_away_score")
                is_correct = r.get("is_correct")
                points = r.get("points_awarded") if r.get("points_awarded") is not None else 0
                m_status = r.get("match_status") or "scheduled"
                
                pred_score = f"{h_score}-{a_score}" if h_score is not None and a_score is not None else "—"
                
                if m_status.lower() in ["scheduled", "live", "pending"] or is_correct is None:
                    result_str = "Pending"
                elif is_correct == 1:
                    result_str = "Correct"
                else:
                    result_str = "Incorrect"
                    
                result_data["predictions"].append({
                    "name": username, 
                    "pred": pred_score, 
                    "result": result_str, 
                    "points": str(points)
                })
                
        conn.close()
    except Exception:
        pass
        
    return result_data


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
    match_details_block = ""

    if is_match_prediction:
        home_team, away_team = "Unknown", "Unknown"
        
        # Parse out clean home and away team names from the text body template
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

        # Query the live database for match metadata and individual predictions
        match_data = get_all_match_predictions(home_team, away_team)
        all_predictions = match_data["predictions"]

        # 1. Format the Match Overview Section (Matching your Excel structure)
        match_details_block = f"""Match Details:
• Match: {home_team} vs {away_team}
• Game No: {match_data['game_no']}
• Round: {match_data['round']}
• Date (NPT): {match_data['date_npt']}
• Venue: {match_data['venue']}"""

        # Fallback tracking if database query returns empty arrays
        if not all_predictions:
            for u in users:
                p_name = u.get("name") or u.get("username") or u.get("email", "User").split("@")[0]
                all_predictions.append({
                    "name": p_name, 
                    "pred": "—", 
                    "result": "Pending", 
                    "points": "0"
                })

        # 2. Format the Plaintext Alignment Grid Matrix
        if all_predictions:
            pred_lines = [
                f"All Participant Predictions ({len(all_predictions)}):",
                f"{'-'*20}-+-{'-'*7}-+-{'-'*9}-+-{'-'*6}",
                f"{'Participant Name':<20} | {'Predict':<7} | {'Result':<9} | {'Points':<6}",
                f"{'-'*20}-+-{'-'*7}-+-{'-'*9}-+-{'-'*6}"
            ]
            for p in all_predictions:
                display_name = p['name']
                if len(display_name) > 18:
                    display_name = display_name[:17] + "…"
                pred_lines.append(
                    f"• {display_name:<18} | {p['pred']:<7} | {p['result']:<9} | {p['points']:<6}"
                )
            pred_lines.append(f"{'-'*20}-+-{'-'*7}-+-{'-'*9}-+-{'-'*6}")
            all_participants_block = "\n".join(pred_lines)

    # Core execution distribution loop
    for user in users:
        try:
            current_email = user["email"]
            user_name = user.get("name") or user.get("username") or current_email.split("@")[0]
            
            if is_match_prediction:
                final_body = f"""Hi {user_name},

{match_details_block}

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
