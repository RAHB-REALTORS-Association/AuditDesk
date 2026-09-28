import base64
import hashlib
import hmac
import html
import json
import logging
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse
from zoneinfo import ZoneInfo

from .assignment import add_reviewer, assign_reviewer, rename_reviewer, set_reviewer_active
from .database import connect, init_db
from .emailer import EmailError, resolve_recipients
from .job import deliver_audit
from .outcomes import deliver_failure_notice, record_outcome
from .settings import display_percent, save_selection_percent, selection_percent
from .simulation import simulate_cycle
from .templates import (FIELDS, FAILURE_FIELDS, clean_html, current_failure_templates, current_templates,
                        format_message_parts, plain_to_html, save_failure_templates, save_templates, validate_templates)


def esc(value):
    return html.escape(str(value or "—"), quote=True)


def local_time(value, zone):
    if not value:
        return "—"
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(ZoneInfo(zone)).strftime("%b %d, %Y · %I:%M %p")


def badge(status):
    kind = "good" if status in {"email_sent", "completed", "passed", "in_progress"} else "bad" if status in {"email_failed", "email_unknown", "email_blocked", "failed", "completed_with_email_errors"} else "neutral"
    label = status.replace("_", " ").title()
    return f'<span class="badge {kind}">{esc(label)}</span>'


def recipients(value):
    try:
        return ", ".join(json.loads(value)) or "—"
    except (ValueError, TypeError):
        return "—"


def retry_token(config, audit_id):
    return hmac.new(config.password.encode(), f"retry:{audit_id}".encode(), hashlib.sha256).hexdigest()


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
    <div id="body-editor" class="body-editor" contenteditable="true" role="textbox" aria-label="Message body" aria-multiline="true">{editor_html}</div><textarea id="body" name="body" hidden></textarea><input type="hidden" name="body_format" value="html">
    <div class="tag-caption">Insert listing details</div><div class="tag-row">{tags}</div>
    <p class="form-help">Select text, then choose B, I, or U to format it. {"Keep MLS number, address, and issues tags in the body." if failure else "Keep MLS number, address, agent, and brokerage tags in the body."} Changes apply to future emails and retries; sent emails stay as they were.</p>
    <div class="form-actions"><button type="submit" name="action" value="preview">Preview changes</button><button class="primary-button" type="submit" name="action" value="save">Save template</button></div></form></section>
    <section class="panel preview-panel"><div class="panel-head"><div><h2>Preview</h2><p>Example listing · {"test mode" if config.test_mode else "production mode"}</p></div></div><div class="preview-content">{preview}</div></section></div>
    <script>const editor=document.getElementById('body-editor'),subject=document.getElementById('subject'),hiddenBody=document.getElementById('body');let lastMergeTarget=editor;editor.addEventListener('focus',()=>lastMergeTarget=editor);subject.addEventListener('focus',()=>lastMergeTarget=subject);document.querySelectorAll('.format-button,.tag-button').forEach(button=>button.addEventListener('mousedown',event=>event.preventDefault()));document.querySelectorAll('.format-button').forEach(button=>button.addEventListener('click',()=>{{editor.focus();document.execCommand(button.dataset.command,false,null);}}));document.querySelectorAll('.tag-button').forEach(button=>button.addEventListener('click',()=>{{const tag=button.dataset.tag;if(lastMergeTarget===subject){{const start=subject.selectionStart,end=subject.selectionEnd;subject.value=subject.value.slice(0,start)+tag+subject.value.slice(end);subject.focus();subject.setSelectionRange(start+tag.length,start+tag.length);}}else{{editor.focus();document.execCommand('insertText',false,tag);}}}}));document.querySelector('.template-form').addEventListener('submit',()=>{{hiddenBody.value=editor.innerHTML;}});</script>'''


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
            </form></td><td>{"Available" if person["active"] else "Inactive"}</td></tr>'''
    return f'''<section class="panel roster-panel"><div class="panel-head"><div><h2>Audit team</h2><p>Names available in the audit assignment dropdown</p></div></div>
        <div class="roster-content">{'<div class="form-error" role="alert">'+html.escape(error)+'</div>' if error else ''}
        <form method="post" action="/reviewers" class="reviewer-form"><input type="hidden" name="token" value="{retry_token(config, "reviewers")}">
        <input name="name" aria-label="New team member name" maxlength="80" required placeholder="Team member name"><button class="primary-button" type="submit">Add name</button></form>
        <p>Removing a name hides it from new assignments. Existing audits keep their assigned name.</p>
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


def render(config, tab="audits", notice="", form_values=None, error="", audit_id=None, preview_outcome=False):
    with connect(config.database_path) as db:
        counts = {
            "processed": db.execute("SELECT count(*) FROM listings").fetchone()[0],
            "selected": db.execute("SELECT count(*) FROM audits").fetchone()[0],
            "sent": db.execute("SELECT count(*) FROM audits WHERE email_status='email_sent'").fetchone()[0],
            "failed": db.execute("SELECT count(*) FROM audits WHERE email_status IN ('email_failed','email_unknown')").fetchone()[0],
        }
        audits = db.execute("""SELECT a.*, l.mls_number, l.address, l.agent_email,
            ar.name AS reviewer_name, ar.active AS reviewer_active FROM audits a
            JOIN listings l ON l.id=a.listing_id LEFT JOIN audit_reviewers ar ON ar.id=a.reviewer_id
            ORDER BY a.selected_at DESC LIMIT 200""").fetchall()
        reviewers = db.execute("SELECT * FROM audit_reviewers ORDER BY active DESC, name COLLATE NOCASE").fetchall()
        listings = db.execute("SELECT * FROM listings ORDER BY first_processed_at DESC LIMIT 200").fetchall()
        runs = db.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 50").fetchall()
        previous = {row["id"]: db.execute("SELECT count(*) FROM audits WHERE brokerage_id=? AND selected_at<?", (row["brokerage_id"], row["selected_at"])).fetchone()[0] if row["brokerage_id"] else 0 for row in audits}
    nav = "".join(f'<a class="nav-item {"active" if tab == name else ""}" href="/?tab={name}">{label}</a>' for name, label in (("audits", "Audit history"), ("reviewers", "Audit team"), ("listings", "Listings considered"), ("runs", "Scheduled runs"), ("simulation", "Simulation")))
    admin_nav = "".join(f'<a class="nav-item {"active" if tab == name else ""}" href="/?tab={name}">{label}</a>' for name, label in (("admin", "Selection settings"), ("template", "Audit request email"), ("failure_template", "Failed-audit email")))
    if config.test_mode:
        detail = "All outgoing messages are redirected exclusively to the administrator."
        if config.test_end_at:
            detail += f" Live-data test ends {local_time(config.test_end_at.isoformat(), config.timezone)}."
            if not config.test_window_open():
                detail = "The live-data test has ended. Scheduled queries and all email sends are blocked."
        banner = f'<div class="test-banner"><strong>TEST MODE</strong><span>{esc(detail)}</span></div>'
    else:
        banner = '<div class="prod-banner">PRODUCTION EMAIL ENABLED</div>'
    cards = "".join(f'<div class="stat"><div class="stat-label">{label}</div><div class="stat-value">{counts[key]}</div></div>' for key, label in (("processed", "Listings considered"), ("selected", "Selected audits"), ("sent", "Emails accepted"), ("failed", "Needs attention")))
    if tab == "simulation":
        title, subtitle = "Simulation", "Watch a complete audit cycle using synthetic listing data."
        content = simulation_view(config)
    elif tab == "template":
        title, subtitle = "Email template", "Edit the audit request and preview merge tags before saving."
        content = template_editor(config, form_values, error)
    elif tab == "failure_template":
        title, subtitle = "Failed-audit email", "Edit the notice sent when an audit is marked failed."
        content = template_editor(config, form_values, error, "failure")
    elif tab == "outcome":
        title, subtitle = "Record audit result", "Mark this audit passed, or describe issues and review its failure notice."
        content = outcome_view(config, audit_id, form_values or "", error, preview_outcome)
    elif tab == "reviewers":
        title, subtitle = "Audit team", "Manage the names available when assigning an audit."
        content = reviewers_view(config, reviewers, error)
    elif tab == "admin":
        title, subtitle = "Admin", "Manage how many new listings are selected for audit."
        content = admin_view(config, form_values, error)
    elif tab == "listings":
        headings = "<th>MLS / Property</th><th>Entered</th><th>Brokerage</th><th>Listing agent</th><th>Processing status</th>"
        rows = "".join(f'<tr><td><strong>{esc(r["mls_number"])}</strong><small>{esc(r["address"])}</small></td><td>{esc(local_time(r["entry_timestamp"], config.timezone))}</td><td>{esc(r["brokerage_name"])}</td><td>{esc(r["agent_name"])}</td><td>{badge(r["processing_status"])}</td></tr>' for r in listings)
        title, subtitle = "Listings considered", "Every new active listing evaluated by the daily selection job."
    elif tab == "runs":
        headings = "<th>Started</th><th>Status</th><th>Fetched</th><th>New</th><th>Selected</th><th>Error</th>"
        rows = "".join(f'<tr><td>{esc(local_time(r["started_at"], config.timezone))}</td><td>{badge(r["status"])}</td><td>{r["fetched_count"]}</td><td>{r["new_count"]}</td><td>{r["selected_count"]}</td><td class="error">{esc(r["error"])}</td></tr>' for r in runs)
        title, subtitle = "Scheduled runs", "Recent daily jobs and any errors they encountered."
    else:
        headings = "<th>MLS / Property</th><th>Selected</th><th>Brokerage / Broker</th><th>Agent</th><th>Intended recipients</th><th>Actual recipient</th><th>Mode / Status</th><th>Work status</th><th>Assigned to</th><th>History</th><th>Outcome</th><th></th>"
        rows = ""
        for r in audits:
            retry = f'<form method="post" action="/retry/{r["id"]}"><input type="hidden" name="token" value="{retry_token(config, r["id"])}"><button type="submit">Retry email</button></form>' if r["email_status"] == "email_failed" else ""
            outcome = f'<a href="/?tab=outcome&id={r["id"]}">Record result</a>' if not r["outcome"] and r["email_status"] == "email_sent" else (f'{badge(r["outcome"])}<small>Notice: {esc((r["failure_email_status"] or "pending").replace("_", " "))}</small><small>{esc(r["issues"])}</small>' if r["outcome"] == "failed" else badge(r["outcome"]) if r["outcome"] else "—")
            if r["outcome"] == "failed" and r["failure_email_status"] in {"email_pending", "email_failed"} and config.test_window_open():
                failure_token = retry_token(config, "failure:" + str(r["id"]))
                retry += f'<form method="post" action="/failure-retry/{r["id"]}"><input type="hidden" name="token" value="{failure_token}"><button type="submit">Send failed notice</button></form>'
            mode = '<span class="mode-test">TEST</span>' if r["test_mode"] else '<span class="mode-prod">LIVE</span>'
            work_status = "Completed" if r["outcome"] else "In progress" if r["reviewer_id"] else "Not started"
            options = '<option value="">Unassigned</option>' + "".join(
                f'<option value="{person["id"]}" {"selected" if person["id"] == r["reviewer_id"] else ""}>{esc(person["name"])}{" (inactive)" if not person["active"] else ""}</option>'
                for person in reviewers if person["active"] or person["id"] == r["reviewer_id"])
            assignment_token = retry_token(config, f'assignment:{r["id"]}')
            assignment = f'<form method="post" action="/assignment/{r["id"]}" class="assignment-form"><input type="hidden" name="token" value="{assignment_token}"><select name="reviewer_id" aria-label="Assign MLS {esc(r["mls_number"])}">{options}</select><button type="submit">Save</button></form>'
            rows += f'<tr><td><strong>{esc(r["mls_number"])}</strong><small>{esc(r["address"])}</small></td><td>{esc(local_time(r["selected_at"], config.timezone))}</td><td>{esc(r["brokerage_name"])}<small>{esc(r["broker_name"])}</small></td><td>{esc(r["agent_name"])}<small>{esc(r["agent_email"])}</small></td><td><span class="muted">To:</span> {esc(recipients(r["intended_to"]))}<small>CC: {esc(recipients(r["intended_cc"]))}</small></td><td>{esc(recipients(r["actual_recipients"]))}</td><td>{mode} {badge(r["email_status"])}<small class="error">{esc(r["last_error"]) if r["last_error"] else ""}</small></td><td>{badge(work_status.lower().replace(" ", "_"))}</td><td>{assignment}<small>{esc(r["reviewer_name"]) if r["reviewer_name"] else ""}</small></td><td>{previous[r["id"]]} prior</td><td>{outcome}</td><td>{retry}</td></tr>'
        title, subtitle = "Audit history", "Selection, recipient routing, and email delivery in one place."
    if tab not in {"template", "failure_template", "outcome", "simulation", "reviewers", "admin"}:
        if not rows:
            rows = f'<tr><td colspan="{12 if tab == "audits" else 6 if tab == "runs" else 5}" class="empty">No {"audits" if tab == "audits" else "records"} yet. The daily job will populate this view.</td></tr>'
        content = f'<section class="stats">{cards}</section><section class="panel"><div class="panel-head"><div><h2>{esc(title)}</h2><p>Showing the most recent {200 if tab != "runs" else 50} records</p></div><span class="live-dot">● &nbsp; Current data</span></div><div class="table-wrap"><table><thead><tr>{headings}</tr></thead><tbody>{rows}</tbody></table></div></section>'
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MLS Audit Desk</title><link rel="stylesheet" href="/static/app.css"></head>
<body><aside class="sidebar"><div class="brand"><img class="brand-logo" src="/static/cornerstone-logo-white.png" alt="Cornerstone Association of REALTORS"><strong class="brand-caption">Compliance Audit Desk</strong></div><div class="sidebar-label">WORKSPACE</div><nav>{nav}</nav><div class="sidebar-label admin-label">ADMIN</div><nav>{admin_nav}</nav><div class="sidebar-foot">Daily selection · {esc(config.timezone)}</div></aside>
<main><header><div><div class="eyebrow">OPERATIONS / {esc(title.upper())}</div><h1>{esc(title)}</h1><p>{esc(subtitle)}</p></div><div class="avatar">AD</div></header>{banner}{'<div class="notice">'+esc(notice)+'</div>' if notice else ''}{content}<footer>Audit Desk · Internal use only</footer></main></body></html>'''


def serve(config):
    if not config.password:
        raise ValueError("APP_PASSWORD is required to serve the staff interface")
    init_db(config.database_path)
    if config.wake_catchup:
        from .job import run_job
        from datetime import timezone
        def catch_up_after_wake():
            while True:
                try:
                    if config.test_window_open():
                        run_job(config, only_if_needed=True)
                except Exception:
                    logging.getLogger("audit_app.web").exception("wake_catchup_failed")
                threading.Event().wait(300)
        threading.Thread(target=catch_up_after_wake, name="audit-wake-catchup", daemon=True).start()
    from pathlib import Path
    try:
        stylesheet = (Path(__file__).parent / "static" / "app.css").read_bytes()
    except OSError:
        stylesheet = b"@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@500;600;700;800&display=swap');\n:root{font-family:'DM Sans',system-ui,sans-serif;color:#182431;background:#f5f6f7;font-size:14px}*{box-sizing:border-box}body{margin:0;display:flex;min-height:100vh}a{color:inherit;text-decoration:none}.sidebar{width:246px;background:#133657;color:#e8eef3;min-height:100vh;padding:30px 16px;display:flex;flex-direction:column;flex-shrink:0}.brand{display:flex;align-items:center;gap:12px;padding:0 10px 54px}.brand-mark{width:38px;height:38px;background:#91c0ef;color:#14293e;border-radius:11px;display:grid;place-items:center;font-family:'Plus Jakarta Sans',sans-serif;font-size:22px;font-weight:800}.brand strong{display:block;font:800 18px 'Plus Jakarta Sans',sans-serif;letter-spacing:-.04em}.brand small{display:block;font-size:9px;letter-spacing:.2em;color:#a8b8c8;margin-top:3px;font-weight:700}.sidebar-label{font-size:10px;font-weight:700;color:#8496a9;letter-spacing:.18em;padding:0 16px;margin-bottom:14px}.sidebar nav{display:grid;gap:5px}.nav-item{padding:13px 16px;border-radius:10px;color:#c5d0dc;font-weight:600}.nav-item:hover,.nav-item.active{background:#243c54;color:white}.nav-item.active{box-shadow:inset 3px 0 #91c0ef}.sidebar-foot{margin-top:auto;border-top:1px solid #324960;padding:22px 10px 0;color:#a3b0be;font-size:11px}main{padding:42px 48px 20px;width:calc(100% - 246px);min-width:0;max-width:1800px;margin:auto}header{display:flex;align-items:start;justify-content:space-between;margin-bottom:26px}.eyebrow{font-size:10px;letter-spacing:.19em;color:#6a7b8c;font-weight:800;margin-bottom:10px}h1,h2{font-family:'Plus Jakarta Sans',sans-serif;letter-spacing:-.04em}h1{font-size:31px;margin:0 0 6px}header p,.panel-head p{color:#727c85;margin:0}.avatar{width:38px;height:38px;border:1px solid #d5dbe1;background:white;border-radius:50%;display:grid;place-items:center;color:#3d4f62;font-size:11px;font-weight:800}.test-banner{background:#fff0d1;border:2px solid #f5aa2c;color:#6e3d06;border-radius:13px;padding:15px 20px;display:flex;align-items:center;gap:17px;margin:0 0 25px;box-shadow:0 5px 15px #a8660d12}.test-banner strong{background:#c4471b;color:white;padding:6px 10px;border-radius:5px;font:800 12px 'Plus Jakarta Sans',sans-serif;letter-spacing:.08em;white-space:nowrap}.test-banner span{font-weight:700}.prod-banner{background:#fee4df;border:2px solid #bb4434;color:#9b281b;padding:15px 20px;border-radius:13px;font-weight:800;letter-spacing:.05em;margin-bottom:25px}.notice{padding:13px 17px;background:#e1eaf2;color:#243b53;border-radius:9px;margin-bottom:20px}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin-bottom:26px}.stat{background:white;border:1px solid #e1e5e9;box-shadow:0 2px 8px #24334207;border-radius:13px;padding:21px 23px;min-height:110px}.stat-label{font-weight:700;color:#798490;font-size:12px;margin-bottom:12px}.stat-value{font:800 29px 'Plus Jakarta Sans',sans-serif;color:#172b3f;line-height:1}.panel{background:white;border:1px solid #e1e5e9;border-radius:14px;box-shadow:0 4px 18px #24334209;overflow:hidden}.panel-head{padding:24px 26px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #e9ecef}.panel-head h2{font-size:17px;margin:0 0 5px}.panel-head p{font-size:12px}.live-dot{color:#4f6c89;font-size:11px;font-weight:700}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;min-width:1040px}th{background:#f9fafb;color:#808a93;font-size:10px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;text-align:left;padding:14px 18px;white-space:nowrap}td{border-top:1px solid #edeff1;padding:16px 18px;vertical-align:top;color:#3a4551;font-size:12px;line-height:1.45;max-width:245px}td strong{color:#1d2f42;font-weight:800;display:block}td small{display:block;color:#838c94;margin-top:5px;font-size:11px;line-height:1.35}td .muted{color:#87919b;font-weight:700}.badge{border-radius:30px;padding:5px 8px;font-size:10px;font-weight:800;display:inline-block;white-space:nowrap}.badge.good{background:#e5eef6;color:#284e74}.badge.bad{background:#fce6e2;color:#b13e36}.badge.neutral{background:#eef0f1;color:#606b76}.mode-test,.mode-prod{font-size:9px;font-weight:900;letter-spacing:.06em;padding:4px 6px;border-radius:3px;margin-right:4px}.mode-test{background:#fff0cf;color:#a05c00}.mode-prod{background:#fce0dd;color:#a72e20}.error{color:#b95a4b!important}.empty{text-align:center;padding:60px;color:#8b959f}button{border:1px solid #b9c6d4;color:#234465;background:#f1f5f9;border-radius:7px;padding:7px 10px;font:700 11px 'DM Sans',sans-serif;cursor:pointer;white-space:nowrap}button:hover{background:#dfe8f2}footer{font-size:11px;color:#a2a8ae;text-align:center;padding:30px}@media(max-width:900px){body{display:block}.sidebar{width:100%;min-height:auto;padding:15px;display:block}.brand{padding:0 4px 14px}.sidebar-label,.sidebar-foot{display:none}.sidebar nav{display:flex;overflow:auto}.nav-item{white-space:nowrap;padding:10px}main{width:100%;padding:25px 18px}.stats{grid-template-columns:repeat(2,1fr)}}@media(max-width:520px){.stats{gap:8px}.stat{padding:16px}.stat-value{font-size:23px}.test-banner{align-items:start;flex-direction:column;gap:9px}h1{font-size:26px}}\n.template-grid{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(330px,.9fr);gap:20px;align-items:start}.template-form{padding:24px 26px}.template-form label{display:block;font-size:12px;font-weight:800;color:#394a5b;margin:0 0 8px}.template-form input[name=subject],.template-form textarea{display:block;width:100%;border:1px solid #cbd2d9;background:#fbfcfd;color:#192939;border-radius:9px;padding:12px 14px;font:500 13px 'DM Sans',system-ui,sans-serif;line-height:1.5;outline:none;margin-bottom:22px}.template-form input:focus,.template-form textarea:focus{border-color:#5d7e9f;box-shadow:0 0 0 3px #dce6ef}.template-form textarea{resize:vertical;min-height:280px}.tag-caption{font-size:11px;font-weight:800;color:#606f7f;margin-bottom:10px}.tag-row{display:flex;flex-wrap:wrap;gap:8px}.tag-button{background:#edf1f5;border-color:#d5dee7;padding:8px 10px}.tag-button code{font:600 11px ui-monospace,SFMono-Regular,Menlo,monospace}.form-help{color:#717c87;font-size:11px;line-height:1.55;margin:16px 0 20px}.form-actions{display:flex;justify-content:flex-end;gap:10px}.form-actions button{font-size:12px;padding:10px 15px}.primary-button{background:#1e3d5c;color:white;border-color:#1e3d5c}.primary-button:hover{background:#172f47}.form-error{background:#fff0ed;color:#9d3024;border:1px solid #e9b8ae;border-radius:8px;padding:11px 13px;margin-bottom:18px;font-weight:700}.preview-content{padding:24px 26px}.preview-subject{font-weight:800;color:#16283b;padding-bottom:16px;border-bottom:1px solid #e3e7eb;overflow-wrap:anywhere}.preview-body{font:12px/1.65 'DM Sans',system-ui,sans-serif;white-space:pre-wrap;overflow-wrap:anywhere;color:#394756;margin:18px 0 0}.preview-empty{color:#858e98}.template-panel,.preview-panel{min-width:0}@media(max-width:1100px){.template-grid{grid-template-columns:1fr}}\n.format-toolbar{display:flex;gap:7px;margin-bottom:8px}.format-button{min-width:34px;min-height:32px;font-size:14px;background:#f7f8fa}.format-button:focus-visible,.tag-button:focus-visible{outline:3px solid #b7cbdf}.body-editor{width:100%;min-height:280px;max-height:540px;overflow:auto;border:1px solid #cbd2d9;background:#fbfcfd;color:#192939;border-radius:9px;padding:12px 14px;font:500 13px/1.55 'DM Sans',system-ui,sans-serif;outline:none;margin-bottom:22px;white-space:pre-wrap;overflow-wrap:anywhere}.body-editor:focus{border-color:#5d7e9f;box-shadow:0 0 0 3px #dce6ef}.body-editor strong,.preview-body strong{font-weight:800}.body-editor em,.preview-body em{font-style:italic}.body-editor u,.preview-body u{text-decoration:underline}\n.outcome-panel{max-width:1050px}.outcome-content{padding:26px}.outcome-content h3{font-size:14px;color:#26384b;margin:24px 0 10px}.outcome-content label{display:block;font-weight:800;color:#394a5b;margin:24px 0 9px}.outcome-content textarea{display:block;width:100%;max-width:800px;min-height:140px;padding:12px 14px;border:1px solid #cbd2d9;border-radius:9px;font:13px/1.55 'DM Sans',system-ui,sans-serif}.outcome-content textarea:focus{outline:3px solid #dce6ef}.outcome-content p{color:#64707d;font-size:12px;line-height:1.6}.pass-form{padding-bottom:20px;border-bottom:1px solid #e3e7eb}.failure-form>button{margin-top:8px}.outcome-preview{margin-top:25px;padding:22px;border:1px solid #d9e0e6;border-radius:11px;background:#fbfcfd}.outcome-preview .preview-body{background:#fff;border:1px solid #edeff1;border-radius:8px;padding:18px;max-height:460px;overflow:auto}.outcome-preview .primary-button{margin-top:18px}.outcome-issues{white-space:pre-wrap;font:13px/1.6 'DM Sans',system-ui,sans-serif;color:#24313f;background:#f8fafb;border:1px solid #e2e8ed;padding:15px;border-radius:8px}.table-wrap td small{overflow-wrap:anywhere}\n.sim-alert{display:flex;gap:16px;align-items:center;background:#e4f0f7;border:1px solid #a9cddf;color:#28546a;border-radius:12px;padding:16px 20px;margin-bottom:22px}.sim-alert strong{background:#2a6c8e;color:white;border-radius:5px;padding:7px 10px;font-size:10px;letter-spacing:.08em;white-space:nowrap}.sim-alert span{font-weight:600;font-size:12px}.sim-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin-bottom:24px}.sim-summary>div{background:white;border:1px solid #e1e5e9;border-radius:12px;padding:18px 20px}.sim-summary span{display:block;color:#7c8691;font-size:11px;font-weight:700;margin-bottom:8px}.sim-summary strong{color:#172f47;font:800 23px 'Plus Jakarta Sans',sans-serif}.sim-flow{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0}.sim-step{display:flex;gap:16px;padding:25px 27px;border-bottom:1px solid #e9ecef}.sim-step:nth-child(odd){border-right:1px solid #e9ecef}.sim-step h3{font:800 14px 'Plus Jakarta Sans',sans-serif;margin:0 0 8px;color:#1f3449}.sim-step strong{display:block;font-size:12px;color:#394756;line-height:1.55}.sim-step p{font-size:11px;color:#828c95;line-height:1.55;margin:6px 0 0}.sim-index{height:30px;min-width:30px;display:grid;place-items:center;background:#e8edf2;color:#335272;border-radius:8px;font:800 11px 'Plus Jakarta Sans',sans-serif}@media(max-width:950px){.sim-summary{grid-template-columns:repeat(2,1fr)}.sim-flow{grid-template-columns:1fr}.sim-step:nth-child(odd){border-right:0}}@media(max-width:520px){.sim-alert{align-items:start;flex-direction:column}}\n"

    class Handler(BaseHTTPRequestHandler):
        def authorized(self):
            header = self.headers.get("Authorization", "")
            try:
                raw = base64.b64decode(header.removeprefix("Basic "), validate=True).decode()
                username, password = raw.split(":", 1)
            except (ValueError, UnicodeDecodeError):
                username, password = "", ""
            return hmac.compare_digest(username, config.username) and hmac.compare_digest(password, config.password)

        def require_auth(self):
            if self.authorized():
                return True
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Audit Desk"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        def do_GET(self):
            if not self.require_auth():
                return
            path = urlparse(self.path)
            if path.path == "/static/app.css":
                body = stylesheet
                content_type = "text/css; charset=utf-8"
            elif path.path == "/static/cornerstone-logo-white.png":
                body = (Path(__file__).parent / "static" / "cornerstone-logo-white.png").read_bytes()
                content_type = "image/png"
            elif path.path == "/":
                query = parse_qs(path.query)
                tab = query.get("tab", ["audits"])[0]
                if tab not in {"audits", "reviewers", "listings", "runs", "template", "failure_template", "outcome", "simulation", "admin"}:
                    tab = "audits"
                audit_id = query.get("id", [""])[0]
                if tab == "outcome" and not audit_id.isdigit():
                    tab = "audits"
                body = render(config, tab, query.get("notice", [""])[0], audit_id=int(audit_id) if tab == "outcome" else None).encode()
                content_type = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if not self.require_auth():
                return
            origin = self.headers.get("Origin")
            host = self.headers.get("Host")
            if origin and urlparse(origin).netloc != host:
                self.send_error(403)
                return
            if self.path == "/admin/selection-rate":
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024:
                    self.send_error(413)
                    return
                try:
                    form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
                except UnicodeDecodeError:
                    self.send_error(400)
                    return
                if not hmac.compare_digest(form.get("token", [""])[0], retry_token(config, "selection-rate")):
                    self.send_error(403)
                    return
                value = form.get("rate_percent", [""])[0]
                try:
                    save_selection_percent(config, value)
                except ValueError as exc:
                    page = render(config, "admin", form_values=value, error=str(exc)).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(page)))
                    self.end_headers()
                    self.wfile.write(page)
                    return
                self.send_response(303)
                self.send_header("Location", "/?tab=admin&notice=" + quote("Selection percentage saved for future runs."))
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/reviewers" or self.path.startswith("/reviewers/") or self.path.startswith("/assignment/"):
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2048:
                    self.send_error(413)
                    return
                try:
                    form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
                except UnicodeDecodeError:
                    self.send_error(400)
                    return
                if self.path == "/reviewers":
                    token_key = "reviewers"
                    item_id = None
                else:
                    prefix, _, item = self.path.rpartition("/")
                    if not item.isdigit() or prefix not in {"/reviewers", "/assignment"}:
                        self.send_error(404)
                        return
                    item_id = int(item)
                    token_key = ("reviewer:" if prefix == "/reviewers" else "assignment:") + item
                if not hmac.compare_digest(form.get("token", [""])[0], retry_token(config, token_key)):
                    self.send_error(403)
                    return
                tab = "audits" if self.path.startswith("/assignment/") else "reviewers"
                try:
                    if self.path == "/reviewers":
                        add_reviewer(config, form.get("name", [""])[0])
                        notice = "Name added to the audit team."
                    elif tab == "reviewers":
                        action = form.get("action", [""])[0]
                        if action == "rename":
                            rename_reviewer(config, item_id, form.get("name", [""])[0])
                            notice = "Name updated."
                        elif action in {"activate", "deactivate"}:
                            set_reviewer_active(config, item_id, action == "activate")
                            notice = "Audit team list updated."
                        else:
                            self.send_error(400)
                            return
                    else:
                        value = form.get("reviewer_id", [""])[0]
                        if value and not value.isdigit():
                            raise ValueError("Choose a name from the list.")
                        assign_reviewer(config, item_id, int(value) if value else None)
                        notice = "Audit assignment updated."
                except ValueError as exc:
                    if tab == "reviewers":
                        page = render(config, tab, error=str(exc)).encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Content-Length", str(len(page)))
                        self.end_headers()
                        self.wfile.write(page)
                        return
                    notice = str(exc)
                self.send_response(303)
                self.send_header("Location", "/?tab=" + tab + "&notice=" + quote(notice))
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path in {"/template", "/failure-template"}:
                failure = self.path == "/failure-template"
                kind = "failure" if failure else "request"
                tab = "failure_template" if failure else "template"
                subject, body = "", ""
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 32768:
                        self.send_error(413)
                        return
                    form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
                    token = form.get("token", [""])[0]
                    if not hmac.compare_digest(token, retry_token(config, "failure-template" if failure else "template")):
                        self.send_error(403)
                        return
                    subject = form.get("subject", [""])[0]
                    body = form.get("body", [""])[0]
                    action = form.get("action", [""])[0]
                    if action not in {"preview", "save"}:
                        self.send_error(400)
                        return
                    body_format = form.get("body_format", ["plain"])[0]
                    validate_templates(subject, body, body_format, kind)
                    if action == "save":
                        (save_failure_templates if failure else save_templates)(config, subject, body, body_format)
                        self.send_response(303)
                        self.send_header("Location", "/?tab=" + tab + "&notice=Template%20saved%20for%20future%20emails.")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    page = render(config, tab, form_values=(subject, body, body_format)).encode()
                except (UnicodeDecodeError, ValueError) as exc:
                    page = render(config, tab, form_values=(subject, body, form.get("body_format", ["plain"])[0] if "form" in locals() else "plain"), error=str(exc)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
                return
            if self.path.startswith("/outcome/") and self.path[9:].isdigit():
                audit_id = int(self.path[9:])
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 12000:
                    self.send_error(413)
                    return
                form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
                if not hmac.compare_digest(form.get("token", [""])[0], retry_token(config, f"outcome:{audit_id}")):
                    self.send_error(403)
                    return
                outcome = form.get("outcome", [""])[0]
                action = form.get("action", [""])[0]
                issues = form.get("issues", [""])[0]
                try:
                    if outcome == "passed" and action == "record":
                        record_outcome(config, audit_id, "passed")
                    elif outcome == "failed" and action == "preview":
                        if not issues.strip():
                            raise ValueError("Describe the issues before previewing the notice.")
                        page = render(config, "outcome", form_values=issues, audit_id=audit_id, preview_outcome=True).encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Content-Length", str(len(page)))
                        self.end_headers()
                        self.wfile.write(page)
                        return
                    elif outcome == "failed" and action == "send":
                        record_outcome(config, audit_id, "failed", issues)
                    else:
                        self.send_error(400)
                        return
                except ValueError as exc:
                    page = render(config, "outcome", form_values=issues, error=str(exc), audit_id=audit_id).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(page)))
                    self.end_headers()
                    self.wfile.write(page)
                    return
                self.send_response(303)
                self.send_header("Location", "/?tab=audits&notice=Audit%20result%20recorded.%20Check%20notice%20status%20below.")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path.startswith("/failure-retry/") and self.path[15:].isdigit():
                audit_id = int(self.path[15:])
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2048:
                    self.send_error(413)
                    return
                form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
                if not hmac.compare_digest(form.get("token", [""])[0], retry_token(config, f"failure:{audit_id}")):
                    self.send_error(403)
                    return
                with connect(config.database_path) as db:
                    row = db.execute("SELECT failure_email_status FROM audits WHERE id=?", (audit_id,)).fetchone()
                sent = deliver_failure_notice(config, audit_id, retry=bool(row and row["failure_email_status"] == "email_failed"))
                self.send_response(303)
                self.send_header("Location", "/?tab=audits&notice=" + quote("Failed-audit notice accepted by SendGrid." if sent else "Notice was not sent. Check its status."))
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if not self.path.startswith("/retry/") or not self.path[7:].isdigit():
                self.send_error(404)
                return
            audit_id = int(self.path[7:])
            length = int(self.headers.get("Content-Length", "0"))
            if length > 2048:
                self.send_error(413)
                return
            form = parse_qs(self.rfile.read(length).decode())
            token = form.get("token", [""])[0]
            if not hmac.compare_digest(token, retry_token(config, audit_id)):
                self.send_error(403)
                return
            result = deliver_audit(config, audit_id, retry=True)
            notice = "Email retry accepted by SendGrid." if result else "Retry was not sent. Check its status and error below."
            self.send_response(303)
            self.send_header("Location", "/?notice=" + quote(notice))
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):
            logging.getLogger("audit_app.web").info("request %s", self.path.split("?")[0])

    server = ThreadingHTTPServer((config.host, config.port), Handler)
    print(f"Audit Desk at http://{config.host}:{config.port}")
    server.serve_forever()
