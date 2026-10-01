"""Audit result forms and failed-notice previews."""
import html

from ..database import connect
from ..emailer import EmailError, resolve_recipients
from ..templates import current_failure_templates, format_message_parts
from .common import badge, esc, local_time, retry_token


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

