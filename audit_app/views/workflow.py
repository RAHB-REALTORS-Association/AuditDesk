"""Audit outcomes and the isolated synthetic simulation."""
import html

from ..database import connect
from ..emailer import EmailError, resolve_recipients
from ..simulation import simulate_cycle
from ..templates import current_failure_templates, format_message_parts
from .common import badge, esc, local_time, retry_token


def simulation_view(config):
    result = simulate_cycle(config)
    steps = [
        ("01", "Bridge intake", f'{result["bridge_rows"]} synthetic source listings; {result["server_filtered_rows"]} Active and within 24 hours.', "DEMO-OLD and DEMO-INACTIVE were excluded."),
        ("02", "Selection", f'{result["first_run"]["new"]} new listings entered the 5% draw; {result["first_run"]["selected"]} was selected.', "A recent audit cooled down office-a, so DEMO-B was chosen."),
        ("03", "Audit record", f'One audit was recorded for {result["selected_listing"]} before any send attempt.', "The audit ID remained the same through the retry."),
        ("04", "Recipient routing", f'Intended To: {", ".join(result["intended_to"])}', f'Intended CC: {", ".join(result["intended_cc"])} · Actual: {", ".join(result["actual_recipients"])}'),
        ("05", "Simulated delivery", "First attempt: HTTP 503 → email_failed.", "Manual retry: email_sent after two recorded attempts."),
        ("06", "Duplicate check", f'Second daily run: {result["second_run"]["new"]} new listings, {result["second_run"]["selected"]} new audits.', "No second audit or email was created."),
    ]
    cards = "".join(f'<div class="sim-step"><span class="sim-index">{number}</span><div><h3>{esc(title)}</h3><strong>{esc(main)}</strong><p>{esc(detail)}</p></div></div>' for number, title, main, detail in steps)
    return f'''<div class="sim-alert"><strong>SIMULATION ONLY</strong><span>All listing names and addresses are synthetic. This demonstration uses an isolated temporary database and makes no Bridge or SendGrid network calls.</span></div>
    <div class="sim-summary"><div><span>Source rows</span><strong>{result["bridge_rows"]}</strong></div><div><span>Eligible</span><strong>{result["server_filtered_rows"]}</strong></div><div><span>Audits created</span><strong>{result["audit_records_for_new_listings"]}</strong></div><div><span>Final status</span><strong>{esc(result["final_email_status"].replace("_", " ").title())}</strong></div></div>
    <section class="panel"><div class="panel-head"><div><h2>Complete operation cycle</h2><p>Including a failed send, manual retry, and duplicate-run check</p></div></div><div class="sim-flow">{cards}</div></section>'''



def outcome_view(config, audit_id, issues="", error="", preview=False):
    with connect(config.database_path) as db:
        row = db.execute("""SELECT a.*,l.mls_number,l.address,l.agent_name AS listing_agent_name,
            l.agent_email,l.brokerage_email,l.broker_email,l.broker_first_name FROM audits a
            JOIN listings l ON l.id=a.listing_id WHERE a.id=?""", (audit_id,)).fetchone()
    if not row:
        return '<section class="panel outcome-panel">Audit not found.</section>'
    data = dict(row)
    heading = f'<div class="panel-head"><div><h2>MLS {esc(data["mls_number"])} · {esc(data["address"])}</h2><p>{esc(data["brokerage_name"])} · {esc(data["broker_name"])}</p></div></div>'
    if data["outcome"]:
        details = f'<p>Result: {badge(data["outcome"])}</p><p>Recorded {esc(local_time(data["outcome_at"], config.timezone))}</p>'
        if data["outcome"] == "failed":
            details += f'<h3>Issues recorded</h3><pre class="outcome-issues">{html.escape(data["issues"] or "")}</pre><p>Follow-up notice: {badge(data["failure_email_status"] or "email_pending")}</p>'
            if data["failure_last_error"]:
                details += f'<p class="error">{esc(data["failure_last_error"])}</p>'
        return f'<section class="panel outcome-panel">{heading}<div class="outcome-content">{details}<a href="/?tab=audits">Back to audit history</a></div></section>'
    if data["email_status"] != "email_sent":
        return f'<section class="panel outcome-panel">{heading}<div class="outcome-content">Send the original audit request before recording its result.</div></section>'
    token = retry_token(config, f"outcome:{audit_id}")
    preview_html = ""
    if preview and issues.strip():
        try:
            if data["test_mode"] and not config.test_mode:
                raise ValueError("A test audit cannot send a production failed-audit notice.")
            intended_to, intended_cc, actual = resolve_recipients(data, config)
            subject_template, body_template, _, body_format = current_failure_templates(config)
            subject, _, rich_body = format_message_parts(subject_template, body_template, data,
                config.test_mode, intended_to, intended_cc, body_format, issues=issues.strip())
            preview_html = f'''<div class="outcome-preview"><h3>Review failed-audit notice</h3>
                <p><strong>Actual recipient:</strong> {esc(", ".join(actual))}</p>
                <p><strong>Intended To:</strong> {esc(", ".join(intended_to))}<br><strong>Intended CC:</strong> {esc(", ".join(intended_cc))}</p>
                <div class="preview-subject">{html.escape(subject)}</div><div class="preview-body">{rich_body}</div>
                <button class="primary-button" type="submit" name="action" value="send">Record fail and send notice</button></div>'''
        except (EmailError, ValueError) as exc:
            error = str(exc)
    return f'''<section class="panel outcome-panel">{heading}<div class="outcome-content">
        {'<div class="form-error" role="alert">'+html.escape(error)+'</div>' if error else ''}
        <form method="post" action="/outcome/{audit_id}" class="pass-form"><input type="hidden" name="token" value="{token}"><input type="hidden" name="outcome" value="passed"><button type="submit" name="action" value="record">Mark passed</button></form>
        <form method="post" action="/outcome/{audit_id}" class="failure-form"><input type="hidden" name="token" value="{token}"><input type="hidden" name="outcome" value="failed">
        <label for="issues">If the audit failed, describe each issue</label><textarea id="issues" name="issues" maxlength="5000" rows="7" placeholder="Describe the missing, incorrect, or incomplete items">{html.escape(issues)}</textarea>
        <p>The issues will appear in the follow-up email. Review the notice before sending.</p>
        <button type="submit" name="action" value="preview">Preview failed notice</button>{preview_html}</form></div></section>'''

