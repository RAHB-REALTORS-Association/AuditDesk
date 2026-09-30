from .security import event
"""Staff roster and audit assignment stored with the local audit data."""

import sqlite3

from .database import connect
from .emailer import utcnow


def _clean_name(name):
    name = " ".join(name.split())
    if not name or len(name) > 80 or any(ord(char) < 32 for char in name):
        raise ValueError("Enter a name of 1 to 80 characters.")
    return name


def add_reviewer(config, name):
    name = _clean_name(name)
    now = utcnow()
    with connect(config.database_path) as db:
        try:
            db.execute("INSERT INTO audit_reviewers(name,created_at,updated_at) VALUES(?,?,?)", (name, now, now))
            event(db, "reviewer.created", name)
            db.commit()
        except sqlite3.IntegrityError:
            raise ValueError("That name is already on the list. Restore it if it is inactive.") from None


def rename_reviewer(config, reviewer_id, name):
    name = _clean_name(name)
    with connect(config.database_path) as db:
        try:
            cursor = db.execute("UPDATE audit_reviewers SET name=?,updated_at=? WHERE id=?", (name, utcnow(), reviewer_id))
            if not cursor.rowcount:
                raise ValueError("Name not found.")
            db.execute("UPDATE audits SET reviewer_name_snapshot=? WHERE reviewer_id=?", (name, reviewer_id))
            event(db, "reviewer.renamed", reviewer_id, name)
            db.commit()
        except sqlite3.IntegrityError:
            raise ValueError("That name is already on the list.") from None


def set_reviewer_active(config, reviewer_id, active):
    with connect(config.database_path) as db:
        cursor = db.execute("UPDATE audit_reviewers SET active=?,updated_at=? WHERE id=?", (int(active), utcnow(), reviewer_id))
        if not cursor.rowcount:
            raise ValueError("Name not found.")
        event(db, "reviewer.status", reviewer_id, str(active))
        db.commit()


def delete_reviewer(config, reviewer_id):
    with connect(config.database_path) as db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        reviewer = db.execute("SELECT name FROM audit_reviewers WHERE id=?", (reviewer_id,)).fetchone()
        if not reviewer:
            raise ValueError("Name not found.")
        db.execute("""UPDATE audits SET reviewer_name_snapshot=?,reviewer_id=NULL
            WHERE reviewer_id=?""", (reviewer["name"], reviewer_id))
        db.execute("DELETE FROM audit_reviewers WHERE id=?", (reviewer_id,))
        event(db, "reviewer.deleted", reviewer_id, reviewer["name"])
        db.commit()


def assign_reviewer(config, audit_id, reviewer_id):
    with connect(config.database_path) as db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM audits WHERE id=?", (audit_id,)).fetchone():
            raise ValueError("Audit not found.")
        if reviewer_id is not None:
            reviewer = db.execute("SELECT name,active FROM audit_reviewers WHERE id=?", (reviewer_id,)).fetchone()
            if not reviewer or not reviewer["active"]:
                raise ValueError("Choose an active name from the list.")
        db.execute("UPDATE audits SET reviewer_id=?,reviewer_name_snapshot=? WHERE id=?",
                   (reviewer_id, reviewer["name"] if reviewer_id is not None else None, audit_id))
        event(db, "audit.assigned", audit_id, str(reviewer_id))
        db.commit()
