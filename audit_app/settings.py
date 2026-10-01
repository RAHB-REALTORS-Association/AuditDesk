from .security import event
"""Editable selection settings shared by the dashboard and scheduled job."""

import re
from decimal import Decimal

from .database import connect
from .emailer import utcnow


def selection_percent(config, db=None):
    if db is None:
        with connect(config.database_path) as connection:
            return selection_percent(config, connection)
    row = db.execute("SELECT rate_percent FROM selection_settings WHERE id=1").fetchone()
    return Decimal(row["rate_percent"]) if row else Decimal(str(config.rate)) * 100


def brokerage_cooldown_days(config, db=None):
    if db is None:
        with connect(config.database_path) as connection:
            return brokerage_cooldown_days(config, connection)
    row = db.execute("SELECT days FROM brokerage_cooldown_settings WHERE id=1").fetchone()
    return row["days"] if row else config.brokerage_cooldown_days


def save_brokerage_cooldown_days(config, raw):
    value = raw.strip()
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,2})", value) or int(value) > 365:
        raise ValueError("Enter a whole number of days from 0 to 365.")
    days = int(value)
    with connect(config.database_path) as db:
        db.execute("""INSERT INTO brokerage_cooldown_settings(id,days,updated_at) VALUES(1,?,?)
            ON CONFLICT(id) DO UPDATE SET days=excluded.days,updated_at=excluded.updated_at""",
                   (days, utcnow()))
        event(db, "selection.brokerage_cooldown_updated", "brokerage-cooldown", value)
        db.commit()
    return days


def save_selection_percent(config, raw):
    value = raw.strip()
    if not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,2})?", value):
        raise ValueError("Enter a percentage from 0 to 100, with up to two decimal places.")
    percent = Decimal(value)
    if percent > 100:
        raise ValueError("Enter a percentage from 0 to 100.")
    with connect(config.database_path) as db:
        db.execute("""INSERT INTO selection_settings(id,rate_percent,updated_at) VALUES(1,?,?)
            ON CONFLICT(id) DO UPDATE SET rate_percent=excluded.rate_percent,updated_at=excluded.updated_at""",
                   (str(percent), utcnow()))
        event(db, "selection.updated", "selection-rate", str(percent))
        db.commit()
    return percent


def display_percent(percent):
    value = format(percent, "f")
    return value.rstrip("0").rstrip(".") if "." in value else value
