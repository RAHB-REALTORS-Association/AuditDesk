"""Verify membership classes for historical snapshots without sending requests."""
import logging

from .bridge import ResoClient
from .database import connect, init_db
from .eligibility import code
from .job import job_lock
from .security import event

LOG = logging.getLogger('audit_app')


def backfill_membership_classes(config, client=None):
    if config.env == 'development':
        raise ValueError('Live membership verification is unavailable in development.')
    init_db(config.database_path)
    client = client or ResoClient(config)
    LOG.info('membership_verification_metadata')
    client.inspect_metadata()
    field = config.field_map['Member']['membership_class']
    checked = verified = 0
    with job_lock(config.database_path):
        with connect(config.database_path) as db:
            records = [dict(row) for row in db.execute('''SELECT id,agent_id FROM listings
                WHERE agent_membership_class IS NULL ORDER BY id''')]
        LOG.info('membership_verification_started', extra={'total': len(records)})
        for record in records:
            membership = code(client._one('Member', record['agent_id'], client.members).get(field)) or None
            checked += 1
            if membership:
                with connect(config.database_path) as db:
                    cursor = db.execute('''UPDATE listings SET agent_membership_class=?
                        WHERE id=? AND agent_id IS ? AND agent_membership_class IS NULL''',
                        (membership, record['id'], record['agent_id']))
                    updated = bool(cursor.rowcount)
                    if updated:
                        event(db, 'listing.membership_verified', record['id'], membership)
                    db.commit()
                    verified += int(updated)
            LOG.info('membership_verification_progress', extra={
                'total': len(records), 'checked': checked, 'verified': verified,
                'unverified': checked - verified})
    return {'checked': checked, 'verified': verified, 'unverified': checked - verified}
