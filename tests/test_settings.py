import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from audit_app.config import load_config
from audit_app.database import connect, init_db
from audit_app.job import run_job
from audit_app.settings import display_percent, save_selection_percent, selection_percent
from audit_app.web import render


NOW = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)


class OneListing:
    def __init__(self, number):
        self.number = number

    def active_new_listings(self, start, end):
        yield {
            "listing_id": self.number, "mls_number": self.number, "status": "Active",
            "entry_timestamp": (NOW - timedelta(hours=1)).isoformat(), "address": "1 Example Street",
            "agent_name": "Example Agent", "agent_email": "agent@example.invalid",
            "brokerage_id": "office-" + self.number, "brokerage_name": "Example Realty",
            "brokerage_email": "office@example.invalid", "broker_id": "broker-" + self.number,
            "broker_name": "Example Broker", "broker_email": "broker@example.invalid",
        }


class SelectionSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = replace(load_config(), env="test", database_path=str(Path(self.temp.name) / "audit.sqlite3"),
                              rate=0.05, admin_email="admin@example.invalid", test_end_at=None)
        init_db(self.config.database_path)

    def test_saved_percentage_controls_future_runs_and_survives_config_reload(self):
        self.assertEqual(selection_percent(self.config), Decimal("5.00"))
        self.assertEqual(display_percent(selection_percent(self.config)), "5")
        save_selection_percent(self.config, "0")
        self.assertEqual(run_job(self.config, OneListing("A"), now=NOW)["selected"], 0)
        save_selection_percent(self.config, "100")
        config_after_restart = replace(self.config, rate=0.05)
        self.assertEqual(selection_percent(config_after_restart), Decimal("100"))
        result = run_job(config_after_restart, OneListing("B"), now=NOW + timedelta(minutes=1),
                         sender=lambda *args: "fake-message-id")
        self.assertEqual(result["selected"], 1)
        with connect(self.config.database_path) as db:
            metadata = json.loads(db.execute("SELECT selection_metadata FROM audits").fetchone()[0])
            self.assertEqual(db.execute("SELECT count(*) FROM listings").fetchone()[0], 2)
        self.assertEqual(metadata["rate"], 1.0)
        self.assertIn('100%', render(config_after_restart, "admin"))

    def test_invalid_percentages_are_rejected_without_changing_saved_value(self):
        save_selection_percent(self.config, "7.50")
        self.assertEqual(display_percent(selection_percent(self.config)), "7.5")
        for value in ("", "-1", "100.01", "2.345", "nan", "1e2", "5%"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                save_selection_percent(self.config, value)
        self.assertEqual(selection_percent(self.config), Decimal("7.50"))

    def test_setting_does_not_reopen_ended_test_window(self):
        ended = replace(self.config, test_end_at=NOW - timedelta(seconds=1))
        save_selection_percent(ended, "100")
        result = run_job(ended, OneListing("C"), now=NOW)
        self.assertEqual(result["skipped"], "test_window_closed")
        self.assertIn("will not restart listing collection", render(ended, "admin"))


if __name__ == "__main__":
    unittest.main()
