"""Explicit refresh of unsent audit data; never selects audits or sends email."""
import json

from .board_scope import is_eligible_listing
from .bridge import BridgeClient, BridgeError
from .database import connect
from .emailer import EmailError, resolve_recipients
from .job import LISTING_COLUMNS, job_lock
from .security import event

FIELDS = tuple(name for name in LISTING_COLUMNS if name not in {
    'bridge_listing_id', 'first_processed_at', 'processing_status'})


def refresh_audits(config, audit_ids, client=None):
    if config.env == 'development':
        raise ValueError('Bridge refresh is unavailable in the development sandbox.')
    ids = list(dict.fromkeys(audit_ids))
    if not ids or len(ids) > 20:
        raise ValueError('Select between 1 and 20 audits to refresh from Bridge.')
    with job_lock(config.database_path):
        with connect(config.database_path) as db:
            snapshots = []
            skipped = 0
            for audit_id in ids:
                audit = db.execute('SELECT * FROM audits WHERE id=?', (audit_id,)).fetchone()
                if not audit:
                    raise ValueError('An audit no longer exists. Reload the list.')
                listing = db.execute('SELECT * FROM listings WHERE id=?', (audit['listing_id'],)).fetchone()
                if audit['email_status'] not in {'email_failed','email_pending'} or audit['outcome']:
                    skipped += 1
                    continue
                if audit['test_mode'] and not config.test_mode:
                    raise ValueError('A test audit cannot be refreshed for production delivery.')
                snapshots.append((dict(audit),dict(listing)))
        if not snapshots:
            return {'refreshed':0,'skipped':skipped,'needs_attention':0}
        client = client or BridgeClient(config)
        updates = []
        try:
            client.inspect_metadata()
            for audit, previous in snapshots:
                current = client.listing_by_key(previous['bridge_listing_id'])
                if str(current.get('listing_id')) != previous['bridge_listing_id']:
                    raise BridgeError('Bridge returned a different listing. No refresh was saved.')
                error = None
                try:
                    if not is_eligible_listing(current):
                        raise EmailError('This listing is no longer eligible for Cornerstone audits.')
                    to, cc, actual = resolve_recipients(current, config)
                except EmailError as exc:
                    error = str(exc)
                    to = [current['broker_email']] if current.get('broker_email') else []
                    cc = [value for value in (current.get('brokerage_email'),current.get('agent_email')) if value]
                    actual = [config.admin_email] if config.test_mode else to + cc
                updates.append((audit,previous,current,to,cc,actual,error))
        except BridgeError as exc:
            raise ValueError(str(exc)) from None
        with connect(config.database_path) as db:
            if not db.in_transaction:
                db.execute('BEGIN IMMEDIATE')
            for audit, previous, current, to, cc, actual, error in updates:
                now_audit = db.execute('SELECT * FROM audits WHERE id=?',(audit['id'],)).fetchone()
                now_listing = db.execute('SELECT * FROM listings WHERE id=?',(previous['id'],)).fetchone()
                if not now_audit or not now_listing or dict(now_audit) != audit or dict(now_listing) != previous:
                    raise ValueError('Audit data changed during refresh. No refresh was saved; reload and try again.')
            for audit, previous, current, to, cc, actual, error in updates:
                db.execute('UPDATE listings SET ' + ','.join(name+'=?' for name in FIELDS) + ' WHERE id=?',
                           tuple(current.get(name) for name in FIELDS)+(previous['id'],))
                db.execute('UPDATE audits SET brokerage_id=?,brokerage_name=?,broker_id=?,broker_name=?,agent_name=?,intended_to=?,intended_cc=?,actual_recipients=?,last_error=? WHERE id=?',
                           (current.get('brokerage_id'),current.get('brokerage_name'),current.get('broker_id'),current.get('broker_name'),current.get('agent_name'),json.dumps(to),json.dumps(cc),json.dumps(actual),error,audit['id']))
                if error:
                    db.execute("UPDATE audits SET email_status='email_failed' WHERE id=?",(audit['id'],))
                    db.execute("UPDATE listings SET processing_status='email_failed' WHERE id=?",(previous['id'],))
                event(db,'audit.data_refreshed',audit['id'],'Bridge listing and contacts refreshed; no email sent')
            db.commit()
        return {'refreshed':len(updates),'skipped':skipped,'needs_attention':sum(bool(row[-1]) for row in updates)}
