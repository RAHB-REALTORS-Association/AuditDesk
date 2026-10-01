"""Selection, reviewer roster, application access, and activity views."""
import html

from ..database import connect
from ..settings import display_percent, selection_percent
from .common import esc, retry_token


def reviewers_view(config, reviewers, error=""):
    rows = ""
    for person in reviewers:
        reviewer_id = person["id"]
        action = "deactivate" if person["active"] else "activate"
        rows += f'''<tr><td><form method="post" action="/reviewers/{reviewer_id}" class="reviewer-form">
            <input type="hidden" name="token" value="{retry_token(config, f"reviewer:{reviewer_id}")}">
            <input name="name" aria-label="Name" maxlength="80" required value="{html.escape(person["name"], quote=True)}">
            <button name="action" value="rename">Save name</button>
            <button name="action" value="{action}">{"Remove from list" if person["active"] else "Restore to list"}</button>
            <button name="action" value="delete" class="delete-button" formnovalidate onclick="return confirm('Delete this team member? Their unfinished audits will need reassignment.');">Delete</button>
            </form></td><td>{"Available" if person["active"] else "Inactive"}</td></tr>'''
    return f'''<section class="panel roster-panel"><div class="panel-head"><div><h2>Audit team</h2><p>Names available in the audit assignment dropdown</p></div></div>
        <div class="roster-content">{'<div class="form-error" role="alert">'+html.escape(error)+'</div>' if error else ''}
        <form method="post" action="/reviewers" class="reviewer-form"><input type="hidden" name="token" value="{retry_token(config, "reviewers")}">
        <input name="name" aria-label="New team member name" maxlength="80" required placeholder="Team member name"><button class="primary-button" type="submit">Add name</button></form>
        <p>Remove from list hides a name from new assignments and can be undone. Delete removes the roster entry; unfinished audits assigned to that person will need reassignment. The former name remains visible on those audit records.</p>
        <table class="roster-table"><thead><tr><th>Name</th><th>Status</th></tr></thead><tbody>{rows or '<tr><td colspan="2">No names yet. Add one above to begin assigning audits.</td></tr>'}</tbody></table></div></section>'''



def admin_view(config, value=None, error=""):
    current = display_percent(selection_percent(config))
    shown = current if value is None else value
    window_note = ("The live-data test has ended. Changing this percentage will not restart listing collection or email delivery."
                   if config.test_mode and not config.test_window_open() else "")
    return f'''<section class="panel admin-panel"><div class="panel-head"><div><h2>Listing selection</h2><p>Choose the percentage of new listings to target for audit.</p></div></div>
        <div class="admin-content">{'<div class="form-error" role="alert">'+html.escape(error)+'</div>' if error else ''}
        <div class="admin-current"><span>Current selection rate</span><strong>{current}%</strong></div>
        <form method="post" action="/admin/selection-rate" class="admin-rate-form"><input type="hidden" name="token" value="{retry_token(config, "selection-rate")}">
        <label for="rate-percent">Selection percentage</label><div class="rate-control"><input id="rate-percent" name="rate_percent" type="number" min="0" max="100" step="0.01" inputmode="decimal" required value="{html.escape(shown, quote=True)}"><span>%</span><button class="primary-button" type="submit">Save percentage</button></div></form>
        <p>Changes apply to future runs and new listings only. 0% pauses selection; 100% targets every eligible listing. Brokerage and broker cooldowns can reduce the final count.</p>
        {f'<p class="admin-window-note">{window_note}</p>' if window_note else ''}</div></section>'''



def users_view(config):
    with connect(config.database_path) as db:
        users = db.execute("SELECT * FROM app_users ORDER BY email").fetchall()
    def form(user=None):
        user = dict(user) if user else {"email": "", "display_name": "", "role": "reviewer", "active": 1, "version": 0}
        roles = "".join(f'<option value="{role}" {"selected" if role == user["role"] else ""}>{label}</option>' for role, label in (("reviewer", "Reviewer"), ("manager", "Audit manager"), ("admin", "IT administrator")))
        return f'<form method="post" action="/users" class="access-form"><input type="hidden" name="token" value="{retry_token(config, "users")}"><input type="hidden" name="version" value="{user["version"]}"><label>Email <input type="email" name="email" required value="{html.escape(user["email"], quote=True)}" {"readonly" if user["email"] else ""}></label><label>Display name <input name="display_name" maxlength="100" value="{html.escape(user["display_name"], quote=True)}"></label><label>Role <select name="role">{roles}</select></label><label class="access-active"><input type="checkbox" name="active" value="1" {"checked" if user["active"] else ""}> Active</label><button type="submit">{"Save access" if user["email"] else "Grant access"}</button></form>'
    return '<section class="panel access-panel"><div class="panel-head"><h2>People and roles</h2></div><div class="access-content"><p class="form-help">Cloudflare verifies identity. Only active people listed here may use AuditDesk. Bootstrap administrators are protected.</p>' + "".join(form(user) for user in users) + '<h3 class="access-heading">Add a person</h3>' + form() + '</div></section>'



def activity_view(config):
    with connect(config.database_path) as db:
        events = db.execute("""SELECT e.*,u.email FROM activity_events e LEFT JOIN app_users u ON u.subject=e.actor
            ORDER BY e.id DESC LIMIT 200""").fetchall()
    rows = "".join(f'<tr><td>{esc(e["occurred_at"])}</td><td>{esc(e["email"] or e["actor"])}</td><td>{esc(e["action"])}</td><td>{esc(e["target"])}</td><td>{esc(e["detail"])}</td></tr>' for e in events)
    return '<section class="panel"><div class="panel-head"><h2>Latest 200 changes</h2><a href="/activity.csv">Export CSV</a></div><div class="table-wrap"><table><thead><tr><th>Time (UTC)</th><th>Actor</th><th>Action</th><th>Target</th><th>Details</th></tr></thead><tbody>' + (rows or '<tr><td colspan="5">No changes recorded yet.</td></tr>') + '</tbody></table></div></section>'

