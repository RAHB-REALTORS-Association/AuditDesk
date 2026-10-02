"""Staff-recorded broker response receipt, separate from audit completion."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .board_scope import require_cornerstone_audit
from .database import connect
from .security import event


def record_response(config, audit_id, raw='', reopen=False, now=None):
    now = now or datetime.now(timezone.utc)
    received = now
    if raw and not reopen:
        try:
            received = datetime.fromisoformat(raw)
            if received.tzinfo is None:
                local = received
                received = local.replace(tzinfo=ZoneInfo(config.timezone))
                # Reject nonexistent or ambiguous local times rather than guessing.
                if received.utcoffset() != local.replace(tzinfo=ZoneInfo(config.timezone), fold=1).utcoffset():
                    raise ValueError()
                if received.astimezone(timezone.utc).astimezone(ZoneInfo(config.timezone)).replace(tzinfo=None) != local:
                    raise ValueError()
            received = received.astimezone(timezone.utc)
        except ValueError:
            raise ValueError('Enter an unambiguous response time in the office timezone.') from None
    with connect(config.database_path) as db:
        if not db.in_transaction:
            db.execute('BEGIN IMMEDIATE')
        require_cornerstone_audit(db, audit_id)
        audit = db.execute('SELECT * FROM audits WHERE id=?', (audit_id,)).fetchone()
        if audit['email_status'] != 'email_sent' or not audit['email_sent_at']:
            raise ValueError('The request must have a confirmed send time before recording a response.')
        if audit['outcome']:
            raise ValueError('This audit already has a recorded result.')
        if reopen:
            if not audit['response_received_at']:
                raise ValueError('No response is recorded.')
        else:
            if audit['response_received_at']:
                raise ValueError('A response is already recorded.')
            sent = datetime.fromisoformat(audit['email_sent_at'].replace('Z', '+00:00'))
            if not sent <= received <= now:
                raise ValueError('Response time must be between the request send time and now.')
        stamp = None if reopen else received.isoformat(timespec='seconds')
        db.execute('UPDATE audits SET response_received_at=? WHERE id=?', (stamp, audit_id))
        event(db, 'audit.response_reopened' if reopen else 'audit.response_received', audit_id, stamp or 'Receipt cleared; response timer resumed')
        db.commit()
