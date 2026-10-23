"""Selection, application access, and activity views."""
import html
from flask import g, has_request_context

from ..database import connect
from ..lists import query_page
from ..office_calendar import DAY_NAMES, read_calendar
from ..request_schedule import DEADLINE_MODES, read_policy
from ..settings import brokerage_cooldown_days, display_percent, selection_percent, workflow_config
from .common import esc, retry_token, sort_heading


def calendar_view(config, values=None, error=''):
    hours, holidays = read_calendar(config)
    policy = read_policy(config)
    send_days = ''.join(f'<label><input type="checkbox" name="send_{day}" value="1" {"checked" if (values.get(f"send_{day}") == "1" if values is not None else day in policy.send_days) else ""}>{label}</label>' for day, label in enumerate(DAY_NAMES))
    send_time = values.get('send_time', '') if values is not None else policy.send_time
    mode = values.get('deadline_mode', '') if values is not None else policy.deadline_mode
    response_days = values.get('response_days', '') if values is not None else str(policy.response_days)
    modes = ''.join(f'<option value="{key}" {"selected" if key == mode else ""}>{esc(label)}</option>' for key, label in DEADLINE_MODES.items())
    rows = ''
    for day, label in enumerate(DAY_NAMES):
        start, end = hours[day] or ('', '')
        if values is not None:
            start, end = (values.get(f'{name}_{day}', '') for name in ('open', 'close'))
        rows += f'<div class="office-hours-row"><strong>{label}</strong><label>Opens<input type="time" name="open_{day}" value="{html.escape(start, quote=True)}"></label><label>Closes<input type="time" name="close_{day}" value="{html.escape(end, quote=True)}"></label></div>'
    holiday_text = values.get('holidays', '') if values is not None else '\n'.join(sorted(holidays))
    return f'''<section class="panel admin-panel"><div class="panel-head"><div><h2>Office hours &amp; holidays</h2><p>Request delivery and broker response availability · {esc(config.timezone)}</p></div></div>
        <div class="admin-content">{'<p class="form-error" role="alert">'+esc(error)+'</p>' if error else ''}
        <p>Listings are considered every day. Choose when requests may send and how their response deadlines use the office calendar. Existing sent deadlines stay fixed.</p>
        <form method="post" action="/manage/calendar" class="office-calendar-form"><input type="hidden" name="token" value="{retry_token(config, 'calendar')}">
        <label for="request-send-time">Request email time ({esc(config.timezone)})<input id="request-send-time" name="send_time" type="time" value="{html.escape(send_time, quote=True)}"></label>
        <p class="form-help">Requests wait until this local time each eligible day. If the office opens later, they wait for opening. The scheduler checks every five minutes and catches up after downtime. Leave blank to send whenever the existing calendar allows. Listing collection keeps its daily 8 a.m. schedule; failed-audit notices are unchanged.</p>
        <fieldset class="request-send-days"><legend>Allowed request-send days</legend>{send_days}</fieldset>
        <p class="form-help">Requests also require office hours and cannot send on a holiday. Uncheck all days to pause request delivery without stopping listing collection.</p>
        <label for="response-days">Response period (days)<input id="response-days" name="response_days" type="number" min="1" max="30" step="1" required value="{html.escape(response_days, quote=True)}"></label>
        <label for="deadline-mode">Deadline policy<select id="deadline-mode" name="deadline_mode">{modes}</select></label>
        <p class="form-help">Strict elapsed days count 24 hours each, including weekends: a one-day Friday request waits if Saturday is closed. Roll forward keeps that elapsed period, then moves a closed deadline to the next opening. Business days skip closed weekdays and holidays, preserving the local send time; if that time is outside office hours, the deadline moves to the next opening. Send-day choices do not restrict response days.</p>
        <p class="form-help">For a one-day Friday 10 a.m. request with Monday–Friday hours and no holidays: strict mode waits to send, roll forward is due Monday at opening, and business days is due Monday at 10 a.m. Defaults preserve strict one-day behavior.</p>
        <div class="office-hours">{rows}</div><p class="form-help">Leave both times blank for closed days. Use same-day opening and closing times.</p>
        <label for="holiday-dates">Holidays and office closures<textarea id="holiday-dates" name="holidays" rows="6" maxlength="5000" placeholder="2026-12-25">{html.escape(holiday_text)}</textarea></label>
        <p class="form-help">One YYYY-MM-DD date per line. Include observed holidays and any other full-day closures. No holidays are assumed automatically.</p>
        <button type="submit" class="primary-button">Save office calendar</button><p class="form-help">Changes affect queued requests. Deadlines for requests already sent stay unchanged. Email and scheduler switches still apply.</p></form></div></section>'''


def admin_view(config, value=None, error=""):
    config = workflow_config(config)
    current = display_percent(selection_percent(config))
    cooldown = brokerage_cooldown_days(config)
    shown = current if value is None else value
    window_note = ("The live-data test has ended. Changing this percentage will not restart listing collection or email delivery."
                   if config.test_mode and not config.test_window_open() else "")
    return f'''<section class="panel admin-panel"><div class="panel-head"><div><h2>Listing selection</h2><p>Choose the percentage of new listings to target for audit.</p></div></div>
        <div class="admin-content">{'<div class="form-error" role="alert">'+html.escape(error)+'</div>' if error else ''}
        <form method="post" action="/manage/selection" class="workflow-settings-form selection-settings-form">
        <input type="hidden" name="token" value="{retry_token(config, 'selection')}">
        <label for="rate-percent">Selection percentage (%)<input id="rate-percent" name="rate_percent" type="number" min="0" max="100" step="0.01" inputmode="decimal" required value="{html.escape(shown, quote=True)}"></label>
        <p class="form-help">0% pauses selection; 100% targets every eligible new listing. Cooldowns can reduce the final count.</p>
        <label for="cooldown-days">Office cooldown (days)<input id="cooldown-days" name="cooldown_days" type="number" min="0" max="365" step="1" required value="{cooldown}"></label>
        <p class="form-help">Wait before auditing another listing from the same brokerage office.</p>
        <label for="broker-cooldown">Individual broker cooldown (days)<input id="broker-cooldown" name="broker_cooldown_days" type="number" min="0" max="365" step="1" required value="{config.broker_cooldown_days}"></label>
        <p class="form-help">Wait before auditing the same broker, including across offices. Set either cooldown to 0 to remove its waiting period; each office and broker can still be selected only once per run.</p>
        <label for="listing-window">Initial listing window (hours)<input id="listing-window" name="window_hours" type="number" min="1" max="168" step="1" required value="{config.window_hours}"></label>
        <p class="form-help">Look back 1–168 hours for Active listings. Catch-up after downtime can extend this window; already processed listings are excluded.</p>
        <button type="submit" class="primary-button">Save selection settings</button>
        <p class="form-help">Changes apply to future runs and new listings only.</p>
        {f'<p class="admin-window-note">{window_note}</p>' if window_note else ''}</form></div></section>
        <section class="panel admin-panel"><div class="panel-head"><div><h2>Service configuration</h2><p>Deployment and delivery safeguards</p></div></div>
        <div class="admin-content"><dl class="service-settings"><div><dt>Daily schedule</dt><dd>08:00 · {esc(config.timezone)}</dd></div>
        <div><dt>Scheduler</dt><dd>{'Enabled' if config.scheduler_enabled else 'Disabled'}</dd></div>
        <div><dt>Email delivery</dt><dd>{'Enabled' if config.email_enabled else 'Disabled'}</dd></div>
        <div><dt>Environment</dt><dd>{esc(config.env.title())}</dd></div></dl>
        <p class="form-help">IT configures credentials, identity, sender verification, timezone, scheduling, and live email switches in the deployment environment.</p></div></section>'''



def users_view(config):
    with connect(config.database_path) as db:
        users = db.execute("SELECT * FROM app_users ORDER BY email").fetchall()
    def form(user=None):
        user = dict(user) if user else {"email": "", "display_name": "", "role": "reviewer", "active": 1, "version": 0}
        roles = "".join(f'<option value="{role}" {"selected" if role == user["role"] else ""}>{label}</option>' for role, label in (("reviewer", "Reviewer"), ("manager", "Audit manager"), ("admin", "IT administrator")))
        removal = ''
        if user['email'] and user['email'] not in config.bootstrap_admins and (not has_request_context() or user['email'] != g.principal.email):
            removal = f'<button type="submit" class="danger-button" formaction="/users/{user["id"]}/delete" formnovalidate data-confirm="Delete this person and revoke their access? Their audit assignments will be cleared; audit history will remain.">Delete person</button>'
        return f'<form method="post" action="/users" class="access-form"><input type="hidden" name="token" value="{retry_token(config, "users")}"><input type="hidden" name="version" value="{user["version"]}"><label>Email <input type="email" name="email" required value="{html.escape(user["email"], quote=True)}" {"readonly" if user["email"] else ""}></label><label>Display name <input name="display_name" maxlength="100" value="{html.escape(user["display_name"], quote=True)}"></label><label>Role <select name="role">{roles}</select></label><label class="access-active"><input type="checkbox" name="active" value="1" {"checked" if user["active"] else ""}> Active</label><div class="access-actions"><button type="submit">{"Save access" if user["email"] else "Grant access"}</button>{removal}</div></form>'
    return '<section class="panel access-panel"><div class="panel-head"><h2>People and roles</h2></div><div class="access-content"><p class="form-help">Cloudflare verifies identity. Only active people listed here may use AuditDesk. Active Reviewers, Audit managers, and IT administrators can be assigned audits. Bootstrap administrators are protected.</p>' + "".join(form(user) for user in users) + '<h3 class="access-heading">Add a person</h3>' + form() + '</div></section>'



def activity_view(config):
    with connect(config.database_path) as db:
        events, page = query_page(db, 'activity', """SELECT e.*,u.email FROM activity_events e
            LEFT JOIN app_users u ON u.subject=e.actor""", ('email','actor','action','target','detail'), 'action',
            {'Time (UTC)':'occurred_at','Actor':'actor','Action':'action','Target':'target','Details':'detail'}, 'id')
        page.facet_label = 'Action'
    rows = "".join(f'<tr><td>{esc(e["occurred_at"])}</td><td>{esc(e["email"] or e["actor"])}</td><td>{esc(e["action"])}</td><td>{esc(e["target"])}</td><td>{esc(e["detail"])}</td></tr>' for e in events)
    headings = ''.join(sort_heading(label) for label in ('Time (UTC)','Actor','Action','Target','Details'))
    return f'<section class="panel"><div class="panel-head"><h2>Activity history</h2><a href="/activity.csv">Export CSV</a></div>{page.filters()}<div class="table-wrap"><table data-server-list data-columns="activity"><thead><tr>{headings}</tr></thead><tbody>' + (rows or '<tr><td colspan="5">No changes match these filters.</td></tr>') + f'</tbody></table></div>{page.footer()}</section>'
