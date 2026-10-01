"""Request and failed-notice template editors."""
import html

from ..templates import (FIELDS, FAILURE_FIELDS, clean_html, current_failure_templates,
                         current_templates, format_message_parts, plain_to_html, validate_templates)
from .common import esc, local_time, retry_token


def template_editor(config, values=None, error="", kind="request"):
    failure = kind == "failure"
    saved_subject, saved_body, updated_at, saved_format = (current_failure_templates(config) if failure else current_templates(config))
    subject, body, body_format = values if values is not None else (saved_subject, saved_body, saved_format)
    sample = {"mls_number": "12345678", "address": "100 Example Street, Toronto ON", "agent_name": "Alex Agent",
              "brokerage_name": "Example Realty", "broker_name": "Morgan Broker", "broker_first_name": "Morgan"}
    sample_issues = "Signed listing agreement was not provided.\nThe listing price does not match the agreement." if failure else None
    try:
        validate_templates(subject, body, body_format, kind)
        preview_subject, _, preview_html = format_message_parts(subject, body, sample, config.test_mode,
                                                       ["broker@example.com"], ["office@example.com", "agent@example.com"],
                                                       body_format, issues=sample_issues)
        preview = f'<div class="preview-subject">{html.escape(preview_subject)}</div><div class="preview-body">{preview_html}</div>'
    except ValueError as exc:
        preview = '<div class="preview-empty">Fix the template to see a preview.</div>'
        if not error:
            error = str(exc)
    tags = "".join(f'<button class="tag-button" type="button" data-tag="{{{{{field}}}}}"><code>{{{{{field}}}}}</code></button>' for field in (FAILURE_FIELDS if failure else FIELDS))
    editor_html = clean_html(body) if body_format == "html" else plain_to_html(body)
    updated = f'Last saved {esc(local_time(updated_at, config.timezone))}' if updated_at else "Using the starting template from configuration"
    return f'''<div class="template-grid"><section class="panel template-panel"><div class="panel-head"><div><h2>{"Edit failed-audit notice" if failure else "Edit audit request"}</h2><p>{updated}</p></div></div>
    <form method="post" action="/{"failure-template" if failure else "template"}" class="template-form"><input type="hidden" name="token" value="{retry_token(config, "failure-template" if failure else "template")}">
    {'<div class="form-error" role="alert">'+html.escape(error)+'</div>' if error else ''}
    <label for="subject">Subject</label><input id="subject" name="subject" maxlength="200" required value="{html.escape(subject, quote=True)}">
    <label for="body-editor">Message body</label><div class="format-toolbar" role="toolbar" aria-label="Message formatting"><button type="button" class="format-button" data-command="bold" title="Bold" aria-label="Bold"><strong>B</strong></button><button type="button" class="format-button" data-command="italic" title="Italic" aria-label="Italic"><em>I</em></button><button type="button" class="format-button" data-command="underline" title="Underline" aria-label="Underline"><u>U</u></button></div>
    <div id="body-editor" class="body-editor" contenteditable="true" role="textbox" aria-label="Message body" aria-multiline="true">{editor_html}</div><input id="body" name="body" type="hidden"><input type="hidden" name="body_format" value="html">
    <div class="tag-caption">Insert listing details</div><div class="tag-row">{tags}</div>
    <p class="form-help">Select text, then choose B, I, or U to format it. {"Keep MLS number, address, and issues tags in the body." if failure else "Keep MLS number, address, agent, and brokerage tags in the body."} Changes apply to future emails and retries; sent emails stay as they were.</p>
    <div class="form-actions"><button type="submit" name="action" value="preview">Preview changes</button><button class="primary-button" type="submit" name="action" value="save">Save template</button></div></form></section>
    <section class="panel preview-panel"><div class="panel-head"><div><h2>Preview</h2><p>Example listing · {"test mode" if config.test_mode else "production mode"}</p></div></div><div class="preview-content">{preview}</div></section></div>
    <script src="/static/template-editor.js" defer></script>'''

