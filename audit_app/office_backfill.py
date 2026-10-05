"""Refresh saved listing records from MLS office profiles without running an audit."""

from .bridge import ResoClient, office_address
from .database import connect, init_db


def backfill_office_addresses(config, client=None):
    init_db(config.database_path)
    client = client or ResoClient(config)
    client.inspect_metadata()
    fields = config.field_map["Office"]
    with connect(config.database_path) as db:
        office_ids = [row[0] for row in db.execute("""SELECT DISTINCT brokerage_id FROM listings
            WHERE brokerage_id IS NOT NULL AND trim(brokerage_id) != ''
            AND (brokerage_address IS NULL OR trim(brokerage_address) = '')""")]
    updated = missing = 0
    for office_id in office_ids:
        address = office_address(client._one("Office", office_id, client.offices), fields)
        if not address:
            missing += 1
            continue
        with connect(config.database_path) as db:
            db.execute("""UPDATE listings SET brokerage_address=? WHERE brokerage_id=?
                AND (brokerage_address IS NULL OR trim(brokerage_address) = '')""", (address, office_id))
            db.commit()
        updated += 1
    return {"offices_checked": len(office_ids), "offices_updated": updated, "offices_without_address": missing}
