"""Production HTTP entry point: identity, authorization, bounded requests and views."""
import os
from dataclasses import replace
from pathlib import Path
import csv
import io
import logging
import secrets
import threading
import time
import tempfile
from collections import defaultdict, deque
from datetime import timedelta
from urllib.parse import urlparse

from flask import Flask, Response, abort, g, redirect, request, session
from werkzeug.exceptions import HTTPException

from . import web
from .assignment import assign_audits, assign_reviewer
from .brokerage_report import PERIODS, brokerage_statistics
from .config import development_config, load_config, validate_web_config
from .database import SCHEMA_VERSION, connect, init_db
from .job import deliver_audit
from .outcomes import deliver_failure_notice, record_outcome
from .security import AccessVerifier, authenticate, check_csrf, require, save_user, seed_admins
from .settings import save_selection_percent
from .templates import save_templates, save_failure_templates, validate_templates


def create_app(config=None, verifier=None):
    config = development_config(config or load_config())
    validate_web_config(config)
    sandbox = None
    if config.env == "development":
        sandbox = tempfile.TemporaryDirectory(prefix="auditdesk-preview-")
        config = replace(config, database_path=str(Path(sandbox.name) / "sandbox.sqlite3"))
    init_db(config.database_path)
    seed_admins(config)
    if sandbox:
        from .development import seed_development
        seed_development(config)
    if not config.secret_key:
        secret_path = Path(config.database_path).resolve().parent / "session.key"
        try:
            descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "w") as secret_file:
                secret_file.write(secrets.token_urlsafe(48))
        secret = secret_path.read_text().strip()
        if len(secret) < 32:
            raise ValueError("Persisted session key is invalid")
        config = replace(config, secret_key=secret)
    app = Flask(__name__, static_folder="static")
    app.config.update(SECRET_KEY=config.secret_key,
                      MAX_CONTENT_LENGTH=32768, MAX_FORM_MEMORY_SIZE=32768, MAX_FORM_PARTS=20,
                      SESSION_COOKIE_NAME="auditdesk_session", SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SECURE=urlparse(config.public_url).scheme == "https", SESSION_COOKIE_SAMESITE="Strict",
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
                      TRUSTED_HOSTS=[urlparse(config.public_url).hostname, "127.0.0.1", "localhost"])
    app.extensions["auditdesk_config"] = config
    app.extensions["auditdesk_sandbox"] = sandbox  # Keep disposable storage alive for this app.
    verifier = None if sandbox else (verifier or AccessVerifier(config))
    limits = defaultdict(deque)
    limit_lock = threading.Lock()

    @app.before_request
    def guard():
        g.request_id = secrets.token_hex(8)
        if request.routing_exception:
            raise request.routing_exception
        if request.path == "/healthz" and request.method in {"GET", "HEAD"}:
            return
        authenticate(config, verifier)
        session.permanent = True
        # Rate limit by verified subject, never untrusted forwarding headers.
        if request.method == "POST" or request.path.endswith((".pdf", ".csv")):
            with limit_lock:
                now = time.monotonic()
                for key in list(limits):
                    if not limits[key] or now - limits[key][-1] >= 60:
                        del limits[key]
                bucket = limits[g.principal.subject]
                while bucket and now - bucket[0] >= 60:
                    bucket.popleft()
                if len(bucket) >= 30:
                    abort(429, "Too many actions. Wait a minute and try again.")
                bucket.append(now)
        if request.method == "POST":
            if request.mimetype != "application/x-www-form-urlencoded":
                abort(415, "Use an application form to submit changes.")
            if any(len(values) != 1 for key, values in request.form.lists()
                   if not (request.path == "/assignments" and key == "audit_ids")):
                abort(400, "Repeated form fields are not allowed.")
            check_csrf(config)
            revision = request.form.get("revision", "")
            if not revision.isdigit():
                abort(400, "Reload the form before saving.")
            g.expected_revision = int(revision)

    @app.after_request
    def headers(response):
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                 "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
                                 "X-Request-ID": getattr(g, "request_id", "")})
        # Legacy inline event handlers remain; no external script sources are allowed.
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if urlparse(config.public_url).scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.errorhandler(HTTPException)
    def http_error(error):
        return f'<h1>{error.code} — {web.esc(error.name)}</h1><p>{web.esc(error.description)}</p><p><a href="/">Return to AuditDesk</a></p>', error.code

    @app.errorhandler(ValueError)
    def invalid(error):
        return f'<h1>Change not saved</h1><p>{web.esc(str(error))}</p><p>Use Back to keep your form, or <a href="/">return to AuditDesk</a>.</p>', 400

    @app.errorhandler(Exception)
    def unexpected(error):
        logging.getLogger("audit_app").error("request_failed request_id=%s type=%s", getattr(g, "request_id", ""), type(error).__name__)
        return '<h1>Something went wrong</h1><p>Your request could not be completed. Contact IT with request ID ' + web.esc(getattr(g, "request_id", "")) + '.</p>', 500

    @app.get("/healthz")
    def health():
        with connect(config.database_path) as db:
            db.execute("SELECT id FROM app_users LIMIT 1").fetchone()
        return {"status": "ok", "schema": SCHEMA_VERSION}

    @app.get("/")
    def index():
        tab = request.args.get("tab", "audits")
        if tab not in web.TAB_CAPABILITIES:
            abort(404)
        @require(web.TAB_CAPABILITIES[tab])
        def page():
            audit_id = request.args.get("id", "")
            if tab == "outcome":
                if not audit_id.isdigit():
                    abort(400, "Choose an audit.")
                with connect(config.database_path) as db:
                    if not db.execute("SELECT 1 FROM audits WHERE id=?", (int(audit_id),)).fetchone():
                        abort(404)
            period = request.args.get("period", "3m")
            if period not in PERIODS:
                abort(400, "Invalid report period.")
            return web.render(config, tab, notice=request.args.get("notice", ""), audit_id=int(audit_id) if tab == "outcome" else None, period=period)
        return page()

    def done(tab, notice):
        from urllib.parse import urlencode
        return redirect("/?" + urlencode({"tab": tab, "notice": notice}), code=303)

    @app.post("/admin/selection-rate")
    @require("settings.manage")
    def selection():
        save_selection_percent(config, request.form.get("rate_percent", ""))
        return done("admin", "Selection percentage saved for future runs.")

    @app.post("/users")
    @require("users.manage")
    def users():
        save_user(config, request.form)
        return done("users", "Application access saved.")

    @app.post("/assignment/<int:item_id>")
    @require("audits.assign")
    def assignment(item_id):
        if "assignee_user_id" not in request.form:
            abort(400, "Reload the assignment form before saving.")
        value = request.form["assignee_user_id"]
        if value and (not value.isascii() or not value.isdigit()):
            abort(400, "Choose an active reviewer.")
        assign_reviewer(config, item_id, int(value) if value else None)
        return done("audits", "Assignment saved.")

    @app.post("/assignments")
    @require("audits.assign")
    def bulk_assignment():
        ids = request.form.getlist("audit_ids")
        if not ids or len(ids) > 200 or any(not value.isascii() or not value.isdigit() for value in ids):
            abort(400, "Select between 1 and 200 audits.")
        if "assignee_user_id" not in request.form:
            abort(400, "Choose an assignment.")
        value = request.form["assignee_user_id"]
        if value != "unassigned" and (not value.isascii() or not value.isdigit()):
            abort(400, "Choose an active reviewer.")
        assign_audits(config, [int(item) for item in ids], None if value == "unassigned" else int(value))
        return done("audits", f"Assignment updated for {len(ids)} audits.")

    @app.post("/template")
    @app.post("/failure-template")
    @require("templates.manage")
    def template():
        failure = request.path == "/failure-template"
        tab = "failure_template" if failure else "template"
        subject, body, fmt = (request.form.get(k, "") for k in ("subject", "body", "body_format"))
        action = request.form.get("action")
        if action not in {"preview", "save"}:
            abort(400, "Invalid template action.")
        try:
            validate_templates(subject, body, fmt, "failure" if failure else "request")
            if action == "save":
                (save_failure_templates if failure else save_templates)(config, subject, body, fmt)
                return done(tab, "Template saved for future emails.")
        except ValueError as error:
            return web.render(config, tab, form_values=(subject, body, fmt), error=str(error)), 400
        return web.render(config, tab, form_values=(subject, body, fmt))

    @app.post("/outcome/<int:audit_id>")
    @require("audits.result")
    def outcome(audit_id):
        result, action, issues = (request.form.get(k, "") for k in ("outcome", "action", "issues"))
        with connect(config.database_path) as db:
            if not db.execute("SELECT 1 FROM audits WHERE id=?", (audit_id,)).fetchone():
                abort(404)
        try:
            if result == "failed" and action == "preview":
                if not issues.strip() or len(issues) > 5000:
                    raise ValueError("Describe the issues using 1 to 5,000 characters.")
                return web.render(config, "outcome", form_values=issues, audit_id=audit_id, preview_outcome=True)
            if (result, action) not in {("passed", "record"), ("failed", "send")}:
                abort(400, "Invalid result action.")
            record_outcome(config, audit_id, result, issues)
        except ValueError as error:
            return web.render(config, "outcome", form_values=issues, error=str(error), audit_id=audit_id), 400
        return done("audits", "Audit result recorded. Check notice status below.")

    @app.post("/retry/<int:audit_id>")
    @app.post("/failure-retry/<int:audit_id>")
    @require("email.retry")
    def retry(audit_id):
        if request.path.startswith("/failure-retry/"):
            with connect(config.database_path) as db:
                row = db.execute("SELECT failure_email_status FROM audits WHERE id=?", (audit_id,)).fetchone()
            sent = deliver_failure_notice(config, audit_id, retry=bool(row and row["failure_email_status"] == "email_failed"))
        else:
            sent = deliver_audit(config, audit_id, retry=True)
        return done("audits", "Email accepted by SendGrid." if sent else "Email was not sent. Check status and configuration.")

    @app.get("/reports/brokerages.pdf")
    @require("reports.read")
    def pdf():
        from .brokerage_pdf import build_brokerage_pdf
        period = request.args.get("period", "3m")
        if period not in PERIODS:
            abort(400, "Invalid report period.")
        return Response(build_brokerage_pdf(brokerage_statistics(config, period)), mimetype="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="auditdesk-brokerages-{period}.pdf"'})

    @app.get("/activity.csv")
    @require("activity.read")
    def activity_csv():
        with connect(config.database_path) as db:
            rows = db.execute("SELECT occurred_at,actor,action,target,detail FROM activity_events ORDER BY id DESC LIMIT 10000").fetchall()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Time (UTC)", "Actor", "Action", "Target", "Detail"])
        for row in rows:
            writer.writerow(["'" + str(v) if str(v).startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in row])
        return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": 'attachment; filename="auditdesk-activity.csv"'})

    return app
