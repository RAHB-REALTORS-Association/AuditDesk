"""Repair missing contacts on unsent audits without selection or delivery."""
import logging

from .database import connect, init_db
from .emailer import valid_email
from .refresh import refresh_audits

LOG = logging.getLogger('audit_app')


def backfill_broker_contacts(config, client=None):
    if config.env == 'development':
        raise ValueError('Live broker contact repair is unavailable in development.')
    init_db(config.database_path)
    with connect(config.database_path) as db:
        rows = db.execute('''SELECT a.id,l.broker_email FROM audits a
            JOIN listings l ON l.id=a.listing_id
            WHERE a.email_status IN ('email_pending','email_failed') AND a.outcome IS NULL
            AND listing_allowed(l.originating_system_name,l.agent_mls_id,l.agent_membership_class)
            ORDER BY a.id''').fetchall()
    ids = [row['id'] for row in rows if not valid_email(row['broker_email'])]
    result = {'total': len(ids), 'refreshed': 0, 'skipped': 0, 'needs_attention': 0}
    LOG.info('broker_contact_repair_started', extra={'total': len(ids)})
    for start in range(0, len(ids), 20):
        batch = refresh_audits(config, ids[start:start + 20], client=client)
        for name in ('refreshed', 'skipped', 'needs_attention'):
            result[name] += batch[name]
        LOG.info('broker_contact_repair_progress', extra=result)
    return result
