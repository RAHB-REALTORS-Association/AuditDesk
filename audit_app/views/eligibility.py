"""Business rule editing with a bounded, read-only stored-data preview."""
from html import escape

from ..database import connect
from ..eligibility import eligibility_reason, read_rules, rules_from_form
from .common import esc, retry_token


def eligibility_view(config, values=None, error='', preview=False):
    with connect(config.database_path) as db:
        saved = read_rules(db)
        rules = saved
        if preview and values is not None:
            rules = rules_from_form(values)
        db.create_function('preview_reason', 3, lambda board, agent, membership:
            eligibility_reason({'originating_system_name': board, 'agent_mls_id': agent,
                'agent_membership_class': membership}, rules, require_class=True))
        source = 'preview_reason(originating_system_name,agent_mls_id,agent_membership_class)'
        counts = db.execute(f"SELECT COALESCE({source},'Eligible') AS reason,count(*) AS total FROM listings GROUP BY reason ORDER BY reason").fetchall()
        samples = db.execute(f'SELECT mls_number,{source} AS reason FROM listings WHERE {source} IS NOT NULL ORDER BY id DESC LIMIT 10').fetchall()
    classes = values.get('membership_classes', '') if values is not None else '\n'.join(saved.membership_classes)
    agent_ids = values.get('agent_ids', '') if values is not None else '\n'.join(saved.agent_ids)
    summary = ''.join(f'<tr><td>{esc(label)}</td><td>{count:,}</td></tr>' for label, count in counts)
    rows = ''.join(f'<tr><td>{esc(number)}</td><td>{esc(reason)}</td></tr>' for number, reason in samples)
    source = config.field_map['Member']['membership_class']
    return f'''<section class="panel admin-panel"><div class="panel-head"><div><h2>Listing eligibility</h2>
        <p>Exclude agent categories that should not receive audit requests.</p></div></div><div class="admin-content">
        {'<p class="form-error" role="alert">'+esc(error)+'</p>' if error else ''}
        <p>Only Cornerstone listings are considered. Interboard agents marked NONMEM are always excluded.</p>
        <form method="post" action="/manage/eligibility" class="eligibility-form">
        <input type="hidden" name="token" value="{retry_token(config, 'eligibility')}">
        <label for="membership-classes">Excluded membership classes (MUC)<textarea id="membership-classes" name="membership_classes" rows="3" maxlength="2000" spellcheck="false">{escape(classes)}</textarea></label>
        <p class="form-help">NL7 excludes super subscribers. Enter one code per line or separate codes with commas. Clear this field to remove optional class exclusions.</p>
        <label for="excluded-agent-ids">Additional excluded agent MLS IDs<textarea id="excluded-agent-ids" name="agent_ids" rows="3" maxlength="2000" spellcheck="false">{escape(agent_ids)}</textarea></label>
        <p class="form-help">Use MLS identifiers, not names or email addresses. Codes are case-insensitive; up to 50 per field.</p>
        <div class="eligibility-actions"><button type="submit" name="action" value="preview">Preview rules</button>
        <button type="submit" name="action" value="save" class="primary-button">Save eligibility rules</button></div>
        <p class="form-help">Changes apply to future intake and to unsent requests, including retries. Previous requests and audit records are retained. Unverified membership classes cannot be selected or sent; IT can verify historical records, or staff can refresh an unsent audit from the MLS.</p></form>
        <details class="eligibility-source"><summary>Data source and intake window</summary><p class="form-help">Membership class: MLS Member → {esc(source)}. Board: OriginatingSystemName = Cornerstone. Status: Active. The initial lookback window is managed under Selection; catch-up includes listings entered during downtime.</p></details>
        </div></section><section class="panel"><div class="panel-head"><div><h2>{'Preview of unsaved rules' if preview else 'Current rules preview'}</h2>
        <p>Stored listing snapshots only. No MLS query, selection or email is triggered. Eligibility does not guarantee selection; cooldowns and sampling still apply.</p></div></div>
        <div class="table-wrap"><table><thead><tr><th>Eligibility</th><th>Listings</th></tr></thead><tbody>{summary or '<tr><td colspan="2">No stored listings to preview.</td></tr>'}</tbody></table></div>
        {f'<div class="panel-head"><h3>Latest excluded or unverified listings · up to 10</h3></div><div class="table-wrap"><table><thead><tr><th>MLS number</th><th>Reason</th></tr></thead><tbody>{rows}</tbody></table></div>' if rows else ''}</section>'''
