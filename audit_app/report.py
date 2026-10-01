"""Daily audit counts from the durable listing, audit, and run records."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .database import connect


def _day(value, zone):
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(zone).date()


def daily_audit_report(config, today=None, days=90):
    if days < 1:
        raise ValueError("Report length must be positive.")
    zone = ZoneInfo(config.timezone)
    today = today or datetime.now(zone).date()
    if type(today) is not date:
        raise ValueError("Report date must be a date.")
    first_day = today - timedelta(days=days - 1)
    rows = {today - timedelta(days=offset): {"date": today - timedelta(days=offset),
            "listings": 0, "audited": 0, "runs": []} for offset in range(days)}
    with connect(config.database_path) as db:
        listings = db.execute("""SELECT l.first_processed_at, a.id AS audit_id FROM listings l
            LEFT JOIN audits a ON a.listing_id=l.id WHERE l.originating_system_name='Cornerstone'""")
        for listing in listings:
            day = _day(listing["first_processed_at"], zone)
            if first_day <= day <= today:
                rows[day]["listings"] += 1
                rows[day]["audited"] += bool(listing["audit_id"])
        for run in db.execute("SELECT started_at,status FROM runs"):
            day = _day(run["started_at"], zone)
            if first_day <= day <= today:
                rows[day]["runs"].append(run["status"])
    for row in rows.values():
        statuses = set(row.pop("runs"))
        if "failed" in statuses or "completed_with_email_errors" in statuses:
            row["status"] = "Run had errors"
        elif "completed" in statuses or row["listings"]:
            row["status"] = "Recorded"
        elif "running" in statuses:
            row["status"] = "Run in progress"
        elif "test_window_closed" in statuses:
            row["status"] = "Test ended"
        else:
            row["status"] = "No run"
        row["percentage"] = (100 * row["audited"] / row["listings"]
                             if row["listings"] else 0.0 if row["status"] == "Recorded" else None)
    return list(rows.values())
