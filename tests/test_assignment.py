import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from audit_app.assignment import add_reviewer, assign_reviewer, delete_reviewer, rename_reviewer, set_reviewer_active
from audit_app.config import load_config
from audit_app.database import connect, init_db
from audit_app.web import render


class AssignmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = replace(load_config(), database_path=str(Path(self.temp.name) / "audit.sqlite3"))
        init_db(self.config.database_path)
        with connect(self.config.database_path) as db:
            listing_id = db.execute("""INSERT INTO listings(bridge_listing_id,mls_number,status,entry_timestamp,address,first_processed_at,processing_status)
                VALUES('demo','DEMO-1','Active','2026-09-25T12:00:00+00:00','1 Example Road','2026-09-25T12:00:00+00:00','email_sent')""").lastrowid
            db.execute("""INSERT INTO audits(listing_id,selected_at,intended_to,intended_cc,actual_recipients,test_mode,email_status,selection_metadata)
                VALUES(?,'2026-09-25T12:00:00+00:00','[]','[]','[]',1,'email_sent','{}')""", (listing_id,))
            db.commit()

    def test_assignment_status_and_inactive_name_history(self):
        initial = render(self.config)
        self.assertIn('Not Started', initial)
        self.assertIn('Unassigned', initial)

        add_reviewer(self.config, "  Taylor   Morgan  ")
        add_reviewer(self.config, "Casey Lee")
        with connect(self.config.database_path) as db:
            reviewer_id = db.execute("SELECT id FROM audit_reviewers WHERE name='Taylor Morgan'").fetchone()[0]
        assign_reviewer(self.config, 1, reviewer_id)
        active = render(self.config)
        self.assertIn('In Progress', active)
        self.assertIn('Taylor Morgan', active)
        self.assertIn('Casey Lee', active)

        set_reviewer_active(self.config, reviewer_id, False)
        inactive = render(self.config)
        self.assertIn('Taylor Morgan (inactive)', inactive)
        with self.assertRaisesRegex(ValueError, "active name"):
            assign_reviewer(self.config, 1, reviewer_id)
        rename_reviewer(self.config, reviewer_id, "Taylor M.")
        self.assertIn('Taylor M. (inactive)', render(self.config))
        set_reviewer_active(self.config, reviewer_id, True)
        assign_reviewer(self.config, 1, None)
        self.assertIn('Not Started', render(self.config))

    def test_completed_audit_keeps_assignee_and_roster_rejects_duplicates(self):
        add_reviewer(self.config, "Alex Smith")
        with self.assertRaisesRegex(ValueError, "already on the list"):
            add_reviewer(self.config, "alex smith")
        with connect(self.config.database_path) as db:
            reviewer_id = db.execute("SELECT id FROM audit_reviewers").fetchone()[0]
        assign_reviewer(self.config, 1, reviewer_id)
        with connect(self.config.database_path) as db:
            db.execute("UPDATE audits SET outcome='passed' WHERE id=1")
            db.commit()
        page = render(self.config)
        self.assertIn('Completed', page)
        self.assertIn('Alex Smith', page)
        with self.assertRaisesRegex(ValueError, "active name"):
            assign_reviewer(self.config, 1, 999)

    def test_delete_removes_name_but_keeps_audit_context(self):
        add_reviewer(self.config, "Jordan Lee")
        with connect(self.config.database_path) as db:
            reviewer_id = db.execute("SELECT id FROM audit_reviewers").fetchone()[0]
        assign_reviewer(self.config, 1, reviewer_id)
        with connect(self.config.database_path) as db:
            db.execute("UPDATE audits SET reviewer_name_snapshot=NULL WHERE id=1")
            db.commit()
        init_db(self.config.database_path)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute("SELECT reviewer_name_snapshot FROM audits WHERE id=1").fetchone()[0], "Jordan Lee")
        self.assertIn('value="delete"', render(self.config, "reviewers"))

        delete_reviewer(self.config, reviewer_id)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM audit_reviewers").fetchone()[0], 0)
            audit = db.execute("SELECT reviewer_id,reviewer_name_snapshot FROM audits WHERE id=1").fetchone()
            self.assertEqual(tuple(audit), (None, "Jordan Lee"))
        page = render(self.config)
        self.assertIn("Needs Reassignment", page)
        self.assertIn("Previously assigned: Jordan Lee", page)
        add_reviewer(self.config, "Jordan Lee")
        with connect(self.config.database_path) as db:
            replacement_id = db.execute("SELECT id FROM audit_reviewers").fetchone()[0]
        assign_reviewer(self.config, 1, replacement_id)
        self.assertIn("In Progress", render(self.config))
        self.assertNotIn("Needs Reassignment", render(self.config))

    def test_delete_keeps_completed_audit_assignee_name(self):
        add_reviewer(self.config, "Alex Smith")
        with connect(self.config.database_path) as db:
            reviewer_id = db.execute("SELECT id FROM audit_reviewers").fetchone()[0]
        assign_reviewer(self.config, 1, reviewer_id)
        with connect(self.config.database_path) as db:
            db.execute("UPDATE audits SET outcome='passed' WHERE id=1")
            db.commit()
        delete_reviewer(self.config, reviewer_id)
        page = render(self.config)
        self.assertIn("Completed", page)
        self.assertIn("Previously assigned: Alex Smith", page)


if __name__ == "__main__":
    unittest.main()
