"""Managed office availability, request days and response deadlines."""
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .database import connect
from .emailer import utcnow
from .security import event
from .request_schedule import RequestPolicy, policy_from_form, write_policy

DAY_NAMES = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
DEFAULT_HOURS = tuple(('08:30', '16:30') if day < 5 else None for day in range(7))


def local_instant(day, wall_time, zone):
    """Use the first occurrence of repeated times; advance past a DST gap."""
    naive = datetime.combine(day, wall_time)
    tz = ZoneInfo(zone)
    for _ in range(1441):
        candidate = naive.replace(tzinfo=tz).astimezone(timezone.utc)
        if candidate.astimezone(tz).replace(tzinfo=None) == naive:
            return candidate
        naive = naive.replace(second=0, microsecond=0) + timedelta(minutes=1)
    raise ValueError('The office calendar contains an unavailable local date.')


def read_calendar(config, db=None):
    if db is None:
        with connect(config.database_path) as connection:
            return read_calendar(config, connection)
    row = db.execute('SELECT * FROM office_calendar WHERE id=1').fetchone()
    return (json.loads(row['weekly_hours']), set(json.loads(row['holidays']))) if row else (DEFAULT_HOURS, set())


def save_calendar(config, form):
    policy = policy_from_form(form)
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
        stamp = utcnow()
        db.execute('''INSERT INTO office_calendar VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET
            weekly_hours=excluded.weekly_hours,holidays=excluded.holidays,updated_at=excluded.updated_at''',
            (json.dumps(hours), json.dumps(sorted(holidays)), stamp))
        write_policy(db, policy, stamp)
        event(db, 'settings.office_calendar_updated', 'office-calendar', json.dumps({'hours': hours, 'holidays': sorted(holidays),
            'send_days': policy.send_days, 'response_days': policy.response_days, 'deadline_mode': policy.deadline_mode}))
        db.commit()


def office_open(moment, zone, calendar):
    local = moment.astimezone(ZoneInfo(zone))
    hours, holidays = calendar
    interval = hours[local.weekday()]
    return bool(interval and local.date().isoformat() not in holidays
                and time.fromisoformat(interval[0]) <= local.time().replace(tzinfo=None) < time.fromisoformat(interval[1]))


def next_office_time(moment, zone, calendar):
    """First staffed instant at or after moment, ignoring request-send weekdays."""
    if office_open(moment, zone, calendar):
        return moment
    hours, holidays = calendar
    local = moment.astimezone(ZoneInfo(zone))
    for offset in range(740):
        day = local.date() + timedelta(days=offset)
        interval = hours[day.weekday()]
        if not interval or day.isoformat() in holidays:
            continue
        opening = local_instant(day, time.fromisoformat(interval[0]), zone)
        candidate = max(moment, opening)
        if office_open(candidate, zone, calendar):
            return candidate
    return None


def response_deadline(moment, zone, calendar, policy=RequestPolicy()):
    moment = moment.astimezone(timezone.utc)
    if policy.deadline_mode != 'business_days':
        due = moment + timedelta(days=policy.response_days)
        return next_office_time(due, zone, calendar) if policy.deadline_mode == 'next_open' else due
    local = moment.astimezone(ZoneInfo(zone))
    hours, holidays = calendar
    remaining = policy.response_days
    for offset in range(1, 740):
        day = local.date() + timedelta(days=offset)
        if not hours[day.weekday()] or day.isoformat() in holidays:
            continue
        remaining -= 1
        if remaining == 0:
            due = local_instant(day, local.time().replace(tzinfo=None), zone)
            return next_office_time(due, zone, calendar)
    return None


def can_send_request(moment, zone, calendar, policy=RequestPolicy()):
    if moment.astimezone(ZoneInfo(zone)).weekday() not in policy.send_days or not office_open(moment, zone, calendar):
        return False
    due = response_deadline(moment, zone, calendar, policy)
    return due is not None and office_open(due, zone, calendar)


def next_request_time(moment, zone, calendar, policy=RequestPolicy()):
    if not policy.send_days or not any(calendar[0]):
        return None
    if can_send_request(moment, zone, calendar, policy):
        return moment
    local = moment.astimezone(ZoneInfo(zone))
    hours, holidays = calendar
    for offset in range(740):
        day = local.date() + timedelta(days=offset)
        interval = hours[day.weekday()]
        if day.weekday() not in policy.send_days or not interval or day.isoformat() in holidays:
            continue
        def boundary(on, value):
            return local_instant(on, time.fromisoformat(value), zone)
        start = max(moment, boundary(day, interval[0]))
        if policy.deadline_mode != 'elapsed':
            if start < boundary(day, interval[1]) and can_send_request(start, zone, calendar, policy):
                return start
            continue
        # UTC elapsed days can land on an adjacent local date across DST.
        candidates = []
        for due_offset in range(max(0, policy.response_days - 1), policy.response_days + 2):
            due_day = day + timedelta(days=due_offset)
            following = hours[due_day.weekday()]
            if not following or due_day.isoformat() in holidays:
                continue
            candidate = max(start, boundary(due_day, following[0]) - timedelta(days=policy.response_days))
            end = min(boundary(day, interval[1]), boundary(due_day, following[1]) - timedelta(days=policy.response_days))
            if candidate < end and can_send_request(candidate, zone, calendar, policy):
                candidates.append(candidate)
        if candidates:
            return min(candidates)
    return None
