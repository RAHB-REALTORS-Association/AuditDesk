import json
import random
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from audit_app.config import load_config
from audit_app.database import connect, init_db
from audit_app.emailer import EmailError, send_failure_email
from audit_app.job import run_job
from audit_app.outcomes import deliver_failure_notice, record_outcome
from audit_app.templates import current_failure_templates, save_failure_templates
from audit_app.web import render


NOW = datetime(2026, 9, 24, 13, 0, tzinfo=timezone.utc)


def listing(number):
    return {
        "originating_system_name": "Cornerstone", "agent_mls_id": "DEMO-MEMBER", "listing_id": number, "mls_number": number, "status": "Active",
        "entry_timestamp": (NOW - timedelta(hours=1)).isoformat(),
        "address": "42 Main & King", "agent_id": "agent", "agent_name": "Agent Name",
        "agent_email": "agent@example.com", "brokerage_id": "office",
        "brokerage_name": "Example Realty", "brokerage_email": "office@example.com",
        "broker_id": "broker", "broker_name": "Taylor Broker", "broker_first_name": "Taylor",
        "broker_email": "broker@example.com",
    }


class OneListing:
    def __init__(self, row): self.row = row
    def active_new_listings(self, start, end): return iter([self.row])


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = replace(load_config(), env="test", email_enabled=True, database_path=str(Path(self.temp.name) / "audit.sqlite3"),
                              admin_email="admin@example.com", sendgrid_key="fake-key", from_address="audit@example.com",
                              rate=1, test_end_at=None)
        init_db(self.config.database_path)
        with patch("audit_app.job.send_email", return_value="original-id"):
            run_job(self.config, OneListing(listing("42")), random.Random(1), NOW)

    def test_pass_records_result_without_followup_email(self):
        with patch("audit_app.outcomes.send_failure_email") as send:
            self.assertEqual(record_outcome(self.config, 1, "passed"), "passed")
        send.assert_not_called()
        with connect(self.config.database_path) as db:
            row = db.execute("SELECT outcome,issues,failure_email_status FROM audits WHERE id=1").fetchone()
            self.assertEqual(tuple(row), ("passed", None, None))
        with self.assertRaisesRegex(ValueError, "already has"):
            record_outcome(self.config, 1, "failed", "Missing form")

    def test_failure_requires_issues_and_sends_once_to_admin(self):
        with self.assertRaisesRegex(ValueError, "Describe the issues"):
            record_outcome(self.config, 1, "failed", "  ")
        with patch("audit_app.outcomes.send_failure_email", return_value="failure-id") as send:
            self.assertEqual(record_outcome(self.config, 1, "failed", "Missing signed agreement"), "failed")
        self.assertEqual(send.call_count, 1)
        with connect(self.config.database_path) as db:
            row = db.execute("SELECT outcome,issues,failure_email_status,failure_actual_recipients FROM audits WHERE id=1").fetchone()
            self.assertEqual(row["outcome"], "failed")
            self.assertEqual(row["issues"], "Missing signed agreement")
            self.assertEqual(row["failure_email_status"], "email_sent")
            self.assertEqual(json.loads(row["failure_actual_recipients"]), ["admin@example.com"])
            self.assertEqual(db.execute("SELECT count(*) FROM failure_email_attempts").fetchone()[0], 1)
        with self.assertRaisesRegex(ValueError, "already has"):
            record_outcome(self.config, 1, "failed", "Again")

    def test_failed_delivery_can_retry_without_duplicate_outcome(self):
        with patch("audit_app.outcomes.send_failure_email", side_effect=EmailError("HTTP 503")):
            record_outcome(self.config, 1, "failed", "Wrong price")
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute("SELECT failure_email_status FROM audits WHERE id=1").fetchone()[0], "email_failed")
        with patch("audit_app.outcomes.send_failure_email", return_value="retry-id") as send:
            self.assertTrue(deliver_failure_notice(self.config, 1, retry=True))
            self.assertFalse(deliver_failure_notice(self.config, 1, retry=True))
        self.assertEqual(send.call_count, 1)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM failure_email_attempts").fetchone()[0], 2)

    def test_failure_email_contains_issues_and_only_admin_in_test_mode(self):
        class Response:
            status = 202
            headers = {"X-Message-Id": "id"}
            def __enter__(self): return self
            def __exit__(self, *args): pass
        with patch("audit_app.emailer.urllib.request.urlopen", return_value=Response()) as urlopen:
            send_failure_email(self.config, listing("42"), 1, "Missing agreement\nWrong price")
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload["personalizations"][0], {"to": [{"email": "admin@example.com"}]})
        self.assertIn("Missing agreement\nWrong price", payload["content"][0]["value"])
        self.assertIn("Missing agreement<br>Wrong price", payload["content"][1]["value"])
        self.assertEqual(payload["custom_args"]["notice_type"], "audit_failure")

    def test_failure_template_and_review_page(self):
        subject, body, _, _ = current_failure_templates(self.config)
        self.assertIn("{{issues}}", body)
        save_failure_templates(self.config, subject, "<strong>{{issues}}</strong><br>{{mls_number}} {{address}}", "html")
        page = render(self.config, "outcome", form_values="Missing <form>", audit_id=1, preview_outcome=True)
        self.assertIn("Review failed-audit notice", page)
        self.assertIn("Missing &lt;form&gt;", page)
        self.assertIn("Record fail and send notice", page)
        self.assertIn("admin@example.com", page)

    def test_closed_test_window_records_failure_but_does_not_send(self):
        config = replace(self.config, test_end_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        with patch("audit_app.outcomes.send_failure_email") as send:
            record_outcome(config, 1, "failed", "Missing form")
        send.assert_not_called()
        with connect(config.database_path) as db:
            self.assertEqual(db.execute("SELECT failure_email_status FROM audits WHERE id=1").fetchone()[0], "email_pending")

    def test_test_audit_cannot_send_to_broker_after_mode_change(self):
        production = replace(self.config, env="production")
        with patch("audit_app.outcomes.send_failure_email") as send:
            record_outcome(production, 1, "failed", "Missing form")
        send.assert_not_called()
        with connect(production.database_path) as db:
            row = db.execute("SELECT outcome,failure_email_status FROM audits WHERE id=1").fetchone()
            self.assertEqual(tuple(row), ("failed", "email_blocked"))
        with patch("audit_app.emailer.urllib.request.urlopen", side_effect=AssertionError("Email was sent")):
            with self.assertRaisesRegex(EmailError, "test audit cannot send"):
                send_failure_email(production, listing("42"), 1, "Missing form")


if __name__ == "__main__":
    unittest.main()
