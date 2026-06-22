import json


DEFAULT_REGISTRATION_REQUIREMENTS = {
    "email_required": True,
    "mobile_required": False,
    "otp_required": False,
}


def get_registration_requirements(db) -> dict:
    row = db.execute("SELECT value FROM app_settings WHERE key = 'registration_requirements'").fetchone()
    if not row:
        return DEFAULT_REGISTRATION_REQUIREMENTS.copy()
    try:
        return {**DEFAULT_REGISTRATION_REQUIREMENTS, **json.loads(row["value"])}
    except json.JSONDecodeError:
        return DEFAULT_REGISTRATION_REQUIREMENTS.copy()


def set_registration_requirements(db, requirements: dict) -> dict:
    normalized = {
        "email_required": bool(requirements.get("email_required")),
        "mobile_required": bool(requirements.get("mobile_required")),
        "otp_required": bool(requirements.get("otp_required")),
    }
    db.execute(
        """
        INSERT INTO app_settings (key, value, updated_at)
        VALUES ('registration_requirements', ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key)
        DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """,
        (json.dumps(normalized),),
    )
    return normalized
