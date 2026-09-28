import sqlite3
from contextlib import contextmanager
from pathlib import Path


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
  failure_actual_recipients TEXT
);
CREATE TABLE IF NOT EXISTS audit_reviewers (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS selection_settings (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  rate_percent TEXT NOT NULL,
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
"""


@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
    finally:
        db.close()


def init_db(path):
    Path(path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with connect(path) as db:
        db.executescript(SCHEMA)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(listings)")}
        if "broker_first_name" not in columns:
            db.execute("ALTER TABLE listings ADD COLUMN broker_first_name TEXT")
        template_columns = {row["name"] for row in db.execute("PRAGMA table_info(email_template)")}
        if "body_format" not in template_columns:
            db.execute("ALTER TABLE email_template ADD COLUMN body_format TEXT NOT NULL DEFAULT 'plain'")
        audit_columns = {row["name"] for row in db.execute("PRAGMA table_info(audits)")}
        for name in ("outcome", "outcome_at", "issues", "failure_email_status", "failure_email_sent_at",
                     "failure_message_id", "failure_last_error", "failure_actual_recipients"):
            if name not in audit_columns:
                db.execute(f"ALTER TABLE audits ADD COLUMN {name} TEXT")
        if "reviewer_id" not in audit_columns:
            db.execute("ALTER TABLE audits ADD COLUMN reviewer_id INTEGER REFERENCES audit_reviewers(id)")
        db.commit()
