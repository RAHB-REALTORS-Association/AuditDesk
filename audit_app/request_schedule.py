"""Request delivery policy, separate from office availability and intake."""
import json
import re
from dataclasses import dataclass

from .database import connect


DEADLINE_MODES = {
    'elapsed': 'Strict elapsed days — send only when the deadline is in office hours',
    'next_open': 'Elapsed days — move a closed-time deadline to the next opening',
    'business_days': 'Business days — skip closures and keep the local send time',
}


@dataclass(frozen=True)
class RequestPolicy:
    send_days: tuple = tuple(range(7))
    response_days: int = 1
    deadline_mode: str = 'elapsed'
    send_time: str = ''


def read_policy(config, db=None):
    if db is None:
        with connect(config.database_path) as connection:
            return read_policy(config, connection)
    row = db.execute('SELECT * FROM request_schedule WHERE id=1').fetchone()
    return RequestPolicy(tuple(json.loads(row['send_days'])), row['response_days'], row['deadline_mode'], row['send_time']) if row else RequestPolicy()


def policy_from_form(form):
    send_time = form.get('send_time', '')
    if not isinstance(send_time, str) or (send_time and not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', send_time)):
        raise ValueError('Choose a valid request email time in HH:MM format, or leave it blank.')
    raw = form.get('response_days', '')
    mode = form.get('deadline_mode', '')
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdigit() or not 1 <= int(raw) <= 30:
        raise ValueError('Choose a response period from 1 to 30 days.')
    if mode not in DEADLINE_MODES:
        raise ValueError('Choose a deadline policy.')
    if any(form.get(f'send_{day}', '') not in {'', '1'} for day in range(7)):
        raise ValueError('Choose valid request-send days.')
    return RequestPolicy(tuple(day for day in range(7) if form.get(f'send_{day}') == '1'), int(raw), mode, send_time)


def write_policy(db, policy, stamp):
    db.execute('''INSERT INTO request_schedule (id,send_days,response_days,deadline_mode,updated_at,send_time) VALUES(1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
        send_days=excluded.send_days,response_days=excluded.response_days,
        deadline_mode=excluded.deadline_mode,updated_at=excluded.updated_at,send_time=excluded.send_time''',
        (json.dumps(policy.send_days), policy.response_days, policy.deadline_mode, stamp, policy.send_time))


def response_period_text(policy):
    count = policy.response_days
    unit = 'day' if count == 1 else 'days'
    if policy.deadline_mode == 'business_days':
        return f'Response period: {count} business {unit}, at the same local time. Closed days and holidays are skipped; a time outside office hours moves to the next opening.'
    text = f'Response period: {count} elapsed {unit} ({count * 24} hours).'
    if policy.deadline_mode == 'next_open':
        text += ' A deadline outside office hours or on a holiday moves to the next opening.'
    return text
