"""Managed office availability and literal 24-hour request deadlines."""
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .database import connect
from .emailer import utcnow
from .security import event

DAY_NAMES = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
DEFAULT_HOURS = tuple(('08:30', '16:30') if day < 5 else None for day in range(7))


def read_calendar(config, db=None):
    if db is None:
        with connect(config.database_path) as connection:
            return read_calendar(config, connection)
    row = db.execute('SELECT * FROM office_calendar WHERE id=1').fetchone()
    return (json.loads(row['weekly_hours']), set(json.loads(row['holidays']))) if row else (DEFAULT_HOURS, set())


def save_calendar(config, form):
    hours = []
    for day, label in enumerate(DAY_NAMES):
        start, end = (form.get(f'{name}_{day}', '').strip() for name in ('open', 'close'))
        if not start and not end:
            hours.append(None)
            continue
        if any(not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', value) for value in (start, end)) or start >= end:
            raise ValueError(f'{label}: enter opening and closing times, with closing after opening, or leave both blank.')
        hours.append((start, end))
    raw = form.get('holidays', '')
    if len(raw) > 5000:
        raise ValueError('Enter at most 366 holiday dates, one per line.')
    holidays = set()
    for value in raw.splitlines():
        value = value.strip()
        if not value:
            continue
        try:
            if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
                raise ValueError()
            date.fromisoformat(value)
        except ValueError:
            raise ValueError('Use valid holiday dates in YYYY-MM-DD format, one per line.') from None
        holidays.add(value)
    if len(holidays) > 366:
        raise ValueError('Enter at most 366 holiday dates.')
    with connect(config.database_path) as db:
        db.execute('''INSERT INTO office_calendar VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET
            weekly_hours=excluded.weekly_hours,holidays=excluded.holidays,updated_at=excluded.updated_at''',
            (json.dumps(hours), json.dumps(sorted(holidays)), utcnow()))
        event(db, 'settings.office_calendar_updated', 'office-calendar', json.dumps({'hours': hours, 'holidays': sorted(holidays)}))
        db.commit()


def office_open(moment, zone, calendar):
    local = moment.astimezone(ZoneInfo(zone))
    hours, holidays = calendar
    interval = hours[local.weekday()]
    return bool(interval and local.date().isoformat() not in holidays
                and time.fromisoformat(interval[0]) <= local.time().replace(tzinfo=None) < time.fromisoformat(interval[1]))


def can_send_request(moment, zone, calendar):
    # Calendar arithmetic in UTC preserves 24 elapsed hours across DST changes.
    moment = moment.astimezone(timezone.utc)
    return office_open(moment, zone, calendar) and office_open(moment + timedelta(hours=24), zone, calendar)


def next_request_time(moment, zone, calendar):
    if can_send_request(moment, zone, calendar):
        return moment
    local = moment.astimezone(ZoneInfo(zone))
    hours, holidays = calendar
    for offset in range(370):
        day = local.date() + timedelta(days=offset)
        interval = hours[day.weekday()]
        if not interval or day.isoformat() in holidays:
            continue
        tomorrow = day + timedelta(days=1)
        following = hours[tomorrow.weekday()]
        if not following or tomorrow.isoformat() in holidays:
            continue
        def boundary(on, value):
            return datetime.combine(on, time.fromisoformat(value), ZoneInfo(zone)).astimezone(timezone.utc)
        start = max(moment, boundary(day, interval[0]), boundary(tomorrow, following[0]) - timedelta(hours=24))
        end = min(boundary(day, interval[1]), boundary(tomorrow, following[1]) - timedelta(hours=24))
        if start < end and can_send_request(start, zone, calendar):
            return start
    return None
