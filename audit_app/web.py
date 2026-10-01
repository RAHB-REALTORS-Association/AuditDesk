"""Page composition and navigation for the staff interface.

Feature-specific HTML belongs in views/. Request handling belongs in application.py.
"""
from flask import g, has_request_context, request
import re

from .assignment import can_audit, eligible_assignees
from .database import connect
from .lists import query_page
from .security import allowed
from .views.common import badge, esc, local_time, recipients, retry_token, sort_heading
from .views.emails import template_editor
from .views.management import activity_view, admin_view, users_view
from .views.reports import brokerage_view, report_view
from .views.recovery import recovery_view
from .views.workflow import outcome_view


TAB_CAPABILITIES = {
    "audits": "audits.read", "listings": "audits.read", "runs": "audits.read",
    "outcome": "audits.result",
    "admin": "settings.manage", "manage": "settings.manage", "template": "templates.manage", "failure_template": "templates.manage",
    "report": "reports.read", "brokerages": "reports.read", "users": "users.manage", "activity": "activity.read", "recovery": "backups.manage",
}



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
        audits, listings, runs = [], [], []
        reviewers = eligible_assignees(db)
        if tab == "audits":
            audits, list_page = query_page(db, tab, """SELECT a.*, l.mls_number, l.address, l.agent_email,
                COALESCE(NULLIF(ar.display_name,''),ar.email) AS reviewer_name, ar.active AS active, ar.role AS role,
                (SELECT count(*) FROM audits prior WHERE prior.brokerage_id=a.brokerage_id AND prior.selected_at<a.selected_at) AS prior_count,
                CASE WHEN a.outcome IS NOT NULL THEN 'completed' WHEN ar.active=1 THEN 'in_progress'
                  WHEN a.assignee_user_id IS NOT NULL THEN 'needs_reassignment' ELSE 'not_started' END AS work_status
                FROM audits a JOIN listings l ON l.id=a.listing_id LEFT JOIN app_users ar ON ar.id=a.assignee_user_id""",
                ('mls_number','address','brokerage_name','broker_name','agent_name','agent_email','reviewer_name'),
                'email_status', {'MLS / Property':'mls_number','Selected':'selected_at','Brokerage / Broker':'brokerage_name',
                'Agent':'agent_name','Intended recipients':'intended_to','Actual recipient':'actual_recipients',
                'Mode / Status':'email_status','Assigned to':'reviewer_name','Outcome':'outcome','Work status':'work_status','History':'prior_count'}, 'selected_at')
        elif tab == "listings":
            listings, list_page = query_page(db, tab, 'SELECT * FROM listings',
                ('mls_number','address','brokerage_name','agent_name'), 'processing_status',
                {'MLS / Property':'mls_number','Entered':'entry_timestamp','Brokerage':'brokerage_name',
                 'Listing agent':'agent_name','Processing status':'processing_status'}, 'first_processed_at')
        elif tab == "runs":
            source = """SELECT r.*,
                (SELECT count(*) FROM audits a WHERE a.selected_at=r.started_at
                  AND a.email_status IN ('email_failed','email_unknown','email_blocked')) AS email_issues,
                (SELECT count(*) FROM audits a WHERE a.selected_at=r.started_at
                  AND a.email_status='email_pending') AS email_pending
                FROM runs r"""
            source = f"""SELECT *, CASE WHEN status='completed' AND email_issues>0 THEN 'completed_with_email_errors'
                WHEN status='completed' AND email_pending>0 THEN 'completed_with_pending_email'
                ELSE status END AS display_status FROM ({source})"""
            runs, list_page = query_page(db, tab, source, ('started_at','display_status','error'), 'display_status',
                {'Started':'started_at','Status':'display_status','Fetched':'fetched_count','New':'new_count',
                 'Selected':'selected_count','Error':'error'}, 'started_at')

    nav = "".join(f'<a class="nav-item {"active" if tab == name or name == "manage" and tab in {"admin", "template", "failure_template", "users", "recovery"} else ""}" href="/?tab={name}">{label}</a>' for name, label in (("audits", "Audit history"), ("listings", "Listings considered"), ("runs", "Scheduled runs")))
    admin_nav = "".join(f'<a class="nav-item {"active" if tab == name or name == "manage" and tab in {"admin", "template", "failure_template", "users", "recovery"} else ""}" href="/?tab={name}">{label}</a>' for name, label in (("report", "Daily audit report"), ("brokerages", "Brokerage statistics"), ("manage", "Manage"), ("activity", "Activity log")) if allowed(TAB_CAPABILITIES.get(name, "audits.read")))
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
    if config.env == "development":
        banner = '<div class="test-banner"><strong>DEVELOPMENT SANDBOX</strong><span>Synthetic listings only. Email records are simulated; no messages can be sent. Changes reset on restart.</span></div>'
    cards = "".join(f'<div class="stat"><div class="stat-label">{label}</div><div class="stat-value">{counts[key]}</div></div>' for key, label in (("processed", "Listings considered"), ("selected", "Selected audits"), ("sent", "Emails accepted"), ("failed", "Needs attention")))
    if tab == "users":
        title, subtitle = "Access management", "Application roles for individually authenticated people."
        content = users_view(config)
    elif tab == "recovery":
        title, subtitle = "Backup and restore", "Download a backup and prepare a validated restore."
        content = recovery_view(config, request.args.get('restore', '') if has_request_context() else '')
    elif tab == "activity":
        title, subtitle = "Activity log", "Recorded changes and the authenticated person or system responsible."
        content = activity_view(config)
    elif tab == "template":
        title, subtitle = "Email template", "Edit the audit request and preview merge tags before saving."
        content = template_editor(config, form_values, error)
    elif tab == "failure_template":
        title, subtitle = "Failed-audit email", "Edit the notice sent when an audit is marked failed."
        content = template_editor(config, form_values, error, "failure")
    elif tab == "outcome":
        title, subtitle = "Record audit result", "Mark this audit passed, or describe issues and review its failure notice."
        content = outcome_view(config, audit_id, form_values or "", error, preview_outcome)
    elif tab in {"admin", "manage"}:
        title, subtitle = "Manage", "Selection settings, email wording, and application access."
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
        headings = "".join(sort_heading(label, "number" if label in {"Fetched", "New", "Selected"} else "text") for label in ("Started", "Status", "Fetched", "New", "Selected", "Error"))
        rows = ""
        for r in runs:
            status = r["display_status"]
            detail = r["error"] or (f'{r["email_issues"]} email(s) need attention; see audit history' if r["email_issues"] else
                                    f'{r["email_pending"]} email(s) pending; check email configuration' if r["email_pending"] else "")
            if status == "completed" and r["email_pending"]:
                status = "completed_with_pending_email"
            rows += f'<tr><td>{esc(local_time(r["started_at"], config.timezone))}</td><td>{badge(status)}</td><td>{r["fetched_count"]}</td><td>{r["new_count"]}</td><td>{r["selected_count"]}</td><td class="error">{esc(detail)}</td></tr>'
        title, subtitle = "Scheduled runs", "Recent daily jobs and any errors they encountered."
    else:
        headings = "".join((sort_heading("MLS / Property"), sort_heading("Selected", "date", True),
                            sort_heading("Brokerage / Broker"), sort_heading("Agent"), sort_heading("Intended recipients"),
                            sort_heading("Actual recipient"), sort_heading("Mode / Status"), sort_heading("Work status"),
                            sort_heading("Assigned to"), sort_heading("History", "number"), sort_heading("Outcome"), "<th>Actions</th>"))
        selectable = allowed("audits.assign")
        if selectable:
            headings = '<th class="audit-select"><input type="checkbox" data-select-all aria-label="Select all displayed audits"></th>' + headings
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
            eligible = can_audit(r)
            work_status = ("Completed" if r["outcome"] else "In progress" if eligible
                           else "Needs reassignment" if r["assignee_user_id"] else "Not started")
            options = '<option value="">Unassigned</option>' + "".join(
                f'<option value="{person["id"]}" {"selected" if person["id"] == r["assignee_user_id"] else ""}>{esc(person["display_name"] or person["email"])}{(" · " + esc(person["email"])) if person["display_name"] else ""}</option>'
                for person in reviewers)
            if r["assignee_user_id"] and not eligible:
                options += f'<option value="{r["assignee_user_id"]}" selected disabled>{esc(r["reviewer_name"])} (unavailable)</option>'
            assignment_token = retry_token(config, f'assignment:{r["id"]}')
            assignment = f'<form method="post" action="/assignment/{r["id"]}" class="assignment-form"><input type="hidden" name="token" value="{assignment_token}"><select name="assignee_user_id" aria-label="Assign MLS {esc(r["mls_number"])}">{options}</select><button type="submit">Save</button></form>'
            if not allowed("audits.assign"):
                assignment = ""
            reviewer_label = r["reviewer_name"] or ""
            selection = f'<td class="audit-select"><input type="checkbox" name="audit_ids" value="{r["id"]}" form="bulk-assignment" aria-label="Select MLS {esc(r["mls_number"])}"></td>' if selectable else ""
            rows += f'<tr>{selection}<td class="history-property" data-sort="{esc(r["mls_number"])}"><strong>{esc(r["mls_number"])}</strong><small>{esc(r["address"])}</small></td><td data-sort="{esc(r["selected_at"])}">{esc(local_time(r["selected_at"], config.timezone))}</td><td data-sort="{esc(r["brokerage_name"])}">{esc(r["brokerage_name"])}<small>{esc(r["broker_name"])}</small></td><td data-sort="{esc(r["agent_name"])}">{esc(r["agent_name"])}<small>{esc(r["agent_email"])}</small></td><td class="history-intended"><span class="muted">To:</span> {esc(recipients(r["intended_to"]))}<small>CC: {esc(recipients(r["intended_cc"]))}</small></td><td class="history-recipient">{esc(recipients(r["actual_recipients"]))}</td><td data-sort="{esc(r["email_status"])}">{mode} {badge(r["email_status"])}<small class="error">{esc(r["last_error"]) if r["last_error"] else ""}</small></td><td>{badge(work_status.lower().replace(" ", "_"))}</td><td data-sort="{esc(reviewer_label or "Unassigned")}">{assignment}<small>{esc(reviewer_label) if reviewer_label else ""}</small></td><td data-sort="{r["prior_count"]}">{r["prior_count"]} prior</td><td>{outcome}</td><td>{retry}</td></tr>'
        title, subtitle = "Audit history", "Selection, recipient routing, and email delivery in one place."
    if tab not in {"template", "failure_template", "outcome", "admin", "manage", "report", "brokerages", "users", "activity", "recovery"}:
        if not rows:
            rows = f'<tr><td colspan="{(13 if allowed("audits.assign") else 12) if tab == "audits" else 6 if tab == "runs" else 5}" class="empty">No records match these filters.</td></tr>'
        bulk = ""
        if tab == "audits" and allowed("audits.assign") and audits:
            bulk_options = '<option value="" disabled selected>Choose an assignment</option><option value="unassigned">Unassigned — clear assignment</option>' + "".join(
                f'<option value="{person["id"]}">{esc(person["display_name"] or person["email"])}{(" · " + esc(person["email"])) if person["display_name"] else ""}</option>'
                for person in reviewers)
            bulk = f'<form id="bulk-assignment" data-owner="{esc(g.principal.subject) if has_request_context() else "local"}" method="post" action="/assignments" class="bulk-assignment"><input type="hidden" name="token" value="{retry_token(config, "assignments")}"><span data-selection-count aria-live="polite">0 selected</span><label>Assign selected to <select name="assignee_user_id" required>{bulk_options}</select></label><button type="submit" class="primary-button" disabled>Apply to selected</button><button type="button" data-clear-selection disabled>Clear selection</button></form>'
        content = f'<section class="stats">{cards}</section><section class="panel"><div class="panel-head"><div><h2>{esc(title)}</h2><p>{list_page.total:,} matching records · Sort by a column heading</p></div><span class="live-dot">● &nbsp; Current data</span></div>{list_page.filters()}{bulk}<div class="table-wrap"><table data-server-list data-columns="{tab}" class="{"history-table" if tab == "audits" else "listing-table" if tab == "listings" else "runs-table"}"{" data-sortable" if tab in {"audits", "listings"} else ""}><thead><tr>{headings}</tr></thead><tbody>{rows}</tbody></table></div>{list_page.footer()}</section>'
    if tab in {'admin', 'manage', 'template', 'failure_template', 'users', 'recovery'}:
        sections = (('manage', 'Selection', 'settings.manage'), ('template', 'Audit request email', 'templates.manage'),
                    ('failure_template', 'Failed-audit email', 'templates.manage'), ('users', 'Access', 'users.manage'), ('recovery', 'Recovery', 'backups.manage'))
        links = ''
        for name, label, capability in sections:
            if allowed(capability):
                current = 'aria-current="page"' if tab == name or tab == 'admin' and name == 'manage' else ''
                links += f'<a href="/?tab={name}" {current}>{label}</a>'
        content = f'<nav class="manage-sections" aria-label="Manage sections">{links}</nav>' + content
    content = re.sub(r'<form\b[^>]*>.*?</form>',
                     lambda match: match[0].replace('</form>', f'<input type="hidden" name="revision" value="{revision}"></form>')
                     if 'method="post"' in match[0].split('>', 1)[0] else match[0], content, flags=re.DOTALL)
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MLS Audit Desk</title><link rel="stylesheet" href="/static/app.css"></head>
<body><a class="skip-link" href="#main">Skip to content</a><button class="menu-backdrop" type="button" aria-label="Close menu" hidden></button><aside class="sidebar" id="sidebar"><button class="menu-close" type="button" aria-label="Close menu"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M6 6l12 12M18 6L6 18"/></svg></button><div class="brand"><img class="brand-logo" src="/static/cornerstone-logo-white.png" alt="Cornerstone Association of REALTORS"><strong class="brand-caption">Compliance Audit Desk</strong></div><div class="sidebar-label">WORKSPACE</div><nav>{nav}</nav><div class="sidebar-label admin-label">MANAGEMENT</div><nav>{admin_nav}</nav></aside>
<div class="workspace"><header class="topbar"><button class="menu-toggle" type="button" aria-label="Open menu" aria-controls="sidebar" aria-expanded="false"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 6h16M4 12h16M4 18h16"/></svg></button><span class="topbar-title">Audit operations</span><div class="identity">{esc(g.principal.name) if has_request_context() else "Audit Desk"}<small>{esc({"admin": "IT administrator", "manager": "Audit manager", "reviewer": "Reviewer"}.get(g.principal.role, g.principal.role)) if has_request_context() else ""}</small></div></header><main id="main"><header class="page-head"><div><div class="eyebrow">OPERATIONS / {esc(title.upper())}</div><h1>{esc(title)}</h1><p>{esc(subtitle)}</p></div></header>{banner}{'<div class="notice">'+esc(notice)+'</div>' if notice else ''}{content}<footer>Audit Desk · Internal use only</footer></main></div><script src="/static/shell.js" defer></script><script src="/static/bulk-assignment.js" defer></script><script src="/static/columns.js" defer></script><script src="/static/sort.js" defer></script><script src="/static/branches.js" defer></script></body></html>'''
