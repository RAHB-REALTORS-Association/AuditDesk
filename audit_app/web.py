import html
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import g, has_request_context
from .security import allowed, csrf_token

from .brokerage_report import PERIODS, brokerage_statistics
from .database import connect
from .emailer import EmailError, resolve_recipients
from .report import daily_audit_report
from .settings import display_percent, selection_percent
from .simulation import simulate_cycle
from .templates import (FIELDS, FAILURE_FIELDS, clean_html, current_failure_templates, current_templates,
                        format_message_parts, plain_to_html, validate_templates)


def esc(value):
    return html.escape(str(value or "—"), quote=True)


def local_time(value, zone):
    if not value:
        return "—"
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(ZoneInfo(zone)).strftime("%b %d, %Y · %I:%M %p")


def badge(status):
    kind = "good" if status in {"email_sent", "completed", "passed", "in_progress"} else "bad" if status in {"email_failed", "email_unknown", "email_blocked", "failed", "completed_with_email_errors", "needs_reassignment", "run_had_errors"} else "neutral"
    label = status.replace("_", " ").title()
    return f'<span class="badge {kind}">{esc(label)}</span>'


def sort_heading(label, kind="text", initial=False):
    direction = ' aria-sort="descending"' if initial else ''
    return f'<th{direction}><button type="button" class="sort-button" data-sort-type="{kind}">{esc(label)}</button></th>'


def recipients(value):
    try:
        return ", ".join(json.loads(value)) or "—"
    except (ValueError, TypeError):
        return "—"


def retry_token(config, audit_id):
    return csrf_token(config, audit_id)


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


def report_view(config):
    daily = daily_audit_report(config)
    total = sum(row["listings"] for row in daily)
    audited = sum(row["audited"] for row in daily)
    overall = f"{100 * audited / total:.1f}%" if total else "—"
    rows = ""
    for row in daily:
        recorded = row["status"] in {"Recorded", "Run had errors"} or bool(row["listings"])
        listings = str(row["listings"]) if recorded else "—"
        selected = str(row["audited"]) if recorded else "—"
        percentage = f'{row["percentage"]:.1f}%' if row["percentage"] is not None else "—"
        rows += f'<tr><td><strong>{row["date"].strftime("%b %d, %Y")}</strong></td><td>{listings}</td><td>{selected}</td><td>{percentage}</td><td>{badge(row["status"].lower().replace(" ", "_"))}</td></tr>'
    return f'''<section class="report-summary"><div><span>Listings considered</span><strong>{total}</strong></div>
        <div><span>Audited</span><strong>{audited}</strong></div><div><span>Audit percentage</span><strong>{overall}</strong></div></section>
        <section class="panel report-panel"><div class="panel-head"><div><h2>Daily audit report</h2><p>Past 90 days, newest first</p></div></div>
        <div class="report-note">Counts are unique new Active listings first processed by the app on each date in {esc(config.timezone)}, including manual test runs. A day without a completed run is shown as unavailable rather than zero.</div>
        <div class="table-wrap"><table class="report-table"><thead><tr><th>Date</th><th>Total listings</th><th>Audited listings</th><th>Audit %</th><th>Run status</th></tr></thead><tbody>{rows}</tbody></table></div></section>'''


def brokerage_view(config, period):
    report = brokerage_statistics(config, period)
    def rate_text(value):
        return f"{value:.1f}%" if value is not None else "—"
    percentage = f'{report["percentage"]:.1f}%' if report["percentage"] is not None else "—"
    pass_rate = f'{report["pass_rate"]:.1f}%' if report["pass_rate"] is not None else "—"
    fail_rate = f'{report["fail_rate"]:.1f}%' if report["fail_rate"] is not None else "—"
    options = "".join(f'<option value="{key}" {"selected" if key == period else ""}>{label}</option>'
                      for key, (_, label) in PERIODS.items())
    def cells(row):
        return (f'<td data-sort="{row["listings"]}">{row["listings"]:,}</td>'
                f'<td data-sort="{row["audited"]}">{row["audited"]:,}</td>'
                f'<td data-sort="{row["percentage"]}">{row["percentage"]:.1f}%</td>'
                f'<td data-sort="{row["passed"]}">{row["passed"]:,}</td>'
                f'<td data-sort="{row["pass_rate"] if row["pass_rate"] is not None else ""}">{rate_text(row["pass_rate"])}</td>'
                f'<td data-sort="{row["failed"]}">{row["failed"]:,}</td>'
                f'<td data-sort="{row["fail_rate"] if row["fail_rate"] is not None else ""}">{rate_text(row["fail_rate"])}</td>')
    rows = ""
    for index, row in enumerate(report["rows"]):
        branch_label = "branch" if row["branch_count"] == 1 else "branches"
        address = row["branches"][0]["address"] if row["branch_count"] == 1 else None
        details = f'<small>{esc(address) if address else str(row["branch_count"]) + " " + branch_label}</small>'
        button = (f'<button type="button" class="branch-toggle" aria-expanded="false" '
                  f'aria-controls="brokerage-branches-{index}" data-count="{row["branch_count"]}">'
                  f'View {row["branch_count"]} {branch_label}</button>')
        parent = (f'<tr class="brokerage-parent"><td data-sort="{esc(row["name"])}">'
                  f'<strong>{esc(row["name"])}</strong>{details}{button}</td>{cells(row)}</tr>')
        branches = "".join(
            f'<tr class="branch-row" id="brokerage-branches-{index}-{branch_index}" hidden>'
            f'<td><span class="branch-indent">{esc(branch["address"] or "Address unavailable")}</span>'
            f'<small>Office {esc(branch["office_id"] or "unknown")}</small></td>{cells(branch)}</tr>'
            for branch_index, branch in enumerate(row["branches"]))
        rows += f'<tbody class="brokerage-group" id="brokerage-branches-{index}">{parent}{branches}</tbody>'
    if not rows:
        rows = '<tbody><tr><td colspan="8" class="empty">No listings were recorded in this period.</td></tr></tbody>'
    coverage = (f'Available app records begin {report["first_recorded"]:%b %d, %Y}; earlier dates in this period have no app history.'
                if report["first_recorded"] and report["first_recorded"] > report["start"] else
                "The selected period is covered by available app history.")
    headings = "".join((sort_heading("Brokerage"), sort_heading("Listings", "number", True),
                        sort_heading("Audited", "number"), sort_heading("Audit %", "number"),
                        sort_heading("Passed", "number"), sort_heading("Pass rate", "number"),
                        sort_heading("Failed", "number"), sort_heading("Fail rate", "number")))
    return f'''<div class="brokerage-controls"><form method="get" action="/" class="brokerage-period-form">
        <input type="hidden" name="tab" value="brokerages"><label for="brokerage-period">Reporting period</label>
        <select id="brokerage-period" name="period">{options}</select><button type="submit">Apply</button></form>
        <a class="primary-button report-download" href="/reports/brokerages.pdf?period={period}">Export branded PDF</a></div>
        <section class="report-summary brokerage-summary"><div><span>Listings considered</span><strong>{report["total"]:,}</strong></div>
        <div><span>Audited</span><strong>{report["audited"]:,}</strong></div>
        <div><span>Audit percentage</span><strong>{percentage}</strong></div>
        <div><span>Completed audits</span><strong>{report["completed"]:,}</strong></div>
        <div><span>Pass rate</span><strong>{pass_rate}</strong></div>
        <div><span>Fail rate</span><strong>{fail_rate}</strong></div></section>
        <section class="panel brokerage-panel"><div class="panel-head"><div><h2>Brokerage statistics</h2>
        <p>{report["start"]:%b %d, %Y} – {report["end"]:%b %d, %Y} · {report["period_label"].title()} · {len(report["rows"]):,} brokerages</p></div></div>
        <div class="report-note">Branches with the same brokerage name are combined. Select “View branches” to see each office address and its statistics. Listings are unique new Active listings first processed by this app; audited listings have a saved audit selection. Pass and fail rates use completed audits only ({report["completed"]:,} of {report["audited"]:,} selected audits have a result). Includes manual test runs. {esc(coverage)} This is not a count of every MLS listing.</div>
        <div class="table-wrap"><table class="brokerage-table" data-sortable data-sort-groups><thead><tr>{headings}</tr></thead>{rows}</table></div></section>'''


def render(config, tab="audits", notice="", form_values=None, error="", audit_id=None, preview_outcome=False, period="3m"):
    with connect(config.database_path) as db:
        if not db.in_transaction:
            db.execute("BEGIN")
        revision = db.execute("SELECT COALESCE(max(id),0) FROM activity_events").fetchone()[0]
        counts = {
            "processed": db.execute("SELECT count(*) FROM listings").fetchone()[0],
            "selected": db.execute("SELECT count(*) FROM audits").fetchone()[0],
            "sent": db.execute("SELECT count(*) FROM audits WHERE email_status='email_sent'").fetchone()[0],
            "failed": db.execute("SELECT count(*) FROM audits WHERE email_status IN ('email_failed','email_unknown')").fetchone()[0],
        }
        audits = db.execute("""SELECT a.*, l.mls_number, l.address, l.agent_email,
            COALESCE(ar.name,a.reviewer_name_snapshot) AS reviewer_name, ar.active AS reviewer_active FROM audits a
            JOIN listings l ON l.id=a.listing_id LEFT JOIN audit_reviewers ar ON ar.id=a.reviewer_id
            ORDER BY a.selected_at DESC LIMIT 200""").fetchall()
        reviewers = db.execute("SELECT * FROM audit_reviewers ORDER BY active DESC, name COLLATE NOCASE").fetchall()
        listings = db.execute("SELECT * FROM listings ORDER BY first_processed_at DESC LIMIT 200").fetchall()
        runs = db.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 50").fetchall()
        previous = {row["id"]: db.execute("SELECT count(*) FROM audits WHERE brokerage_id=? AND selected_at<?", (row["brokerage_id"], row["selected_at"])).fetchone()[0] if row["brokerage_id"] else 0 for row in audits}
    nav = "".join(f'<a class="nav-item {"active" if tab == name else ""}" href="/?tab={name}">{label}</a>' for name, label in (("audits", "Audit history"), ("listings", "Listings considered"), ("runs", "Scheduled runs"), ("simulation", "Simulation")))
    admin_nav = "".join(f'<a class="nav-item {"active" if tab == name else ""}" href="/?tab={name}">{label}</a>' for name, label in (("admin", "Selection settings"), ("reviewers", "Audit team"), ("report", "Daily audit report"), ("brokerages", "Brokerage statistics"), ("template", "Audit request email"), ("failure_template", "Failed-audit email"), ("users", "Access management"), ("activity", "Activity log")) if allowed(TAB_CAPABILITIES.get(name, "audits.read")))
    if config.test_mode:
        detail = "All outgoing messages are redirected exclusively to the administrator."
        if config.test_end_at:
            detail += f" Live-data test ends {local_time(config.test_end_at.isoformat(), config.timezone)}."
            if not config.test_window_open():
                detail = "The live-data test has ended. Scheduled queries and all email sends are blocked."
        banner = f'<div class="test-banner"><strong>TEST MODE</strong><span>{esc(detail)}</span></div>'
    else:
        banner = '<div class="prod-banner">PRODUCTION EMAIL ENABLED</div>'
    if not config.email_enabled:
        banner = '<div class="test-banner"><strong>EMAIL DISABLED</strong><span>No messages will be sent. Audits and results remain available for review.</span></div>'
    cards = "".join(f'<div class="stat"><div class="stat-label">{label}</div><div class="stat-value">{counts[key]}</div></div>' for key, label in (("processed", "Listings considered"), ("selected", "Selected audits"), ("sent", "Emails accepted"), ("failed", "Needs attention")))
    if tab == "users":
        title, subtitle = "Access management", "Application roles for individually authenticated people."
        content = users_view(config)
    elif tab == "activity":
        title, subtitle = "Activity log", "Recorded changes and the authenticated person or system responsible."
        content = activity_view(config)
    elif tab == "simulation":
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
    elif tab == "report":
        title, subtitle = "Daily audit report", "See listings considered and selected for audit by day."
        content = report_view(config)
    elif tab == "brokerages":
        title, subtitle = "Brokerage statistics", "Review audit selection across brokerages."
        content = brokerage_view(config, period)
    elif tab == "listings":
        headings = "".join((sort_heading("MLS / Property"), sort_heading("Entered", "date"), sort_heading("Brokerage"),
                            sort_heading("Listing agent"), sort_heading("Processing status")))
        rows = "".join(f'<tr><td data-sort="{esc(r["mls_number"])}"><strong>{esc(r["mls_number"])}</strong><small>{esc(r["address"])}</small></td><td data-sort="{esc(r["entry_timestamp"])}">{esc(local_time(r["entry_timestamp"], config.timezone))}</td><td>{esc(r["brokerage_name"])}</td><td>{esc(r["agent_name"])}</td><td>{badge(r["processing_status"])}</td></tr>' for r in listings)
        title, subtitle = "Listings considered", "Every new active listing evaluated by the daily selection job."
    elif tab == "runs":
        headings = "<th>Started</th><th>Status</th><th>Fetched</th><th>New</th><th>Selected</th><th>Error</th>"
        rows = "".join(f'<tr><td>{esc(local_time(r["started_at"], config.timezone))}</td><td>{badge(r["status"])}</td><td>{r["fetched_count"]}</td><td>{r["new_count"]}</td><td>{r["selected_count"]}</td><td class="error">{esc(r["error"])}</td></tr>' for r in runs)
        title, subtitle = "Scheduled runs", "Recent daily jobs and any errors they encountered."
    else:
        headings = "".join((sort_heading("MLS / Property"), sort_heading("Selected", "date", True),
                            sort_heading("Brokerage / Broker"), sort_heading("Agent"), sort_heading("Intended recipients"),
                            sort_heading("Actual recipient"), sort_heading("Mode / Status"), sort_heading("Work status"),
                            sort_heading("Assigned to"), sort_heading("History", "number"), sort_heading("Outcome"), "<th>Actions</th>"))
        rows = ""
        for r in audits:
            retry = f'<form method="post" action="/retry/{r["id"]}"><input type="hidden" name="token" value="{retry_token(config, r["id"])}"><button type="submit">Retry email</button></form>' if r["email_status"] == "email_failed" and allowed("email.retry") else ""
            outcome = f'<a href="/?tab=outcome&id={r["id"]}">Record result</a>' if not r["outcome"] and r["email_status"] == "email_sent" and allowed("audits.result") else (f'{badge(r["outcome"])}<small>Notice: {esc((r["failure_email_status"] or "pending").replace("_", " "))}</small><small>{esc(r["issues"])}</small>' if r["outcome"] == "failed" else badge(r["outcome"]) if r["outcome"] else "—")
            if outcome.startswith("<a "):
                retry = outcome + retry
                outcome = "—"
            if r["outcome"] == "failed" and r["failure_email_status"] in {"email_pending", "email_failed"} and config.test_window_open() and allowed("email.retry"):
                failure_token = retry_token(config, "failure:" + str(r["id"]))
                retry += f'<form method="post" action="/failure-retry/{r["id"]}"><input type="hidden" name="token" value="{failure_token}"><button type="submit">Send failed notice</button></form>'
            mode = '<span class="mode-test">TEST</span>' if r["test_mode"] else '<span class="mode-prod">LIVE</span>'
            work_status = ("Completed" if r["outcome"] else "In progress" if r["reviewer_id"]
                           else "Needs reassignment" if r["reviewer_name_snapshot"] else "Not started")
            options = '<option value="">Unassigned</option>' + "".join(
                f'<option value="{person["id"]}" {"selected" if person["id"] == r["reviewer_id"] else ""}>{esc(person["name"])}{" (inactive)" if not person["active"] else ""}</option>'
                for person in reviewers if person["active"] or person["id"] == r["reviewer_id"])
            assignment_token = retry_token(config, f'assignment:{r["id"]}')
            assignment = f'<form method="post" action="/assignment/{r["id"]}" class="assignment-form"><input type="hidden" name="token" value="{assignment_token}"><select name="reviewer_id" aria-label="Assign MLS {esc(r["mls_number"])}">{options}</select><button type="submit">Save</button></form>'
            if not allowed("audits.assign"):
                assignment = ""
            reviewer_label = ("Previously assigned: " if not r["reviewer_id"] and r["reviewer_name_snapshot"] else "") + (r["reviewer_name"] or "")
            rows += f'<tr><td data-sort="{esc(r["mls_number"])}"><strong>{esc(r["mls_number"])}</strong><small>{esc(r["address"])}</small></td><td data-sort="{esc(r["selected_at"])}">{esc(local_time(r["selected_at"], config.timezone))}</td><td data-sort="{esc(r["brokerage_name"])}">{esc(r["brokerage_name"])}<small>{esc(r["broker_name"])}</small></td><td data-sort="{esc(r["agent_name"])}">{esc(r["agent_name"])}<small>{esc(r["agent_email"])}</small></td><td><span class="muted">To:</span> {esc(recipients(r["intended_to"]))}<small>CC: {esc(recipients(r["intended_cc"]))}</small></td><td>{esc(recipients(r["actual_recipients"]))}</td><td data-sort="{esc(r["email_status"])}">{mode} {badge(r["email_status"])}<small class="error">{esc(r["last_error"]) if r["last_error"] else ""}</small></td><td>{badge(work_status.lower().replace(" ", "_"))}</td><td data-sort="{esc(reviewer_label or "Unassigned")}">{assignment}<small>{esc(reviewer_label) if reviewer_label else ""}</small></td><td data-sort="{previous[r["id"]]}">{previous[r["id"]]} prior</td><td>{outcome}</td><td>{retry}</td></tr>'
        title, subtitle = "Audit history", "Selection, recipient routing, and email delivery in one place."
    if tab not in {"template", "failure_template", "outcome", "simulation", "reviewers", "admin", "report", "brokerages", "users", "activity"}:
        if not rows:
            rows = f'<tr><td colspan="{12 if tab == "audits" else 6 if tab == "runs" else 5}" class="empty">No {"audits" if tab == "audits" else "records"} yet. The daily job will populate this view.</td></tr>'
        content = f'<section class="stats">{cards}</section><section class="panel"><div class="panel-head"><div><h2>{esc(title)}</h2><p>Showing the most recent {200 if tab != "runs" else 50} records{" · Click a column heading to sort these records" if tab in {"audits", "listings"} else ""}</p></div><span class="live-dot">● &nbsp; Current data</span></div><div class="table-wrap"><table{" data-sortable" if tab in {"audits", "listings"} else ""}><thead><tr>{headings}</tr></thead><tbody>{rows}</tbody></table></div></section>'
    content = content.replace('</form>', f'<input type="hidden" name="revision" value="{revision}"></form>')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MLS Audit Desk</title><link rel="stylesheet" href="/static/app.css"></head>
<body><a class="skip-link" href="#main">Skip to content</a><button class="menu-toggle" type="button" aria-controls="sidebar" aria-expanded="false">Menu</button><button class="menu-backdrop" type="button" aria-label="Close menu" hidden></button><aside class="sidebar" id="sidebar"><button class="menu-close" type="button">Close menu</button><div class="brand"><img class="brand-logo" src="/static/cornerstone-logo-white.png" alt="Cornerstone Association of REALTORS"><strong class="brand-caption">Compliance Audit Desk</strong></div><div class="sidebar-label">WORKSPACE</div><nav>{nav}</nav><div class="sidebar-label admin-label">ADMIN</div><nav>{admin_nav}</nav><div class="sidebar-foot">Daily selection · {esc(config.timezone)}</div></aside>
<main id="main"><header><div><div class="eyebrow">OPERATIONS / {esc(title.upper())}</div><h1>{esc(title)}</h1><p>{esc(subtitle)}</p></div><div class="identity">{esc(g.principal.name) if has_request_context() else "Audit Desk"}<small>{esc(g.principal.role) if has_request_context() else ""}</small></div></header>{banner}{'<div class="notice">'+esc(notice)+'</div>' if notice else ''}{content}<footer>Audit Desk · Internal use only</footer></main><script src="/static/shell.js" defer></script><script src="/static/sort.js" defer></script><script src="/static/branches.js" defer></script></body></html>'''



TAB_CAPABILITIES = {
    "audits": "audits.read", "listings": "audits.read", "runs": "audits.read",
    "simulation": "audits.read", "outcome": "audits.result", "reviewers": "reviewers.manage",
    "admin": "settings.manage", "template": "templates.manage", "failure_template": "templates.manage",
    "report": "reports.read", "brokerages": "reports.read", "users": "users.manage", "activity": "activity.read",
}


def users_view(config):
    with connect(config.database_path) as db:
        users = db.execute("SELECT * FROM app_users ORDER BY email").fetchall()
    def form(user=None):
        user = dict(user) if user else {"email": "", "display_name": "", "role": "reviewer", "active": 1, "version": 0}
        roles = "".join(f'<option value="{role}" {"selected" if role == user["role"] else ""}>{label}</option>' for role, label in (("reviewer", "Reviewer"), ("manager", "Audit manager"), ("admin", "IT administrator")))
        return f'<form method="post" action="/users" class="access-form"><input type="hidden" name="token" value="{retry_token(config, "users")}"><input type="hidden" name="version" value="{user["version"]}"><label>Email <input type="email" name="email" required value="{html.escape(user["email"], quote=True)}" {"readonly" if user["email"] else ""}></label><label>Display name <input name="display_name" maxlength="100" value="{html.escape(user["display_name"], quote=True)}"></label><label>Role <select name="role">{roles}</select></label><label><input type="checkbox" name="active" value="1" {"checked" if user["active"] else ""}> Active</label><button type="submit">{"Save access" if user["email"] else "Grant access"}</button></form>'
    return '<section class="panel"><div class="panel-head"><h2>People and roles</h2></div><p class="form-help">Cloudflare verifies identity. Only active people listed here may use AuditDesk. Bootstrap administrators are protected.</p>' + "".join(form(user) for user in users) + '<h3 class="form-help">Add a person</h3>' + form() + '</section>'


def activity_view(config):
    with connect(config.database_path) as db:
        events = db.execute("""SELECT e.*,u.email FROM activity_events e LEFT JOIN app_users u ON u.subject=e.actor
            ORDER BY e.id DESC LIMIT 200""").fetchall()
    rows = "".join(f'<tr><td>{esc(e["occurred_at"])}</td><td>{esc(e["email"] or e["actor"])}</td><td>{esc(e["action"])}</td><td>{esc(e["target"])}</td><td>{esc(e["detail"])}</td></tr>' for e in events)
    return '<section class="panel"><div class="panel-head"><h2>Latest 200 changes</h2><a href="/activity.csv">Export CSV</a></div><div class="table-wrap"><table><thead><tr><th>Time (UTC)</th><th>Actor</th><th>Action</th><th>Target</th><th>Details</th></tr></thead><tbody>' + (rows or '<tr><td colspan="5">No changes recorded yet.</td></tr>') + '</tbody></table></div></section>'


def serve(config):
    from .application import create_app
    from .runtime import start_runtime
    app = create_app(config)
    app.extensions["auditdesk_runtime"] = start_runtime(config)
    app.run(host=config.host, port=config.port, debug=False)
