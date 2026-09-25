import sqlite3
import re
import html
from datetime import datetime, timezone
from html.parser import HTMLParser

from .database import connect


FIELDS = ("mls_number", "address", "agent_name", "brokerage_name", "broker_name", "broker_first_name")
FAILURE_FIELDS = FIELDS + ("issues",)
REQUIRED_BODY_FIELDS = ("mls_number", "address", "agent_name", "brokerage_name")
REQUIRED_FAILURE_BODY_FIELDS = ("mls_number", "address", "issues")
TAG = re.compile(r"\{\{([a-z_]+)\}\}")


class SafeEmailHTML(HTMLParser):
    """Keep only text, line breaks, and the three supported email styles."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.open_tags = []
        self.suppressed = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.suppressed += 1
        elif not self.suppressed:
            tag = {"b": "strong", "i": "em"}.get(tag, tag)
            if tag in {"strong", "em", "u"}:
                self.parts.append(f"<{tag}>")
                self.open_tags.append(tag)
            elif tag == "br":
                self.parts.append("<br>")
            elif tag in {"div", "p"} and self.parts and self.parts[-1] != "<br>":
                self.parts.append("<br>")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.suppressed = max(0, self.suppressed - 1)
        elif not self.suppressed:
            tag = {"b": "strong", "i": "em"}.get(tag, tag)
            if tag in self.open_tags:
                while self.open_tags:
                    opened = self.open_tags.pop()
                    self.parts.append(f"</{opened}>")
                    if opened == tag:
                        break

    def handle_data(self, data):
        if not self.suppressed:
            normalized = data.replace("\r\n", "\n").replace("\r", "\n")
            self.parts.append(html.escape(normalized).replace("\n", "<br>"))

    def finish(self):
        self.parts.extend(f"</{tag}>" for tag in reversed(self.open_tags))
        return "".join(self.parts).strip("\n")


def clean_html(value):
    parser = SafeEmailHTML()
    parser.feed(value)
    parser.close()
    return parser.finish()


def html_to_text(value):
    return html.unescape(re.sub(r"</?(?:strong|em|u)>", "", value).replace("<br>", "\n")).strip()


def plain_to_html(value):
    return html.escape(value.replace("\r\n", "\n").replace("\r", "\n")).replace("\n", "<br>")


def defaults(config):
    def convert(value):
        return re.sub(r"(?<!\{)\{(" + "|".join(FIELDS) + r")\}(?!\})", r"{{\1}}", value)
    return convert(config.subject_template), convert(config.body_template.replace("\\n", "\n"))


def current_templates(config):
    try:
        with connect(config.database_path) as db:
            row = db.execute("SELECT subject, body, updated_at, body_format FROM email_template WHERE id=1").fetchone()
    except sqlite3.OperationalError:
        row = None
    if row:
        return row["subject"], row["body"], row["updated_at"], row["body_format"]
    subject, body = defaults(config)
    return subject, body, None, "plain"


def current_failure_templates(config):
    try:
        with connect(config.database_path) as db:
            row = db.execute("SELECT subject, body, updated_at, body_format FROM failure_template WHERE id=1").fetchone()
    except sqlite3.OperationalError:
        row = None
    if row:
        return row["subject"], row["body"], row["updated_at"], row["body_format"]
    return ("Listing Audit Follow-Up - MLS {{mls_number}}",
            "Hello {{broker_first_name}},\n\nOur audit of MLS {{mls_number}} at {{address}} found the following issues:\n\n{{issues}}\n\nPlease reply with corrected documentation or contact us if you have questions.",
            None, "plain")


def validate_templates(subject, body, body_format="plain", kind="request"):
    subject = subject.strip()
    body = body.strip()
    if body_format not in {"plain", "html"}:
        raise ValueError("Unsupported email body format.")
    if body_format == "html":
        body = clean_html(body)
        body_text = html_to_text(body)
    else:
        body_text = body
    if not subject or not body_text:
        raise ValueError("Subject and body are both required.")
    if len(subject) > 200 or len(body_text) > 10000 or len(body) > 20000:
        raise ValueError("Subject must be at most 200 characters and body at most 10,000 characters.")
    if "\n" in subject or "\r" in subject:
        raise ValueError("The subject must be one line.")
    if kind not in {"request", "failure"}:
        raise ValueError("Unsupported email template type.")
    fields = FAILURE_FIELDS if kind == "failure" else FIELDS
    required = REQUIRED_FAILURE_BODY_FIELDS if kind == "failure" else REQUIRED_BODY_FIELDS
    used = set()
    for label, value in (("subject", subject), ("body", body_text)):
        for match in TAG.finditer(value):
            field = match.group(1)
            if field not in fields:
                raise ValueError(f"Unsupported merge tag {{{{{field}}}}} in the {label}.")
            if label == "body":
                used.add(field)
        if "{{" in TAG.sub("", value) or "}}" in TAG.sub("", value):
            raise ValueError(f"The {label} contains an incomplete merge tag.")
    missing = set(required) - used
    if missing:
        raise ValueError("The body must include " + ", ".join("{{" + field + "}}" for field in sorted(missing)) + ".")
    return subject, body


def save_templates(config, subject, body, body_format="plain"):
    subject, body = validate_templates(subject, body, body_format)
    updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect(config.database_path) as db:
        db.execute("""INSERT INTO email_template(id,subject,body,body_format,updated_at) VALUES(1,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET subject=excluded.subject, body=excluded.body,
            body_format=excluded.body_format, updated_at=excluded.updated_at""",
                   (subject, body, body_format, updated))
        db.commit()
    return updated


def save_failure_templates(config, subject, body, body_format="plain"):
    subject, body = validate_templates(subject, body, body_format, "failure")
    updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect(config.database_path) as db:
        db.execute("""INSERT INTO failure_template(id,subject,body,body_format,updated_at) VALUES(1,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET subject=excluded.subject, body=excluded.body,
            body_format=excluded.body_format, updated_at=excluded.updated_at""",
                   (subject, body, body_format, updated))
        db.commit()
    return updated


def format_message_parts(subject_template, body_template, listing, test_mode, intended_to, intended_cc,
                         body_format="plain", issues=None):
    first_name = (listing.get("broker_first_name") or "").strip()
    if not first_name:
        full_name = (listing.get("broker_name") or "").strip()
        name_start = full_name.split(",", 1)[1] if "," in full_name else full_name
        first_name = name_start.split()[0] if name_start.split() else "Broker"
    context = {
        "mls_number": listing["mls_number"],
        "address": listing["address"],
        "agent_name": listing.get("agent_name") or "Unknown",
        "brokerage_name": listing.get("brokerage_name") or "Unknown",
        "broker_name": listing.get("broker_name") or "Unknown",
        "broker_first_name": first_name,
    }
    if issues is not None:
        context["issues"] = issues
    def merge(value, escape_values=False):
        def substitute(match):
            field = match.group(1)
            value = str(context[field])
            if escape_values and field == "issues":
                return plain_to_html(value)
            return html.escape(value) if escape_values else value
        return TAG.sub(substitute, value)
    subject_template, body_template = validate_templates(subject_template, body_template, body_format,
                                                        "failure" if issues is not None else "request")
    subject = merge(subject_template)
    plain_body = merge(html_to_text(body_template) if body_format == "html" else body_template)
    rich_body = merge(body_template if body_format == "html" else plain_to_html(body_template), True)
    if test_mode:
        subject = "[TEST - WOULD SEND TO " + ", ".join(intended_to) + "] " + subject
        prefix = "TEST MODE\n\nIntended To:\n" + "\n".join(intended_to) + "\n\nIntended CC:\n" + "\n".join(intended_cc) + "\n\n---\n\n"
        plain_body = prefix + plain_body
        rich_body = plain_to_html(prefix) + rich_body
    return subject, plain_body, rich_body


def format_message(subject_template, body_template, listing, test_mode, intended_to, intended_cc, body_format="plain"):
    subject, plain_body, _ = format_message_parts(subject_template, body_template, listing, test_mode, intended_to, intended_cc, body_format)
    return subject, plain_body
