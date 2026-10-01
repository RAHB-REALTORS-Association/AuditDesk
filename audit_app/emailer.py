import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .database import connect
from .templates import current_failure_templates, current_templates, format_message_parts


class EmailError(Exception):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def valid_email(value):
    return bool(value and re.fullmatch(r"[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+", value.strip()))


def normalized_email(value):
    return value.strip() if valid_email(value) else None


def resolve_recipients(listing, config):
    broker = normalized_email(listing.get("broker_email"))
    office = normalized_email(listing.get("brokerage_email"))
    agent = normalized_email(listing.get("agent_email"))
    to = [broker] if broker else []
    cc = []
    for address in (office, agent):
        if address and address.casefold() not in {item.casefold() for item in to + cc}:
            cc.append(address)
    if not to:
        raise EmailError("Broker email is missing or invalid; no email was sent")
    if not agent:
        raise EmailError("Listing agent email is missing or invalid; no email was sent")
    if config.test_mode:
        admin = normalized_email(config.admin_email)
        if not admin:
            raise EmailError("ADMIN_EMAIL is missing or invalid")
        return to, cc, [admin]
    return to, cc, to + cc


def message_content(listing, config, intended_to, intended_cc):
    subject, body, _ = message_parts(listing, config, intended_to, intended_cc)
    return subject, body


def message_parts(listing, config, intended_to, intended_cc):
    subject_template, body_template, _, body_format = current_templates(config)
    try:
        return format_message_parts(subject_template, body_template, listing, config.test_mode, intended_to, intended_cc, body_format)
    except ValueError as exc:
        raise EmailError(str(exc)) from None


def send_email(config, listing, audit_id, intended_to, intended_cc, actual):
    if config.env == "development":
        raise EmailError("Email delivery is unavailable in development")
    with connect(config.database_path) as db:
        audit = db.execute("SELECT test_mode FROM audits WHERE id=?", (audit_id,)).fetchone()
    if audit and audit["test_mode"] and not config.test_mode:
        raise EmailError("A test audit cannot send a production request")
    # Rebuild recipients from the listing at the final SendGrid boundary.
    intended_to, intended_cc, actual = resolve_recipients(listing, config)
    subject, body, rich_body = message_parts(listing, config, intended_to, intended_cc)
    return _post_message(config, audit_id, intended_to, intended_cc, actual, subject, body, rich_body, "audit_request")


def send_failure_email(config, listing, audit_id, issues):
    if config.env == "development":
        raise EmailError("Email delivery is unavailable in development")
    with connect(config.database_path) as db:
        audit = db.execute("SELECT test_mode FROM audits WHERE id=?", (audit_id,)).fetchone()
    if audit and audit["test_mode"] and not config.test_mode:
        raise EmailError("A test audit cannot send a production failed-audit notice")
    intended_to, intended_cc, actual = resolve_recipients(listing, config)
    subject_template, body_template, _, body_format = current_failure_templates(config)
    try:
        subject, body, rich_body = format_message_parts(subject_template, body_template, listing, config.test_mode,
                                                       intended_to, intended_cc, body_format, issues=issues)
    except ValueError as exc:
        raise EmailError(str(exc)) from None
    return _post_message(config, audit_id, intended_to, intended_cc, actual, subject, body, rich_body, "audit_failure")


def _post_message(config, audit_id, intended_to, intended_cc, actual, subject, body, rich_body, kind):
    if config.env == "development":
        raise EmailError("Email delivery is unavailable in development")
    if not config.email_enabled:
        raise EmailError("Email delivery is disabled")
    if not config.test_window_open():
        raise EmailError("Test window has ended; no email was sent")
    if not config.sendgrid_key:
        raise EmailError("SENDGRID_API_KEY is missing")
    if not valid_email(config.from_address):
        raise EmailError("EMAIL_FROM_ADDRESS is missing or invalid")
    personalization = {"to": [{"email": address} for address in actual]}
    if not config.test_mode and intended_cc:
        personalization["to"] = [{"email": address} for address in intended_to]
        personalization["cc"] = [{"email": address} for address in intended_cc]
    payload = {
        "personalizations": [personalization],
        "from": {"email": config.from_address, "name": config.from_name},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}, {"type": "text/html", "value": rich_body}],
        "custom_args": {"audit_id": str(audit_id), "notice_type": kind},
    }
    request = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + config.sendgrid_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 202:
                raise EmailError(f"SendGrid returned HTTP {response.status}")
            return response.headers.get("X-Message-Id")
    except urllib.error.HTTPError as exc:
        raise EmailError(f"SendGrid returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError):
        # The request may have reached SendGrid. Do not retry an uncertain send.
        raise EmailError("SendGrid response was not received; delivery is uncertain", uncertain=True) from None


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
