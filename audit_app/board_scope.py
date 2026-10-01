"""Cornerstone-only audit boundary, independent of office and broker names."""
from .bridge import BridgeClient, BridgeError
from .database import connect, init_db
from .security import event

BOARD = 'Cornerstone'


def require_cornerstone_audit(db, audit_id):
    row = db.execute('SELECT l.originating_system_name FROM audits a JOIN listings l ON l.id=a.listing_id WHERE a.id=?', (audit_id,)).fetchone()
    if not row or row['originating_system_name'] != BOARD:
        raise ValueError('Only verified Cornerstone listings can be audited or followed up.')


def backfill_listing_boards(config, client=None):
    """Read board provenance for older records; never select audits or send mail."""
    if config.env == 'development':
        raise ValueError('Live board verification is unavailable in development.')
    from .job import job_lock
    init_db(config.database_path)
    client = client or BridgeClient(config)
    client.inspect_metadata()
    field = config.field_map['Property']['originating_system_name']
    key_field = config.field_map['Property']['listing_id']
    checked = verified = other = missing = 0
    with job_lock(config.database_path):
        with connect(config.database_path) as db:
            keys = [row[0] for row in db.execute('SELECT bridge_listing_id FROM listings WHERE originating_system_name IS NULL ORDER BY id')]
        for offset in range(0, len(keys), 40):
            batch = keys[offset:offset+40]
            filters = [key_field + " eq '" + str(key).replace("'", "''") + "'" for key in batch]
            rows = client._collection('Property', {'$filter': ' or '.join(filters), '$select': key_field + ',' + field, '$top': 200})
            found = {}
            for row in rows:
                key = str(row.get(key_field, ''))
                if key not in batch:
                    raise BridgeError('Board verification returned an unexpected listing.')
                board = row.get(field)
                if isinstance(board, str) and board.strip():
                    board = board.strip()
                    if key in found and found[key] != board:
                        raise BridgeError('Board verification returned conflicting listing boards.')
                    found[key] = board
            with connect(config.database_path) as db:
                for key, board in found.items():
                    db.execute('UPDATE listings SET originating_system_name=? WHERE bridge_listing_id=? AND originating_system_name IS NULL', (board,key))
                event(db, 'listings.board_verified', 'backfill', f'{len(batch)} checked; {len(found)} identified')
                db.commit()
            checked += len(batch)
            verified += sum(board == BOARD for board in found.values())
            other += sum(board != BOARD for board in found.values())
            missing += len(batch) - len(found)
    return {'checked':checked, 'cornerstone':verified, 'other_boards':other, 'unverified':missing}
