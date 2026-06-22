from .util import to_json


def audit(db, action: str, entity_type: str, entity_id: int | None = None, actor_user_id: int | None = None, detail=None) -> None:
    db.execute(
        "INSERT INTO audit_logs (actor_user_id, action, entity_type, entity_id, detail) VALUES (?, ?, ?, ?, ?)",
        (actor_user_id, action, entity_type, entity_id, to_json(detail or {})),
    )
