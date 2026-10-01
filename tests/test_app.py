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
from audit_app.emailer import message_content, resolve_recipients, send_email
from audit_app.job import choose_fairly, run_job, deliver_audit
from audit_app.simulation import simulate_cycle
from audit_app.templates import current_templates, format_message_parts, save_templates, validate_templates


NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def listing(number, office="office-1", broker="broker-1"):
    return {
        "originating_system_name": "Cornerstone", "agent_mls_id": "DEMO-MEMBER", "listing_id": str(number), "bridge_listing_id": str(number), "mls_number": str(number),
        "status": "Active", "entry_timestamp": (NOW - timedelta(hours=1)).isoformat(),
        "address": f"{number} Main Street", "agent_id": "agent", "agent_name": "Agent Name",
        "agent_email": "agent@example.com", "brokerage_id": office,
        "brokerage_name": "Example Realty", "brokerage_email": "office@example.com",
        "broker_id": broker, "broker_name": "Broker Name", "broker_email": "broker@example.com",
        "broker_first_name": "Taylor",
    }


class FakeClient:
    def __init__(self, rows):
        self.rows = rows

    def active_new_listings(self, start, end):
        return iter(self.rows)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = replace(load_config(), env="test", database_path=str(Path(self.temp.name) / "audit.sqlite3"),
                              email_enabled=True, admin_email="admin@example.com", sendgrid_key="fake-key", from_address="audit@example.com", rate=1)

    def test_twice_does_not_repeat_audit_or_email(self):
        rows = [listing("1")]
        with patch("audit_app.job.send_email", return_value="message-1") as send:
            first = run_job(self.config, FakeClient(rows), random.Random(1), NOW)
            second = run_job(self.config, FakeClient(rows), random.Random(1), NOW)
        self.assertEqual(first["selected"], 1)
        self.assertEqual(second["new"], 0)
        self.assertEqual(send.call_count, 1)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM audits").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM email_attempts").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT broker_first_name FROM listings").fetchone()[0], "Taylor")

    def test_scheduler_skips_before_eight_and_after_a_daily_run(self):
        client = FakeClient([listing("1")])
        before = run_job(self.config, client, random.Random(1), NOW - timedelta(minutes=1), only_if_needed=True)
        self.assertEqual(before["skipped"], "before_daily_time")
        with patch("audit_app.job.send_email", return_value="message-1"):
            first = run_job(self.config, client, random.Random(1), NOW, only_if_needed=True)
        self.assertEqual(first["selected"], 1)
        with patch.object(client, "active_new_listings", side_effect=AssertionError("Bridge queried twice")):
            second = run_job(self.config, client, random.Random(1), NOW + timedelta(hours=2), only_if_needed=True)
        self.assertEqual(second["skipped"], "already_ran_today")
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM runs").fetchone()[0], 1)

    def test_test_mode_substitutes_all_recipients_at_sendgrid_boundary(self):
        init_db(self.config.database_path)
        row = listing("1")
        to, cc, actual = resolve_recipients(row, self.config)
        self.assertEqual(actual, ["admin@example.com"])

        class Response:
            status = 202
            headers = {"X-Message-Id": "id-1"}
            def __enter__(self): return self
            def __exit__(self, *args): pass

        with patch("audit_app.emailer.urllib.request.urlopen", return_value=Response()) as urlopen:
            send_email(self.config, row, 1, to, cc, ["wrong@example.com"])
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload["personalizations"][0], {"to": [{"email": "admin@example.com"}]})
        self.assertIn("broker@example.com", payload["subject"])
        self.assertIn("agent@example.com", payload["content"][0]["value"])

    def test_test_window_stops_live_queries_and_email(self):
        config = replace(self.config, test_end_at=NOW - timedelta(seconds=1))
        client = FakeClient([listing("1")])
        with patch.object(client, "active_new_listings", side_effect=AssertionError("Bridge was queried")):
            result = run_job(config, client, random.Random(1), NOW)
        self.assertEqual(result["skipped"], "test_window_closed")
        with connect(config.database_path) as db:
            self.assertEqual(db.execute("SELECT status FROM runs").fetchone()[0], "test_window_closed")
            self.assertEqual(db.execute("SELECT count(*) FROM audits").fetchone()[0], 0)
        to, cc, actual = resolve_recipients(listing("1"), config)
        with patch("audit_app.emailer.urllib.request.urlopen", side_effect=AssertionError("Email was sent")):
            with self.assertRaisesRegex(Exception, "Test window has ended"):
                send_email(config, listing("1"), 1, to, cc, actual)

    def test_production_routes_to_broker_and_copies_office_and_agent(self):
        init_db(self.config.database_path)
        config = replace(self.config, env="production")
        to, cc, actual = resolve_recipients(listing("1"), config)
        self.assertEqual(to, ["broker@example.com"])
        self.assertEqual(cc, ["office@example.com", "agent@example.com"])
        self.assertEqual(actual, to + cc)

        class Response:
            status = 202
            headers = {"X-Message-Id": "id-3"}
            def __enter__(self): return self
            def __exit__(self, *args): pass

        with patch("audit_app.emailer.urllib.request.urlopen", return_value=Response()) as urlopen:
            send_email(config, listing("1"), 1, ["wrong@example.com"], [], [])
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload["personalizations"][0], {
            "to": [{"email": "broker@example.com"}],
            "cc": [{"email": "office@example.com"}, {"email": "agent@example.com"}],
        })

    def test_brokerage_email_is_optional_and_duplicate_addresses_are_deduplicated(self):
        config = replace(self.config, env="production")
        row = listing("1")
        row["brokerage_email"] = None
        self.assertEqual(resolve_recipients(row, config)[1], ["agent@example.com"])
        row["brokerage_email"] = " BROKER@example.com "
        self.assertEqual(resolve_recipients(row, config)[1], ["agent@example.com"])
        row["brokerage_email"] = " office@example.com "
        self.assertEqual(resolve_recipients(row, config)[1], ["office@example.com", "agent@example.com"])

    def test_cooldown_finds_another_brokerage(self):
        rows = [listing("1", "office-a", "broker-a"), listing("2", "office-b", "broker-b")]
        history = [{"brokerage_id": "office-a", "broker_id": "broker-a", "office_recent": True, "broker_recent": True}]
        chosen = choose_fairly(rows, history, self.config, random.Random(1))
        self.assertEqual([item["bridge_listing_id"] for item, _ in chosen], ["2"])

    def test_low_volume_days_do_not_round_to_zero(self):
        config = replace(self.config, rate=0.05)
        draws = sum(bool(choose_fairly([listing(str(i), f"office-{i}", f"broker-{i}")], [], config, random.Random(i))) for i in range(1000))
        self.assertGreater(draws, 25)
        self.assertLess(draws, 75)

    def test_failed_email_retries_without_second_audit(self):
        with patch("audit_app.job.send_email", side_effect=__import__("audit_app.emailer", fromlist=["EmailError"]).EmailError("SendGrid returned HTTP 503")):
            run_job(self.config, FakeClient([listing("1")]), random.Random(1), NOW)
        with patch("audit_app.job.send_email", return_value="message-2") as send:
            self.assertTrue(deliver_audit(self.config, 1, retry=True))
            self.assertFalse(deliver_audit(self.config, 1, retry=True))
        self.assertEqual(send.call_count, 1)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM audits").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM email_attempts").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT email_status FROM audits").fetchone()[0], "email_sent")

    def test_saved_merge_tags_are_used_by_next_email(self):
        init_db(self.config.database_path)
        subject = "Paperwork request for {{broker_first_name}} about {{mls_number}}"
        body = "Hello {{broker_first_name}}. MLS {{mls_number}} at {{address}}. Agent: {{agent_name}}. Brokerage: {{brokerage_name}}. Broker: {{broker_name}}."
        save_templates(self.config, subject, body)
        self.assertEqual(current_templates(self.config)[:2], (subject, body))
        rendered_subject, rendered_body = message_content(listing("42"), self.config, ["broker@example.com"], ["agent@example.com"])
        self.assertIn("Paperwork request for Taylor about 42", rendered_subject)
        self.assertIn("Hello Taylor.", rendered_body)
        self.assertIn("42 Main Street", rendered_body)
        self.assertIn("Broker Name", rendered_body)

    def test_formatted_email_saves_and_sends_safe_html_with_plain_text(self):
        init_db(self.config.database_path)
        body = ("<div>Hello <b>{{broker_first_name}}</b>,</div>"
                "<div><i>{{mls_number}}</i> <u>{{address}}</u> "
                "{{agent_name}} {{brokerage_name}}</div>"
                "<script>alert('bad')</script><img src=x onerror=alert(1)>")
        save_templates(self.config, "Audit {{mls_number}}", body, "html")
        _, saved, _, body_format = current_templates(self.config)
        self.assertEqual(body_format, "html")
        self.assertIn("<strong>{{broker_first_name}}</strong>", saved)
        self.assertNotIn("script", saved)
        self.assertNotIn("onerror", saved)

        class Response:
            status = 202
            headers = {"X-Message-Id": "id-2"}
            def __enter__(self): return self
            def __exit__(self, *args): pass

        row = listing("42")
        row["address"] = "42 Main & King"
        to, cc, actual = resolve_recipients(row, self.config)
        with patch("audit_app.emailer.urllib.request.urlopen", return_value=Response()) as urlopen:
            send_email(self.config, row, 2, to, cc, actual)
        payload = json.loads(urlopen.call_args.args[0].data)
        content = {part["type"]: part["value"] for part in payload["content"]}
        self.assertIn("Hello Taylor,", content["text/plain"])
        self.assertIn("<strong>Taylor</strong>", content["text/html"])
        self.assertIn("<em>42</em>", content["text/html"])
        self.assertIn("<u>42 Main &amp; King</u>", content["text/html"])
        self.assertNotIn("<script", content["text/html"])
        self.assertNotIn("<img", content["text/html"])
        self.assertEqual(payload["personalizations"][0], {"to": [{"email": "admin@example.com"}]})

    def test_html_template_keeps_typed_newlines_and_editor_breaks(self):
        body = "Hello {{broker_first_name}},\r\n<br>MLS {{mls_number}}\n{{address}}<br>{{agent_name}} {{brokerage_name}}"
        _, plain, rich = format_message_parts("Audit", body, listing("42"), False, [], [], "html")
        self.assertEqual(plain, "Hello Taylor,\n\nMLS 42\n42 Main Street\nAgent Name Example Realty")
        self.assertIn("Hello Taylor,<br><br>MLS 42<br>42 Main Street<br>Agent Name Example Realty", rich)
        self.assertNotIn("\n", rich)

    def test_broker_first_name_falls_back_for_older_saved_listing(self):
        row = listing("42")
        row.pop("broker_first_name")
        row["broker_name"] = "Smith, Morgan"
        subject, body = __import__("audit_app.templates", fromlist=["format_message"]).format_message(
            "Hello {{broker_first_name}}", "{{mls_number}} {{address}} {{agent_name}} {{brokerage_name}}", row, False, [], [])
        self.assertEqual(subject, "Hello Morgan")

    def test_existing_database_adds_broker_first_name_column(self):
        with connect(self.config.database_path) as db:
            db.execute("CREATE TABLE listings (id INTEGER PRIMARY KEY, broker_name TEXT)")
            db.commit()
        init_db(self.config.database_path)
        with connect(self.config.database_path) as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(listings)")}
        self.assertIn("broker_first_name", columns)

    def test_template_validation_rejects_unknown_or_missing_tags(self):
        with self.assertRaisesRegex(ValueError, "Unsupported merge tag"):
            validate_templates("Request {{unknown}}", "{{mls_number}} {{address}} {{agent_name}} {{brokerage_name}}")
        with self.assertRaisesRegex(ValueError, "must include"):
            validate_templates("Request", "{{mls_number}} {{address}}")

    def test_full_simulation_is_isolated_and_idempotent(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("Simulation attempted network access")):
            report = simulate_cycle(self.config)
        self.assertEqual(report["server_filtered_rows"], 2)
        self.assertEqual(report["selected_listing"], "DEMO-B")
        self.assertEqual(report["actual_recipients"], ["admin@example.invalid"])
        self.assertEqual(report["second_run"]["new"], 0)
        self.assertEqual(report["audit_records_for_new_listings"], 1)
        self.assertEqual(report["final_email_status"], "email_sent")


if __name__ == "__main__":
    unittest.main()
