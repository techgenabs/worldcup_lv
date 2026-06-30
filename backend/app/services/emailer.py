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


def send_report_emails(db, users: list[dict], subject: str, body: str, attachments: list[str]) -> dict:
    sent = skipped = failed = 0
    details = []
    for user in users:
        try:
            status = send_email(user["email"], subject, body, attachments)
            queue_notification(db, user["email"], subject, body, user["id"])
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
