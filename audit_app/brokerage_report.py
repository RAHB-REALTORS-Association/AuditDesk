"""Brokerage-level audit statistics from the app's saved listing history."""

import calendar
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .database import connect


PERIODS = {"3m": (3, "3 months"), "6m": (6, "6 months"), "1y": (12, "1 year")}


def _local_day(value, zone):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(zone).date()


def _months_before(day, months):
    month_index = day.year * 12 + day.month - 1 - months
    year, month_index = divmod(month_index, 12)
    month = month_index + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def brokerage_statistics(config, period="3m", today=None):
    if period not in PERIODS:
        raise ValueError("Choose a 3-month, 6-month, or 1-year period.")
    zone = ZoneInfo(config.timezone)
    today = today or datetime.now(zone).date()
    months, label = PERIODS[period]
    start = _months_before(today, months)
    groups = {}
    first_recorded = None

    with connect(config.database_path) as db:
        records = db.execute("""SELECT l.brokerage_id, l.brokerage_name, l.brokerage_address, l.first_processed_at,
            a.id AS audit_id, a.outcome FROM listings l LEFT JOIN audits a ON a.listing_id=l.id
            ORDER BY l.first_processed_at""")
        for record in records:
            day = _local_day(record["first_processed_at"], zone)
            first_recorded = min(first_recorded, day) if first_recorded else day
            if not start <= day <= today:
                continue
            office_id = (record["brokerage_id"] or "").strip()
            name = " ".join((record["brokerage_name"] or "").split())
            key = ("name", name.casefold()) if name else ("office", office_id)
            group = groups.setdefault(key, {"name": name or (f"Office {office_id}" if office_id else "Unknown brokerage"),
                                            "branches": {}, "listings": 0, "audited": 0,
                                            "passed": 0, "failed": 0})
            if name:
                group["name"] = name
            branch = group["branches"].setdefault(office_id, {"office_id": office_id,
                "address": None, "listings": 0, "audited": 0, "passed": 0, "failed": 0})
            if record["brokerage_address"]:
                branch["address"] = record["brokerage_address"]
            for target in (group, branch):
                target["listings"] += 1
                target["audited"] += bool(record["audit_id"])
                target["passed"] += record["outcome"] == "passed"
                target["failed"] += record["outcome"] == "failed"

    rows = sorted(groups.values(), key=lambda row: (-row["listings"], row["name"].casefold()))
    def add_rates(row):
        row["percentage"] = 100 * row["audited"] / row["listings"]
        row["completed"] = row["passed"] + row["failed"]
        row["pass_rate"] = 100 * row["passed"] / row["completed"] if row["completed"] else None
        row["fail_rate"] = 100 * row["failed"] / row["completed"] if row["completed"] else None

    for row in rows:
        row["branches"] = sorted(row["branches"].values(), key=lambda branch: ((branch["address"] or "").casefold(), branch["office_id"]))
        row["branch_count"] = len(row["branches"])
        add_rates(row)
        for branch in row["branches"]:
            add_rates(branch)
    total = sum(row["listings"] for row in rows)
    audited = sum(row["audited"] for row in rows)
    passed = sum(row["passed"] for row in rows)
    failed = sum(row["failed"] for row in rows)
    completed = passed + failed
    return {"period": period, "period_label": label, "start": start, "end": today,
            "first_recorded": first_recorded, "timezone": config.timezone,
            "rows": rows, "total": total, "audited": audited, "passed": passed, "failed": failed,
            "completed": completed, "pass_rate": 100 * passed / completed if completed else None,
            "fail_rate": 100 * failed / completed if completed else None,
            "percentage": 100 * audited / total if total else None}
