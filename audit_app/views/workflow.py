"""Audit result forms and failed-notice previews."""
import html
import json

from ..asana import follow_up
from ..board_scope import require_cornerstone_audit
from ..database import connect
from ..emailer import EmailError, resolve_recipients
from ..templates import current_failure_templates, format_message_parts
from .common import badge, esc, local_time, retry_token
from .deadlines import deadline_view, response_form


def asana_handoff(config, data):
    title, notes, draft = follow_up(config, data)
    saved = data['asana_task_url'] or ''
    action = (f'<a class="primary-button" href="{esc(saved)}" target="_blank" rel="noopener noreferrer">Open Asana task</a>' if saved else
              f'<a class="primary-button" href="{esc(draft)}" target="_blank" rel="noopener noreferrer">Create Asana task ↗</a>')
    return f'''<section class="asana-handoff" aria-labelledby="asana-heading"><h3 id="asana-heading">Follow up in Asana</h3>
        <p>Open a prefilled task draft, choose its project and assignee in Asana, then create the task. Paste its link below to keep it with this audit.</p>
        {action}
        {'<p class="form-help">A task is already linked. Open it before creating another.</p>' if saved else ''}
        <details><summary>Review or copy follow-up details</summary>
        <label for="asana-details">Task title and description<textarea id="asana-details" rows="8" readonly>{esc(title + chr(10) + chr(10) + notes)}</textarea></label>
        <button type="button" data-copy-asana>Copy follow-up details</button><p class="form-help" data-copy-status role="status"></p>
        <p class="form-help">If the prefilled draft is incomplete, paste these full details into Asana.</p></details>
        <form method="post" action="/outcome/{data['id']}/asana" class="asana-link-form">
        <input type="hidden" name="token" value="{retry_token(config, 'asana')}">
        <label for="asana-task-url">Asana task link (optional)<input id="asana-task-url" type="url" name="asana_task_url" maxlength="2048" placeholder="https://app.asana.com/…" value="{html.escape(saved, quote=True)}"></label>
        <button type="submit">Save task link</button><p class="form-help">Paste the link after creating the task. Clear it and save to remove the link.</p></form></section>'''


def outcome_view(config, audit_id, issues="", error="", preview=False):
    with connect(config.database_path) as db:
        require_cornerstone_audit(db, audit_id)
        row = db.execute("""SELECT a.*,l.mls_number,l.address,l.agent_name AS listing_agent_name,
            l.agent_email,l.brokerage_email,l.broker_email,l.broker_first_name FROM audits a
            JOIN listings l ON l.id=a.listing_id WHERE a.id=?""", (audit_id,)).fetchone()
        remediation = db.execute("SELECT detail FROM activity_events WHERE action='audit.remediated' AND target=? ORDER BY id DESC LIMIT 1", (str(audit_id),)).fetchone()
    if not row:
        return '<section class="panel outcome-panel">Audit not found.</section>'
    data = dict(row)
    response = '<div class="response-summary"><h3>Broker response</h3>' + deadline_view(config, data) + response_form(config, data, detailed=True) + '</div>'
    heading = f'<div class="panel-head"><div><h2>MLS {esc(data["mls_number"])} · {esc(data["address"])}</h2><p>{esc(data["brokerage_name"])} · {esc(data["broker_name"])}</p></div></div>'
    if data["outcome"]:
        details = ('<p class="form-error" role="alert">' + esc(error) + '</p>' if error else '')
        details += f'<p>Result: {badge(data["outcome"])}</p>'
        remediated = data["outcome"] == "passed" and remediation is not None
        if remediated:
            previous_at = json.loads(remediation["detail"])["previous_outcome_at"]
            details += f'<p>Remediated and passed {esc(local_time(data["outcome_at"], config.timezone))}</p><p>Originally failed {esc(local_time(previous_at, config.timezone))}</p>'
        else:
            details += f'<p>Recorded {esc(local_time(data["outcome_at"], config.timezone))}</p>'
        if data["outcome"] == "failed":
            if data["failure_email_status"] == "email_sending":
                details += '<p>The failed-audit notice is being sent. Remediation is available after delivery finishes.</p>'
            else:
                details += f'''<form method="post" action="/outcome/{audit_id}/remediate" class="pass-form">
                    <input type="hidden" name="token" value="{retry_token(config, 'remediate')}">
                    <p>Once the listing has been corrected, mark this audit passed. The original failure and notice history stay available. No email will be sent.</p>
                    <button type="submit" class="primary-button">Mark remediated and passed</button></form>'''
            details += asana_handoff(config, data)
        if data["outcome"] == "failed" or remediated:
            details += f'<h3>{"Original failure issues" if remediated else "Issues recorded"}</h3><pre class="outcome-issues">{html.escape(data["issues"] or "")}</pre><p>{"Original follow-up notice" if remediated else "Follow-up notice"}: {badge(data["failure_email_status"] or "email_pending")}</p>'
            if data["failure_last_error"]:
                details += f'<p class="error">{esc(data["failure_last_error"])}</p>'
            if remediated and data['asana_task_url']:
                details += f'<p><a href="{esc(data["asana_task_url"])}" target="_blank" rel="noopener noreferrer">Open Asana task</a></p>'
        back_link = '<a href="/?tab=listings">Back to listing history</a>' if data["outcome"] == "passed" else '<a href="/?tab=audits">Back to audit queue</a>'
        return f'<section class="panel outcome-panel">{heading}<div class="outcome-content">{details}{back_link}</div></section>'
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
            test_delivery = f'<p><strong>Test delivery:</strong> {esc(", ".join(actual))}</p>' if config.test_mode else ""
            preview_html = f'''<div class="outcome-preview"><h3>Review failed-audit notice</h3>
                {test_delivery}
                <p><strong>To:</strong> {esc(", ".join(intended_to))}<br><strong>CC:</strong> {esc(", ".join(intended_cc))}</p>
                <div class="preview-subject">{html.escape(subject)}</div><div class="preview-body">{rich_body}</div>
                <button class="primary-button" type="submit" name="action" value="send">Record fail and send notice</button></div>'''
        except (EmailError, ValueError) as exc:
            error = str(exc)
    return f'''<section class="panel outcome-panel">{heading}<div class="outcome-content">
        {'<div class="form-error" role="alert">'+html.escape(error)+'</div>' if error else ''}
        {response}
        <form method="post" action="/outcome/{audit_id}" class="pass-form"><input type="hidden" name="token" value="{token}"><input type="hidden" name="outcome" value="passed"><button type="submit" name="action" value="record">Mark passed</button></form>
        <form method="post" action="/outcome/{audit_id}" class="failure-form"><input type="hidden" name="token" value="{token}"><input type="hidden" name="outcome" value="failed">
        <label for="issues">If the audit failed, describe each issue</label><textarea id="issues" name="issues" maxlength="5000" rows="7" placeholder="Describe the missing, incorrect, or incomplete items">{html.escape(issues)}</textarea>
        <p>The issues will appear in the follow-up email. Review the notice before sending.</p>
        <button type="submit" name="action" value="preview">Preview failed notice</button>{preview_html}</form></div></section>'''
