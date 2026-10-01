import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from audit_app.assignment import assign_reviewer, eligible_assignees
from audit_app.security import CAPABILITIES
from unittest.mock import patch
from audit_app.config import load_config
from audit_app.database import connect, init_db
from audit_app.web import render


class AssignmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = replace(load_config(), env="test", database_path=str(Path(self.temp.name) / "audit.sqlite3"))
        init_db(self.config.database_path)
        with connect(self.config.database_path) as db:
            listing_id = db.execute("""INSERT INTO listings(originating_system_name,bridge_listing_id,mls_number,status,entry_timestamp,address,first_processed_at,processing_status)
                VALUES('Cornerstone','demo','DEMO-1','Active','2026-09-25T12:00:00+00:00','1 Example Road','2026-09-25T12:00:00+00:00','email_sent')""").lastrowid
            db.execute("""INSERT INTO audits(listing_id,selected_at,intended_to,intended_cc,actual_recipients,test_mode,email_status,selection_metadata)
                VALUES(?,'2026-09-25T12:00:00+00:00','[]','[]','[]',1,'email_sent','{}')""", (listing_id,))
            db.commit()

    def person(self, name="Taylor Morgan", role="reviewer", active=True):
        with connect(self.config.database_path) as db:
            user_id = db.execute("INSERT INTO app_users(email,display_name,role,active) VALUES(?,?,?,?)",
                                 (name.replace(" ", ".") + "@example.invalid", name, role, int(active))).lastrowid
            db.commit()
        return user_id

    def test_active_accounts_and_assignment_status(self):
        self.assertIn('Not Started', render(self.config))
        user_id = self.person()
        self.person("Casey Lee", "manager")
        self.person("Alex Smith", "admin")
        self.person("Inactive Person", active=False)
        with connect(self.config.database_path) as db:
            self.assertEqual(len(eligible_assignees(db)), 3)
        assign_reviewer(self.config, 1, user_id)
        page = render(self.config)
        self.assertIn('In Progress', page)
        self.assertIn('Taylor Morgan', page)
        self.assertNotIn('Inactive Person', page)
        self.assertNotIn('Audit team', page)
        assign_reviewer(self.config, 1, None)
        self.assertIn('Not Started', render(self.config))

    def test_disabled_account_requires_reassignment(self):
        user_id = self.person()
        assign_reviewer(self.config, 1, user_id)
        with connect(self.config.database_path) as db:
            db.execute("UPDATE app_users SET active=0 WHERE id=?", (user_id,))
            db.commit()
        self.assertIn('Needs Reassignment', render(self.config))
        self.assertIn('Taylor Morgan (unavailable)', render(self.config))
        with self.assertRaisesRegex(ValueError, "permission to audit"):
            assign_reviewer(self.config, 1, user_id)
        with connect(self.config.database_path) as db:
            db.execute("UPDATE audits SET outcome='passed' WHERE id=1")
            db.commit()
        self.assertIn('Completed', render(self.config))

    def test_role_capability_is_checked_when_assigning(self):
        user_id = self.person()
        assign_reviewer(self.config, 1, user_id)
        with patch.dict(CAPABILITIES, {"reviewer": frozenset({"audits.read"})}):
            with connect(self.config.database_path) as db:
                self.assertEqual(eligible_assignees(db), [])
            self.assertIn('Needs Reassignment', render(self.config))
            with self.assertRaisesRegex(ValueError, "permission to audit"):
                assign_reviewer(self.config, 1, user_id)

    def test_missing_audit_and_account_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "permission to audit"):
            assign_reviewer(self.config, 1, 999)
        with self.assertRaisesRegex(ValueError, "Audit not found"):
            assign_reviewer(self.config, 999, self.person())
