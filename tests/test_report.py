import tempfile
import unittest
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from audit_app.config import load_config
from audit_app.database import connect, init_db
from audit_app.report import daily_audit_report
from audit_app.web import render


class DailyReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = replace(load_config(), env="test", database_path=str(Path(self.temp.name) / "audit.sqlite3"))
        init_db(self.config.database_path)

    def test_ninety_local_days_count_unique_listings_and_audits(self):
        with connect(self.config.database_path) as db:
            for number, processed in (("A", "2026-09-28T03:30:00+00:00"),
                                      ("B", "2026-09-28T14:00:00+00:00"),
                                      ("C", "2026-06-01T14:00:00+00:00")):
                listing_id = db.execute("""INSERT INTO listings(bridge_listing_id,mls_number,status,entry_timestamp,
                    address,first_processed_at,processing_status) VALUES(?,?,'Active',?,'Example Address',?,'processed_not_selected')""",
                    (number, number, processed, processed)).lastrowid
                if number == "A":
                    db.execute("""INSERT INTO audits(listing_id,selected_at,intended_to,intended_cc,actual_recipients,
                        test_mode,email_status,selection_metadata) VALUES(?,?,'[]','[]','[]',1,'email_sent','{}')""",
                        (listing_id, processed))
            for started, status in (("2026-09-28T12:00:00+00:00", "completed"),
                                    ("2026-09-27T12:00:00+00:00", "completed"),
                                    ("2026-09-25T12:00:00+00:00", "completed"),
                                    ("2026-09-24T12:00:00+00:00", "test_window_closed")):
                db.execute("INSERT INTO runs(started_at,status) VALUES(?,?)", (started, status))
            db.commit()

        rows = daily_audit_report(self.config, today=date(2026, 9, 28))
        by_day = {row["date"]: row for row in rows}
        self.assertEqual(len(rows), 90)
        self.assertEqual(rows[0]["date"], date(2026, 9, 28))
        self.assertEqual(rows[-1]["date"], date(2026, 9, 28) - timedelta(days=89))
        self.assertEqual((by_day[date(2026, 9, 27)]["listings"], by_day[date(2026, 9, 27)]["audited"],
                          by_day[date(2026, 9, 27)]["percentage"]), (1, 1, 100.0))
        self.assertEqual((by_day[date(2026, 9, 28)]["listings"], by_day[date(2026, 9, 28)]["audited"],
                          by_day[date(2026, 9, 28)]["percentage"]), (1, 0, 0.0))
        self.assertEqual((by_day[date(2026, 9, 25)]["status"], by_day[date(2026, 9, 25)]["percentage"]),
                         ("Recorded", 0.0))
        self.assertEqual((by_day[date(2026, 9, 26)]["status"], by_day[date(2026, 9, 26)]["percentage"]),
                         ("No run", None))
        self.assertEqual((by_day[date(2026, 9, 24)]["status"], by_day[date(2026, 9, 24)]["percentage"]),
                         ("Test ended", None))
        self.assertNotIn(date(2026, 6, 1), by_day)

    def test_report_is_under_admin_and_does_not_change_data(self):
        before = Path(self.config.database_path).stat().st_size
        page = render(self.config, "report")
        self.assertIn('href="/?tab=report">Daily audit report</a>', page)
        self.assertIn("Past 90 days", page)
        self.assertIn("Run status", page)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM listings").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM audits").fetchone()[0], 0)
        self.assertEqual(Path(self.config.database_path).stat().st_size, before)


if __name__ == "__main__":
    unittest.main()
