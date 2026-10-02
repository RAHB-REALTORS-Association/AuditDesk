"""Response clocks and receipt controls shared by history and audit details."""
from datetime import datetime, timedelta, timezone

from ..security import allowed
from .common import badge, esc, local_time, retry_token


def deadline_view(config, audit, now=None, next_send=None):
    now = now or datetime.now(timezone.utc)
    if audit['outcome']:
        return badge('completed') + (f'<small>Response received {esc(local_time(audit["response_received_at"], config.timezone))}</small>' if audit['response_received_at'] else '')
    if audit['response_received_at']:
        due = audit['response_due_at'] or (datetime.fromisoformat(audit['email_sent_at'].replace('Z', '+00:00')) + timedelta(hours=24)).isoformat()
        late = datetime.fromisoformat(audit['response_received_at'].replace('Z', '+00:00')) > datetime.fromisoformat(due.replace('Z', '+00:00'))
        return badge('response_received') + f'<small>{esc(local_time(audit["response_received_at"], config.timezone))} · {"After deadline" if late else "Within 24 hours"}</small><small>Awaiting audit review</small>'
    if audit['email_status'] != 'email_sent' or not audit['email_sent_at']:
        label = 'Delivery uncertain · timer unavailable' if audit['email_status'] == 'email_unknown' else 'Not sent · timer not started'
        hint = f'<small>Next request window: {esc(local_time(next_send.isoformat(), config.timezone))}</small>' if audit['email_status'] == 'email_pending' and next_send and config.email_enabled else ''
        return f'<span class="muted">{label}</span>{hint}'
    due = audit['response_due_at'] or (datetime.fromisoformat(audit['email_sent_at'].replace('Z', '+00:00')) + timedelta(hours=24)).isoformat()
    seconds = (datetime.fromisoformat(due.replace('Z', '+00:00')) - now).total_seconds()
    minutes = max(1, int(abs(seconds) / 60))
    duration = f'{minutes // 60}h {minutes % 60}m'
    label = f'Overdue by {duration}' if seconds <= 0 else f'{duration} left'
    kind = 'bad' if seconds <= 0 else 'warning' if seconds <= 4 * 3600 else 'neutral'
    return f'<span class="badge {kind}" data-response-deadline="{esc(due)}" data-server-now="{now.isoformat()}">{label}</span><small>Due {esc(local_time(due, config.timezone))}</small>'


def response_form(config, audit, detailed=False):
    if not allowed('audits.result') or audit['outcome'] or audit['email_status'] != 'email_sent' or not audit['email_sent_at']:
        return ''
    if audit['response_received_at']:
        if not detailed:
            return ''
        controls = '<input type="hidden" name="action" value="reopen"><button type="submit" data-confirm="Clear the recorded response receipt and resume the response timer?">Reopen response timer</button>'
    else:
        controls = ('<label>Response received at (office time)<input type="datetime-local" name="received_at"></label><p class="form-help">Leave blank to use now. Enter the actual receipt time if recording it later.</p>' if detailed else '')
        controls += '<input type="hidden" name="action" value="received"><button type="submit">Response received</button>'
    return f'<form method="post" action="/audits/{audit["id"]}/response" class="response-form"><input type="hidden" name="token" value="{retry_token(config, "response")}">{controls}</form>'
