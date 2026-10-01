"""Assign audits to active application accounts with auditing permission."""

from .database import connect
from .security import CAPABILITIES, event


def can_audit(user):
    return bool(user["active"] and "audits.result" in CAPABILITIES.get(user["role"], ()))


def eligible_assignees(db):
    return [user for user in db.execute(
        "SELECT * FROM app_users ORDER BY COALESCE(NULLIF(display_name,''),email) COLLATE NOCASE, email")
        if can_audit(user)]


def assign_reviewer(config, audit_id, user_id):
    assign_audits(config, [audit_id], user_id)


def assign_audits(config, audit_ids, user_id):
    if not audit_ids or len(audit_ids) > 200 or len(set(audit_ids)) != len(audit_ids):
        raise ValueError("Select between 1 and 200 different audits.")
    with connect(config.database_path) as db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        placeholders = ",".join("?" for _ in audit_ids)
        count = db.execute(f"SELECT count(*) FROM audits WHERE id IN ({placeholders})", audit_ids).fetchone()[0]
        if count != len(audit_ids):
            raise ValueError("Audit not found. No assignments were changed.")
        if user_id is not None:
            user = db.execute("SELECT * FROM app_users WHERE id=?", (user_id,)).fetchone()
            if not user or not can_audit(user):
                raise ValueError("Choose an active account with permission to audit.")
        db.execute(f"UPDATE audits SET assignee_user_id=? WHERE id IN ({placeholders})", [user_id, *audit_ids])
        for audit_id in audit_ids:
            event(db, "audit.assigned", audit_id, str(user_id))
        db.commit()
