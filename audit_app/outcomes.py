"""Staff audit decisions and follow-up notices."""

import json
import logging

from .database import connect
from .emailer import EmailError, resolve_recipients, send_failure_email, utcnow


LOG = logging.getLogger("audit_app.outcomes")


def record_outcome(config, audit_id, outcome, issues="", sender=None):
    if outcome not in {"passed", "failed"}:
        raise ValueError("Choose passed or failed.")
    issues = issues.strip()
    if outcome == "failed" and not issues:
        raise ValueError("Describe the issues before marking an audit failed.")
    if len(issues) > 5000:
        raise ValueError("Issues must be 5,000 characters or fewer.")
    with connect(config.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        audit = db.execute("SELECT outcome,email_status FROM audits WHERE id=?", (audit_id,)).fetchone()
        if not audit:
            db.rollback()
            raise ValueError("Audit not found.")
        if audit["outcome"]:
            db.rollback()
            raise ValueError("This audit already has a recorded result.")
        if audit["email_status"] != "email_sent":
            db.rollback()
            raise ValueError("The original audit request must be sent before recording its result.")
        db.execute("""UPDATE audits SET outcome=?,outcome_at=?,issues=?,failure_email_status=? WHERE id=?""",
                   (outcome, utcnow(), issues if outcome == "failed" else None,
                    "email_pending" if outcome == "failed" else None, audit_id))
        db.commit()
    if outcome == "failed":
        deliver_failure_notice(config, audit_id, sender=sender)
    return outcome


def deliver_failure_notice(config, audit_id, retry=False, sender=None):
    if not config.test_window_open():
        LOG.info("failure_notice_blocked_by_test_window", extra={"audit_id": audit_id})
        return False
    expected = "email_failed" if retry else "email_pending"
    with connect(config.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        audit = db.execute("SELECT * FROM audits WHERE id=?", (audit_id,)).fetchone()
        if not audit or audit["outcome"] != "failed" or audit["failure_email_status"] != expected:
            db.rollback()
            return False
        if audit["test_mode"] and not config.test_mode:
            db.execute("""UPDATE audits SET failure_email_status='email_blocked',
                failure_last_error='A test audit cannot send a production failed-audit notice.' WHERE id=?""", (audit_id,))
            db.commit()
            return False
        listing = dict(db.execute("SELECT * FROM listings WHERE id=?", (audit["listing_id"],)).fetchone())
        try:
            _, _, actual = resolve_recipients(listing, config)
        except EmailError as exc:
            db.execute("UPDATE audits SET failure_email_status='email_failed',failure_last_error=? WHERE id=?",
                       (str(exc), audit_id))
            db.commit()
            return False
        db.execute("""UPDATE audits SET failure_email_status='email_sending',failure_actual_recipients=?,
            failure_last_error=NULL WHERE id=?""", (json.dumps(actual), audit_id))
        attempt_id = db.execute("""INSERT INTO failure_email_attempts(audit_id,attempted_at,status,actual_recipients)
            VALUES(?,?,?,?)""", (audit_id, utcnow(), "started", json.dumps(actual))).lastrowid
        db.commit()
    try:
        message_id = (sender or send_failure_email)(config, listing, audit_id, audit["issues"])
    except EmailError as exc:
        state = "email_unknown" if exc.uncertain else "email_failed"
        with connect(config.database_path) as db:
            db.execute("UPDATE audits SET failure_email_status=?,failure_last_error=? WHERE id=?",
                       (state, str(exc), audit_id))
            db.execute("""UPDATE failure_email_attempts SET completed_at=?,status=?,error=? WHERE id=?""",
                       (utcnow(), state, str(exc), attempt_id))
            db.commit()
        LOG.warning("failure_notice_send_failed", extra={"audit_id": audit_id, "status": state})
        return False
    with connect(config.database_path) as db:
        db.execute("""UPDATE audits SET failure_email_status='email_sent',failure_email_sent_at=?,
            failure_message_id=? WHERE id=?""", (utcnow(), message_id, audit_id))
        db.execute("""UPDATE failure_email_attempts SET completed_at=?,status='email_sent',
            sendgrid_message_id=? WHERE id=?""", (utcnow(), message_id, attempt_id))
        db.commit()
    LOG.info("failure_notice_sent", extra={"audit_id": audit_id})
    return True
