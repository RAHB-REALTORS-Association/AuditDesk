from .security import event
"""Editable selection settings shared by the dashboard and scheduled job."""

import re
from dataclasses import replace
from decimal import Decimal

from .database import connect
from .emailer import utcnow


def workflow_config(config, db=None):
    """Read saved business settings at the start of each run or page render."""
    if db is None:
        with connect(config.database_path) as connection:
            return workflow_config(config, connection)
    row = db.execute("SELECT broker_cooldown_days,window_hours FROM workflow_settings WHERE id=1").fetchone()
    return replace(config, broker_cooldown_days=row['broker_cooldown_days'], window_hours=row['window_hours']) if row else config


def save_workflow_settings(config, form):
    values = {}
    for name, minimum, maximum, label in (('broker_cooldown_days', 0, 365, 'Broker cooldown'),
                                         ('window_hours', 1, 168, 'Listing window')):
        raw = form.get(name, '').strip()
        if not re.fullmatch(r'(?:0|[1-9][0-9]{0,2})', raw) or not minimum <= int(raw) <= maximum:
            raise ValueError(f'{label} must be a whole number from {minimum} to {maximum}.')
        values[name] = int(raw)
    with connect(config.database_path) as db:
        db.execute("""INSERT INTO workflow_settings(id,broker_cooldown_days,window_hours,updated_at) VALUES(1,?,?,?)
            ON CONFLICT(id) DO UPDATE SET broker_cooldown_days=excluded.broker_cooldown_days,
                window_hours=excluded.window_hours,updated_at=excluded.updated_at""",
            (values['broker_cooldown_days'], values['window_hours'], utcnow()))
        event(db, 'settings.workflow_updated', 'workflow',
              f"broker cooldown: {values['broker_cooldown_days']} days; listing window: {values['window_hours']} hours")
        db.commit()
    return values


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


def save_selection_settings(config, form):
    """Validate the whole form before saving its settings in one transaction."""
    raw = form.get('rate_percent', '').strip()
    if not re.fullmatch(r'[0-9]{1,3}(?:\.[0-9]{1,2})?', raw) or Decimal(raw) > 100:
        raise ValueError('Enter a percentage from 0 to 100, with up to two decimal places.')
    percent = Decimal(raw)
    values = {}
    for name, minimum, maximum, label in (('cooldown_days', 0, 365, 'Office cooldown'),
                                         ('broker_cooldown_days', 0, 365, 'Individual broker cooldown'),
                                         ('window_hours', 1, 168, 'Initial listing window')):
        value = form.get(name, '').strip()
        if not re.fullmatch(r'(?:0|[1-9][0-9]{0,2})', value) or not minimum <= int(value) <= maximum:
            raise ValueError(f'{label} must be a whole number from {minimum} to {maximum}.')
        values[name] = int(value)
    with connect(config.database_path) as db:
        stamp = utcnow()
        db.execute("""INSERT INTO selection_settings VALUES(1,?,?) ON CONFLICT(id)
            DO UPDATE SET rate_percent=excluded.rate_percent,updated_at=excluded.updated_at""", (str(percent), stamp))
        db.execute("""INSERT INTO brokerage_cooldown_settings VALUES(1,?,?) ON CONFLICT(id)
            DO UPDATE SET days=excluded.days,updated_at=excluded.updated_at""", (values['cooldown_days'], stamp))
        db.execute("""INSERT INTO workflow_settings VALUES(1,?,?,?) ON CONFLICT(id)
            DO UPDATE SET broker_cooldown_days=excluded.broker_cooldown_days,
            window_hours=excluded.window_hours,updated_at=excluded.updated_at""",
            (values['broker_cooldown_days'], values['window_hours'], stamp))
        event(db, 'settings.selection_updated', 'selection',
              f"rate: {percent}%; office cooldown: {values['cooldown_days']} days; broker cooldown: {values['broker_cooldown_days']} days; listing window: {values['window_hours']} hours")
        db.commit()
