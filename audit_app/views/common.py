"""Shared HTML formatting and form-token helpers."""
import html
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlencode

from flask import has_request_context, request

from ..security import csrf_token


def esc(value):
    return html.escape(str(value or "—"), quote=True)



def local_time(value, zone):
    if not value:
        return "—"
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(ZoneInfo(zone)).strftime("%b %d, %Y · %I:%M %p")



def badge(status):
    kind = "good" if status in {"email_sent", "completed", "passed", "in_progress", "response_received"} else "bad" if status in {"email_failed", "email_unknown", "email_blocked", "failed", "completed_with_email_errors", "needs_reassignment", "run_had_errors"} else "neutral"
    label = status.replace("_", " ").title()
    return f'<span class="badge {kind}">{esc(label)}</span>'



def sort_heading(label, kind="text", initial=False):
    if has_request_context():
        direction = 'desc' if request.args.get('sort') == label and request.args.get('direction') == 'asc' else 'asc'
        values = [(key, value) for key, values in request.args.lists() for value in values if key not in {'sort','direction','page'}]
        if not any(key == 'tab' for key, _ in values):
            values.append(('tab', 'audits'))
        href = '/?' + urlencode(values + [('sort',label),('direction',direction)])
        state = ('ascending' if request.args.get('direction') == 'asc' else 'descending') if request.args.get('sort') == label else 'descending' if initial else None
        aria = f' aria-sort="{state}"' if state else ''
        return f'<th{aria}><a class="sort-button" href="{esc(href)}">{esc(label)}</a></th>'
    direction = ' aria-sort="descending"' if initial else ''
    return f'<th{direction}><button type="button" class="sort-button" data-sort-type="{kind}">{esc(label)}</button></th>'



def recipients(value):
    try:
        return ", ".join(json.loads(value)) or "—"
    except (ValueError, TypeError):
        return "—"



def retry_token(config, audit_id):
    return csrf_token(config, audit_id)
