"""Verify membership classes for historical snapshots without sending requests."""
from .bridge import ResoClient
from .database import connect, init_db
from .eligibility import code
from .job import job_lock
from .security import event


def backfill_membership_classes(config, client=None):
    if config.env == 'development':
        raise ValueError('Live membership verification is unavailable in development.')
    init_db(config.database_path)
    client = client or ResoClient(config)
    client.inspect_metadata()
    field = config.field_map['Member']['membership_class']
    checked = verified = 0
    with job_lock(config.database_path):
        with connect(config.database_path) as db:
            records = [dict(row) for row in db.execute('''SELECT id,agent_id FROM listings
                WHERE agent_membership_class IS NULL ORDER BY id''')]
        for record in records:
            membership = code(client._one('Member', record['agent_id'], client.members).get(field)) or None
            checked += 1
            if not membership:
                continue
            with connect(config.database_path) as db:
                cursor = db.execute('''UPDATE listings SET agent_membership_class=?
                    WHERE id=? AND agent_id IS ? AND agent_membership_class IS NULL''',
                    (membership, record['id'], record['agent_id']))
                if cursor.rowcount:
                    event(db, 'listing.membership_verified', record['id'], membership)
                    verified += 1
                db.commit()
    return {'checked': checked, 'verified': verified, 'unverified': checked - verified}
