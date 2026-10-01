"""Selection, application access, and activity views."""
import html

from ..database import connect
from ..lists import query_page
from ..settings import brokerage_cooldown_days, display_percent, selection_percent, workflow_config
from .common import esc, retry_token, sort_heading


def admin_view(config, value=None, error=""):
    config = workflow_config(config)
    current = display_percent(selection_percent(config))
    cooldown = brokerage_cooldown_days(config)
    shown = current if value is None else value
    window_note = ("The live-data test has ended. Changing this percentage will not restart listing collection or email delivery."
                   if config.test_mode and not config.test_window_open() else "")
    return f'''<section class="panel admin-panel"><div class="panel-head"><div><h2>Listing selection</h2><p>Choose the percentage of new listings to target for audit.</p></div></div>
        <div class="admin-content">{'<div class="form-error" role="alert">'+html.escape(error)+'</div>' if error else ''}
        <div class="admin-current"><span>Current selection rate</span><strong>{current}%</strong></div>
        <form method="post" action="/admin/selection-rate" class="admin-rate-form"><input type="hidden" name="token" value="{retry_token(config, "selection-rate")}">
        <label for="rate-percent">Selection percentage</label><div class="rate-control"><input id="rate-percent" name="rate_percent" type="number" min="0" max="100" step="0.01" inputmode="decimal" required value="{html.escape(shown, quote=True)}"><span>%</span><button class="primary-button" type="submit">Save percentage</button></div></form>
        <p>Changes apply to future runs and new listings only. 0% pauses selection; 100% targets every eligible listing. Brokerage and broker cooldowns can reduce the final count.</p>
        {f'<p class="admin-window-note">{window_note}</p>' if window_note else ''}</div></section>
        <section class="panel admin-panel"><div class="panel-head"><div><h2>Brokerage cooldown</h2><p>Wait before selecting another listing from the same brokerage office.</p></div></div>
        <div class="admin-content"><div class="admin-current"><span>Current cooldown</span><strong>{cooldown} {"day" if cooldown == 1 else "days"}</strong></div>
        <form method="post" action="/admin/brokerage-cooldown" class="admin-rate-form"><input type="hidden" name="token" value="{retry_token(config, "brokerage-cooldown")}">
        <label for="cooldown-days">Cooldown period</label><div class="rate-control"><input id="cooldown-days" name="cooldown_days" type="number" min="0" max="365" step="1" inputmode="numeric" required value="{cooldown}"><span>days</span><button class="primary-button" type="submit">Save cooldown</button></div></form>
        <p>Changes apply to future selections only. 0 days removes the waiting period between runs; each office can still be selected only once per run.</p></div></section>
        <section class="panel admin-panel"><div class="panel-head"><div><h2>Broker cooldown and listing window</h2><p>Control candidate eligibility and the initial intake window.</p></div></div>
        <div class="admin-content"><form method="post" action="/manage/workflow" class="workflow-settings-form">
        <input type="hidden" name="token" value="{retry_token(config, 'workflow')}">
        <label for="broker-cooldown">Broker cooldown (days)<input id="broker-cooldown" name="broker_cooldown_days" type="number" min="0" max="365" step="1" required value="{config.broker_cooldown_days}"></label>
        <p class="form-help">Wait before selecting another listing from the same broker. 0 removes the waiting period; each broker can still be selected only once per run.</p>
        <label for="listing-window">Initial listing window (hours)<input id="listing-window" name="window_hours" type="number" min="1" max="168" step="1" required value="{config.window_hours}"></label>
        <p class="form-help">Look back 1–168 hours for Active listings. Catch-up after downtime can extend this window; already processed listings are excluded.</p>
        <button type="submit" class="primary-button">Save workflow settings</button></form></div></section>
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
        return f'<form method="post" action="/users" class="access-form"><input type="hidden" name="token" value="{retry_token(config, "users")}"><input type="hidden" name="version" value="{user["version"]}"><label>Email <input type="email" name="email" required value="{html.escape(user["email"], quote=True)}" {"readonly" if user["email"] else ""}></label><label>Display name <input name="display_name" maxlength="100" value="{html.escape(user["display_name"], quote=True)}"></label><label>Role <select name="role">{roles}</select></label><label class="access-active"><input type="checkbox" name="active" value="1" {"checked" if user["active"] else ""}> Active</label><button type="submit">{"Save access" if user["email"] else "Grant access"}</button></form>'
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
