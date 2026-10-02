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
from audit_app.settings import (brokerage_cooldown_days, display_percent, save_brokerage_cooldown_days,
                                save_selection_percent, selection_percent, save_workflow_settings, workflow_config)
from audit_app.web import render


NOW = datetime(2026, 9, 24, 13, tzinfo=timezone.utc)


class OneListing:
    def __init__(self, number, office_id=None):
        self.number = number
        self.office_id = office_id or "office-" + number

    def active_new_listings(self, start, end):
        yield {
            "originating_system_name": "Cornerstone", "agent_mls_id": "DEMO-MEMBER", "listing_id": self.number, "mls_number": self.number, "status": "Active",
            "entry_timestamp": (NOW - timedelta(hours=1)).isoformat(), "address": "1 Example Street",
            "agent_name": "Example Agent", "agent_email": "agent@example.invalid",
            "brokerage_id": self.office_id, "brokerage_name": "Example Realty",
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

    def test_saved_brokerage_cooldown_controls_future_runs_and_survives_restart(self):
        self.assertEqual(brokerage_cooldown_days(self.config), 14)
        save_selection_percent(self.config, "100")
        self.assertEqual(run_job(self.config, OneListing("A", "same-office"), now=NOW)["selected"], 1)
        save_brokerage_cooldown_days(self.config, "2")
        self.assertEqual(run_job(self.config, OneListing("B", "same-office"),
                                 now=NOW + timedelta(minutes=1))["selected"], 0)
        save_brokerage_cooldown_days(self.config, "0")
        restarted = replace(self.config, brokerage_cooldown_days=14)
        self.assertEqual(brokerage_cooldown_days(restarted), 0)
        self.assertEqual(run_job(restarted, OneListing("C", "same-office"),
                                 now=NOW + timedelta(minutes=2))["selected"], 1)
        with connect(self.config.database_path) as db:
            metadata = json.loads(db.execute("SELECT selection_metadata FROM audits ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertEqual(metadata["brokerage_cooldown_days"], 0)
        self.assertRegex(render(restarted, "admin"), r'id="cooldown-days"[^>]*value="0"')

    def test_invalid_cooldowns_do_not_change_saved_value_or_percentage(self):
        save_selection_percent(self.config, "7")
        save_brokerage_cooldown_days(self.config, "21")
        for value in ("", "-1", "1.5", "366", "1e2", "+1", "abc", "01"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                save_brokerage_cooldown_days(self.config, value)
        self.assertEqual(brokerage_cooldown_days(self.config), 21)
        self.assertEqual(selection_percent(self.config), Decimal("7"))

    def test_version_two_database_gains_cooldown_setting_without_losing_rate(self):
        save_selection_percent(self.config, "12")
        with connect(self.config.database_path) as db:
            db.execute("DROP TABLE brokerage_cooldown_settings")
            db.execute("PRAGMA user_version=2")
            db.commit()
        init_db(self.config.database_path)
        init_db(self.config.database_path)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 8)
        self.assertEqual(selection_percent(self.config), Decimal("12"))
        self.assertEqual(brokerage_cooldown_days(self.config), 14)

    def test_setting_does_not_reopen_ended_test_window(self):
        ended = replace(self.config, test_end_at=NOW - timedelta(seconds=1))
        save_selection_percent(ended, "100")
        result = run_job(ended, OneListing("C"), now=NOW)
        self.assertEqual(result["skipped"], "test_window_closed")
        self.assertIn("will not restart listing collection", render(ended, "admin"))

    def test_workflow_settings_control_broker_cooldown_and_intake_window(self):
        save_selection_percent(self.config, '100')
        save_workflow_settings(self.config, {'broker_cooldown_days':'2','window_hours':'48'})
        config = replace(self.config, brokerage_cooldown_days=0)
        first = OneListing('A')
        run_job(config, first, now=NOW)
        second = OneListing('B')
        original = second.active_new_listings
        def shared_broker(start, end):
            for item in original(start,end):
                item['broker_id'] = 'broker-A'
                yield item
        second.active_new_listings = shared_broker
        self.assertEqual(run_job(config, second, now=NOW+timedelta(minutes=1))['selected'],0)
        save_workflow_settings(config, {'broker_cooldown_days':'0','window_hours':'48'})
        third = OneListing('C')
        original_third = third.active_new_listings
        def same_broker(start, end):
            for item in original_third(start,end):
                item['broker_id'] = 'broker-A'
                yield item
        third.active_new_listings = same_broker
        self.assertEqual(run_job(config, third, now=NOW+timedelta(minutes=2))['selected'],1)
        restarted = workflow_config(replace(config,broker_cooldown_days=14,window_hours=24))
        self.assertEqual((restarted.broker_cooldown_days,restarted.window_hours),(0,48))
        # A fresh database demonstrates the initial window without catch-up history.
        fresh = replace(config,database_path=str(Path(self.temp.name)/'window.sqlite3'))
        init_db(fresh.database_path)
        save_workflow_settings(fresh, {'broker_cooldown_days':'0','window_hours':'48'})
        from unittest.mock import Mock
        client = Mock(); client.active_new_listings.return_value = []
        run_job(fresh,client,now=NOW)
        client.active_new_listings.assert_called_once_with(NOW-timedelta(hours=48),NOW)

    def test_workflow_validation_and_version_three_migration_preserve_values(self):
        save_selection_percent(self.config,'12')
        save_brokerage_cooldown_days(self.config,'21')
        with connect(self.config.database_path) as db:
            db.execute('DROP TABLE workflow_settings')
            db.execute('PRAGMA user_version=3')
            db.commit()
        init_db(self.config.database_path)
        init_db(self.config.database_path)
        self.assertEqual(selection_percent(self.config),Decimal('12'))
        self.assertEqual(brokerage_cooldown_days(self.config),21)
        save_workflow_settings(self.config,{'broker_cooldown_days':'3','window_hours':'36'})
        for name, value in (('broker_cooldown_days','366'),('broker_cooldown_days','-1'),
                            ('window_hours','0'),('window_hours','169'),('window_hours','1.5')):
            form={'broker_cooldown_days':'4','window_hours':'24',name:value}
            with self.subTest(name=name,value=value), self.assertRaises(ValueError):
                save_workflow_settings(self.config,form)
        current=workflow_config(self.config)
        self.assertEqual((current.broker_cooldown_days,current.window_hours),(3,36))


if __name__ == "__main__":
    unittest.main()
