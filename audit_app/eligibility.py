"""Managed business exclusions; Cornerstone membership remains a fixed boundary."""
import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class EligibilityRules:
    membership_classes: tuple = ('NL7',)
    agent_ids: tuple = ()


def code(value):
    return value.strip().upper() if isinstance(value, str) else ''


def membership_code(value):
    """Accept a code or the feed's 'CODE - description' label."""
    value = code(value)
    match = re.match(r'^([A-Z0-9][A-Z0-9_.-]{0,39})\s+[-–—]\s+\S', value)
    return match.group(1) if match else value


def parse_codes(raw, label):
    if len(raw) > 2000:
        raise ValueError(f'{label} must contain at most 50 codes.')
    values = tuple(sorted(set(part.upper() for part in re.split(r'[\s,]+', raw.strip()) if part)))
    if len(values) > 50 or any(not re.fullmatch(r'[A-Z0-9][A-Z0-9_.-]{0,39}', value) for value in values):
        raise ValueError(f'{label}: use up to 50 codes containing letters, numbers, dots, hyphens or underscores.')
    return values


def rules_from_form(form):
    return EligibilityRules(parse_codes(form.get('membership_classes', ''), 'Membership classes'),
                            parse_codes(form.get('agent_ids', ''), 'Agent MLS IDs'))


def read_rules(db):
    # Older backups are inspected before migration; they use the default policy.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='listing_eligibility'").fetchone():
        return EligibilityRules()
    row = db.execute('SELECT membership_classes,agent_ids FROM listing_eligibility WHERE id=1').fetchone()
    return EligibilityRules(tuple(json.loads(row[0])), tuple(json.loads(row[1]))) if row else EligibilityRules()


def eligibility_reason(listing, rules=EligibilityRules(), *, require_class=False):
    listing = dict(listing)
    if listing.get('originating_system_name') != 'Cornerstone':
        return 'Board is not verified as Cornerstone'
    agent_id = code(listing.get('agent_mls_id'))
    if not agent_id:
        return 'Agent MLS ID is missing'
    if agent_id == 'NONMEM':
        return 'Non-member / interboard agent (NONMEM)'
    if agent_id in rules.agent_ids:
        return f'Excluded agent MLS ID: {agent_id}'
    membership = membership_code(listing.get('agent_membership_class'))
    if membership in rules.membership_classes:
        return f'Excluded membership class: {membership}'
    if require_class and not membership:
        return 'Agent membership class is unverified; refresh from MLS before sending'
    return None


def save_rules(config, form):
    from .database import connect
    from .emailer import utcnow
    from .security import event
    rules = rules_from_form(form)
    with connect(config.database_path) as db:
        db.execute('''INSERT INTO listing_eligibility VALUES(1,?,?,?) ON CONFLICT(id)
            DO UPDATE SET membership_classes=excluded.membership_classes,
            agent_ids=excluded.agent_ids,updated_at=excluded.updated_at''',
            (json.dumps(rules.membership_classes), json.dumps(rules.agent_ids), utcnow()))
        event(db, 'settings.eligibility_updated', 'listing-eligibility',
              'Excluded membership classes: ' + ', '.join(rules.membership_classes) +
              '; excluded agent MLS IDs: ' + ', '.join(rules.agent_ids))
        db.commit()
    return rules
