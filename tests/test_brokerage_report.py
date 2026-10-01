import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path

from audit_app.brokerage_pdf import build_brokerage_pdf
from audit_app.brokerage_report import brokerage_statistics
from audit_app.office_backfill import backfill_office_addresses
from audit_app.bridge import office_address
from audit_app.config import load_config
from audit_app.database import connect, init_db
from audit_app.web import render


class BrokerageReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = replace(load_config(), env="test", database_path=str(Path(self.temp.name) / "audit.sqlite3"))
        init_db(self.config.database_path)

    def add_listing(self, number, office_id, processed, outcome=None, selected=False, name="Example Realty", office_address=None):
        with connect(self.config.database_path) as db:
            listing_id = db.execute("""INSERT INTO listings(bridge_listing_id,mls_number,status,entry_timestamp,
                address,brokerage_id,brokerage_name,brokerage_address,first_processed_at,processing_status)
                VALUES(?,?,'Active',?,'Example Address',?,?,?,?,'processed_not_selected')""",
                (number, number, processed, office_id, name, office_address, processed)).lastrowid
            if selected:
                db.execute("""INSERT INTO audits(listing_id,selected_at,intended_to,intended_cc,actual_recipients,
                    test_mode,email_status,selection_metadata,outcome) VALUES(?,?,'[]','[]','[]',1,'email_sent','{}',?)""",
                    (listing_id, processed, outcome))
            db.commit()

    def test_same_name_branches_combine_and_outcome_rates_use_completed_audits(self):
        self.add_listing("A", "office-one", "2026-06-28T04:30:00+00:00", "passed", True, office_address="1 First Street, Hamilton ON")
        self.add_listing("B", "office-one", "2026-09-27T14:00:00+00:00", "failed", True)
        self.add_listing("C", "office-one", "2026-09-28T14:00:00+00:00", selected=True)
        self.add_listing("D", "office-two", "2026-09-28T14:00:00+00:00", name="  example   REALTY ", office_address="2 Second Street, Toronto ON")
        self.add_listing("E", "office-one", "2026-06-28T03:30:00+00:00", "passed", True)
        self.add_listing("F", "office-one", "2026-03-15T14:00:00+00:00")
        self.add_listing("G", "office-three", "2026-09-28T14:00:00+00:00", name="Other Realty")

        three = brokerage_statistics(self.config, "3m", date(2026, 9, 28))
        self.assertEqual(three["start"], date(2026, 6, 28))
        self.assertEqual((three["total"], three["audited"], three["completed"], three["percentage"]),
                         (5, 3, 2, 60.0))
        self.assertEqual((three["pass_rate"], three["fail_rate"]), (50.0, 50.0))
        self.assertEqual(len(three["rows"]), 2)
        main = next(row for row in three["rows"] if row["name"].casefold() == "example realty")
        self.assertEqual((main["listings"], main["audited"], main["passed"], main["failed"], main["branch_count"]),
                         (4, 3, 1, 1, 2))
        self.assertEqual({branch["office_id"]: (branch["address"], branch["listings"], branch["audited"])
                          for branch in main["branches"]},
                         {"office-one": ("1 First Street, Hamilton ON", 3, 3),
                          "office-two": ("2 Second Street, Toronto ON", 1, 0)})
        page = render(self.config, "brokerages", period="3m")
        self.assertIn('data-sort-groups', page)
        self.assertIn('aria-expanded="false"', page)
        self.assertIn('2 Second Street, Toronto ON', page)
        other = next(row for row in three["rows"] if row["name"] == "Other Realty")
        self.assertIsNone(other["pass_rate"])
        self.assertIsNone(other["fail_rate"])
        self.assertEqual(brokerage_statistics(self.config, "6m", date(2026, 9, 28))["total"], 6)
        self.assertEqual(brokerage_statistics(self.config, "1y", date(2026, 9, 28))["total"], 7)

    def test_view_and_pdf_handle_no_completed_audits(self):
        self.add_listing("A", "office-one", "2026-09-28T14:00:00+00:00", selected=True)
        report = brokerage_statistics(self.config, "3m", date(2026, 9, 28))
        self.assertIsNone(report["pass_rate"])
        page = render(self.config, "brokerages", period="3m")
        self.assertIn("Pass rate", page)
        self.assertIn("Fail rate", page)
        self.assertIn('href="/reports/brokerages.pdf?period=3m"', page)
        pdf = build_brokerage_pdf(report)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertIn(b"%%EOF", pdf[-20:])

    def test_invalid_period_is_rejected(self):
        with self.assertRaises(ValueError):
            brokerage_statistics(self.config, "2m")

    def test_existing_office_address_can_be_backfilled_without_audit(self):
        self.add_listing("A", "office-one", "2026-09-28T14:00:00+00:00")
        fields = self.config.field_map["Office"]
        profile = {fields["address1"]: "10 Main St", fields["city"]: "Hamilton",
                   fields["province"]: "ON", fields["postal_code"]: "L8P 1A1"}
        self.assertEqual(office_address(profile, fields), "10 Main St, Hamilton ON L8P 1A1")

        class FakeBridge:
            offices = {}
            def inspect_metadata(self):
                return {}
            def _one(self, resource, key, cache):
                return profile

        result = backfill_office_addresses(self.config, FakeBridge())
        self.assertEqual(result, {"offices_checked": 1, "offices_updated": 1, "offices_without_address": 0})
        self.assertEqual(backfill_office_addresses(self.config, FakeBridge())["offices_checked"], 0)
        report = brokerage_statistics(self.config, "3m", date(2026, 9, 28))
        self.assertEqual(report["rows"][0]["branches"][0]["address"], "10 Main St, Hamilton ON L8P 1A1")
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM audits").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
