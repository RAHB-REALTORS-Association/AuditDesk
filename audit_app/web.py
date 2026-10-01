"""Page composition and navigation for the staff interface.

Feature-specific HTML belongs in views/. Request handling belongs in application.py.
"""
from flask import g, has_request_context

from .database import connect
from .security import allowed
from .views.common import badge, esc, local_time, recipients, retry_token, sort_heading
from .views.emails import template_editor
from .views.management import activity_view, admin_view, reviewers_view, users_view
from .views.reports import brokerage_view, report_view
from .views.workflow import outcome_view, simulation_view


TAB_CAPABILITIES = {
    "audits": "audits.read", "listings": "audits.read", "runs": "audits.read",
    "simulation": "audits.read", "outcome": "audits.result", "reviewers": "reviewers.manage",
    "admin": "settings.manage", "template": "templates.manage", "failure_template": "templates.manage",
    "report": "reports.read", "brokerages": "reports.read", "users": "users.manage", "activity": "activity.read",
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
    if config.env == "development":
        banner = '<div class="test-banner"><strong>DEVELOPMENT SANDBOX</strong><span>Synthetic listings only. Email records are simulated; no messages can be sent. Changes reset on restart.</span></div>'
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
<body><a class="skip-link" href="#main">Skip to content</a><button class="menu-backdrop" type="button" aria-label="Close menu" hidden></button><aside class="sidebar" id="sidebar"><button class="menu-close" type="button">Close menu</button><div class="brand"><img class="brand-logo" src="/static/cornerstone-logo-white.png" alt="Cornerstone Association of REALTORS"><strong class="brand-caption">Compliance Audit Desk</strong></div><div class="sidebar-label">WORKSPACE</div><nav>{nav}</nav><div class="sidebar-label admin-label">ADMIN</div><nav>{admin_nav}</nav><div class="sidebar-foot">Daily selection · {esc(config.timezone)}</div></aside>
<div class="workspace"><header class="topbar"><button class="menu-toggle" type="button" aria-controls="sidebar" aria-expanded="false">Menu</button><span class="topbar-title">Audit operations</span><div class="identity">{esc(g.principal.name) if has_request_context() else "Audit Desk"}<small>{esc({"admin": "IT administrator", "manager": "Audit manager", "reviewer": "Reviewer"}.get(g.principal.role, g.principal.role)) if has_request_context() else ""}</small></div></header><main id="main"><header class="page-head"><div><div class="eyebrow">OPERATIONS / {esc(title.upper())}</div><h1>{esc(title)}</h1><p>{esc(subtitle)}</p></div></header>{banner}{'<div class="notice">'+esc(notice)+'</div>' if notice else ''}{content}<footer>Audit Desk · Internal use only</footer></main></div><script src="/static/shell.js" defer></script><script src="/static/sort.js" defer></script><script src="/static/branches.js" defer></script></body></html>'''
