"""Cornerstone-only audit boundary, independent of office and broker names."""
from .database import connect, init_db
from .security import event

BOARD = 'Cornerstone'


def is_eligible_listing(listing):
    listing = dict(listing)
    member = listing.get('agent_mls_id')
    return (listing.get('originating_system_name') == BOARD and isinstance(member, str)
            and bool(member.strip()) and member.strip().upper() != 'NONMEM')


def require_cornerstone_audit(db, audit_id):
    row = db.execute('SELECT l.originating_system_name,l.agent_mls_id FROM audits a JOIN listings l ON l.id=a.listing_id WHERE a.id=?', (audit_id,)).fetchone()
    if not row or not is_eligible_listing(row):
        raise ValueError('Only verified Cornerstone member listings can be audited or followed up.')


def backfill_listing_boards(config, client=None):
    """Read board and agent MLS identifiers; never select audits or send mail."""
    if config.env == 'development':
        raise ValueError('Live board verification is unavailable in development.')
    from .bridge import BridgeClient, BridgeError
    from .job import job_lock
    init_db(config.database_path)
    client = client or BridgeClient(config)
    client.inspect_metadata()
    field = config.field_map['Property']['originating_system_name']
    member_field = config.field_map['Property']['agent_mls_id']
    key_field = config.field_map['Property']['listing_id']
    checked = verified = other = missing = nonmembers = 0
    with job_lock(config.database_path):
        with connect(config.database_path) as db:
            keys = [row[0] for row in db.execute('SELECT bridge_listing_id FROM listings WHERE originating_system_name IS NULL OR agent_mls_id IS NULL ORDER BY id')]
        for offset in range(0, len(keys), 40):
            batch = keys[offset:offset+40]
            filters = [key_field + " eq '" + str(key).replace("'", "''") + "'" for key in batch]
            rows = client._collection('Property', {'$filter': ' or '.join(filters), '$select': key_field + ',' + field + ',' + member_field, '$top': 200})
            found = {}
            for row in rows:
                key = str(row.get(key_field, ''))
                if key not in batch:
                    raise BridgeError('Board verification returned an unexpected listing.')
                board = row.get(field)
                if isinstance(board, str) and board.strip():
                    board = board.strip()
                    member = row.get(member_field)
                    member = member.strip() if isinstance(member, str) and member.strip() else None
                    if key in found and found[key] != (board, member):
                        raise BridgeError('Board verification returned conflicting listing boards.')
                    found[key] = (board, member)
            with connect(config.database_path) as db:
                for key, (board, member) in found.items():
                    db.execute('UPDATE listings SET originating_system_name=?,agent_mls_id=? WHERE bridge_listing_id=?', (board,member,key))
                event(db, 'listings.board_verified', 'backfill', f'{len(batch)} checked; {len(found)} identified')
                db.commit()
            checked += len(batch)
            verified += sum(board == BOARD and member is not None and member.upper() != 'NONMEM' for board, member in found.values())
            other += sum(board != BOARD for board, member in found.values())
            missing += len(batch) - len(found) + sum(board == BOARD and member is None for board, member in found.values())
            nonmembers += sum((member or '').upper() == 'NONMEM' for board, member in found.values())
    return {'checked':checked, 'cornerstone':verified, 'other_boards':other, 'unverified':missing, 'nonmembers':nonmembers}
