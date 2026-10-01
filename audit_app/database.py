import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 5

SCHEMA = """
PRAGMA journal_mode=DELETE;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS listings (
  id INTEGER PRIMARY KEY,
  bridge_listing_id TEXT NOT NULL UNIQUE,
  mls_number TEXT NOT NULL,
  status TEXT NOT NULL,
  entry_timestamp TEXT NOT NULL,
  address TEXT NOT NULL,
  agent_id TEXT,
  agent_name TEXT,
  agent_email TEXT,
  brokerage_id TEXT,
  brokerage_name TEXT,
  brokerage_email TEXT,
  brokerage_address TEXT,
  broker_id TEXT,
  broker_name TEXT,
  broker_first_name TEXT,
  broker_email TEXT,
  first_processed_at TEXT NOT NULL,
  processing_status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audits (
  id INTEGER PRIMARY KEY,
  listing_id INTEGER NOT NULL UNIQUE REFERENCES listings(id),
  selected_at TEXT NOT NULL,
  brokerage_id TEXT,
  brokerage_name TEXT,
  broker_id TEXT,
  broker_name TEXT,
  agent_name TEXT,
  intended_to TEXT NOT NULL,
  intended_cc TEXT NOT NULL,
  actual_recipients TEXT NOT NULL,
  test_mode INTEGER NOT NULL,
  email_sent_at TEXT,
  sendgrid_message_id TEXT,
  email_status TEXT NOT NULL,
  selection_metadata TEXT NOT NULL,
  last_error TEXT,
  outcome TEXT,
  outcome_at TEXT,
  issues TEXT,
  failure_email_status TEXT,
  failure_email_sent_at TEXT,
  failure_message_id TEXT,
  failure_last_error TEXT,
  failure_actual_recipients TEXT,
  asana_task_url TEXT,
  assignee_user_id INTEGER REFERENCES app_users(id)
);
CREATE TABLE IF NOT EXISTS selection_settings (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  rate_percent TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS brokerage_cooldown_settings (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  days INTEGER NOT NULL CHECK(days BETWEEN 0 AND 365),
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_settings (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  broker_cooldown_days INTEGER NOT NULL CHECK(broker_cooldown_days BETWEEN 0 AND 365),
  window_hours INTEGER NOT NULL CHECK(window_hours BETWEEN 1 AND 168),
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS email_attempts (
  id INTEGER PRIMARY KEY,
  audit_id INTEGER NOT NULL REFERENCES audits(id),
  attempted_at TEXT NOT NULL,
  completed_at TEXT,
  status TEXT NOT NULL,
  actual_recipients TEXT NOT NULL,
  sendgrid_message_id TEXT,
  error TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  fetched_count INTEGER NOT NULL DEFAULT 0,
  new_count INTEGER NOT NULL DEFAULT 0,
  selected_count INTEGER NOT NULL DEFAULT 0,
  error TEXT
);
CREATE TABLE IF NOT EXISTS email_template (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  body_format TEXT NOT NULL DEFAULT 'plain',
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS failure_template (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  body_format TEXT NOT NULL DEFAULT 'plain',
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS failure_email_attempts (
  id INTEGER PRIMARY KEY,
  audit_id INTEGER NOT NULL REFERENCES audits(id),
  attempted_at TEXT NOT NULL,
  completed_at TEXT,
  status TEXT NOT NULL,
  actual_recipients TEXT NOT NULL,
  sendgrid_message_id TEXT,
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_audits_brokerage ON audits(brokerage_id, selected_at);
CREATE INDEX IF NOT EXISTS idx_audits_broker ON audits(broker_id, selected_at);
CREATE INDEX IF NOT EXISTS idx_audits_selected_at ON audits(selected_at);
"""


@contextmanager
def connect(path, *, check_revision=True):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        from flask import g, has_request_context, request
        if check_revision and has_request_context() and request.method == "POST" and hasattr(g, "expected_revision") and not getattr(g, "mutation_started", False):
            from werkzeug.exceptions import Conflict
            db.execute("BEGIN IMMEDIATE")
            revision = db.execute("SELECT COALESCE(max(id),0) FROM activity_events").fetchone()[0]
            if revision != g.expected_revision:
                raise Conflict("Records changed while this form was open. Refresh before saving; your change was not applied.")
        yield db
    finally:
        db.close()


def init_db(path, *, check_revision=True):
    Path(path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with connect(path, check_revision=check_revision) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise ValueError("Database schema is newer than this application")
        db.execute("BEGIN IMMEDIATE")
        for statement in SCHEMA.split(";"):
            if statement.strip() and not statement.strip().startswith("PRAGMA"):
                db.execute(statement)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(listings)")}
        if "broker_first_name" not in columns:
            db.execute("ALTER TABLE listings ADD COLUMN broker_first_name TEXT")
        if "brokerage_address" not in columns:
            db.execute("ALTER TABLE listings ADD COLUMN brokerage_address TEXT")
        template_columns = {row["name"] for row in db.execute("PRAGMA table_info(email_template)")}
        if "body_format" not in template_columns:
            db.execute("ALTER TABLE email_template ADD COLUMN body_format TEXT NOT NULL DEFAULT 'plain'")
        audit_columns = {row["name"] for row in db.execute("PRAGMA table_info(audits)")}
        for name in ("outcome", "outcome_at", "issues", "failure_email_status", "failure_email_sent_at",
                     "failure_message_id", "failure_last_error", "failure_actual_recipients", "asana_task_url"):
            if name not in audit_columns:
                db.execute(f"ALTER TABLE audits ADD COLUMN {name} TEXT")
        # The separate roster was development-only; discard its assignments.
        for legacy_column in ("reviewer_id", "reviewer_name_snapshot"):
            if legacy_column in audit_columns:
                db.execute(f"ALTER TABLE audits DROP COLUMN {legacy_column}")
        db.execute("DROP TABLE IF EXISTS audit_reviewers")
        db.execute("""CREATE TABLE IF NOT EXISTS app_users (
            id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            subject TEXT UNIQUE, display_name TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL CHECK(role IN ('reviewer','manager','admin')),
            active INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        db.execute("""CREATE TABLE IF NOT EXISTS activity_events (
            id INTEGER PRIMARY KEY, occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '')""")
        if "assignee_user_id" not in audit_columns:
            db.execute("ALTER TABLE audits ADD COLUMN assignee_user_id INTEGER REFERENCES app_users(id)")
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        db.commit()
