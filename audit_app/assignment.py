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
    with connect(config.database_path) as db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM audits WHERE id=?", (audit_id,)).fetchone():
            raise ValueError("Audit not found.")
        if user_id is not None:
            user = db.execute("SELECT * FROM app_users WHERE id=?", (user_id,)).fetchone()
            if not user or not can_audit(user):
                raise ValueError("Choose an active account with permission to audit.")
        db.execute("UPDATE audits SET assignee_user_id=? WHERE id=?", (user_id, audit_id))
        event(db, "audit.assigned", audit_id, str(user_id))
        db.commit()
