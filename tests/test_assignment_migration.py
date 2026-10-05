"""Replace the development roster with account assignments."""
import sqlite3
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from audit_app.backup import backup, restore, validate
from audit_app.config import load_config
from audit_app.database import connect, init_db


class AssignmentMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'version1.sqlite3')
        source = subprocess.check_output(
            ['git', 'show', 'ab2e1f0:audit_app/database.py'], text=True)
        namespace = {}
        exec(source, namespace)
        namespace['init_db'](self.path)
        with connect(self.path) as db:
            db.execute("INSERT INTO audit_reviewers(name,created_at,updated_at) VALUES('Taylor','2026','2026')")
            db.execute("INSERT INTO app_users(email,display_name,role) VALUES('taylor@example.invalid','Taylor','reviewer')")
            for number, outcome in ((1, None), (2, 'passed')):
                db.execute("""INSERT INTO listings(id,bridge_listing_id,mls_number,status,entry_timestamp,
                    address,first_processed_at,processing_status)
                    VALUES(?,?,?,'Active','2026','Example','2026','selected_for_audit')""",
                           (number, str(number), str(number)))
                db.execute("""INSERT INTO audits(id,listing_id,selected_at,intended_to,intended_cc,
                    actual_recipients,test_mode,email_status,selection_metadata,reviewer_id,outcome)
                    VALUES(?,?,'2026','[]','[]','[]',1,'email_sent','{}',1,?)""", (number, number, outcome))
            db.commit()

    def test_migration_drops_development_roster_and_is_idempotent(self):
        init_db(self.path)
        init_db(self.path)
        with connect(self.path) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 9)
            rows = db.execute('SELECT assignee_user_id,outcome FROM audits ORDER BY id').fetchall()
            self.assertEqual([tuple(row) for row in rows], [(None, None), (None, 'passed')])
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='audit_reviewers'").fetchone())
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('UPDATE audits SET assignee_user_id=999 WHERE id=1')
            self.assertIsNone(db.execute('PRAGMA foreign_key_check').fetchone())

    def test_version1_restore_upgrades_and_current_backup_restores(self):
        self.assertEqual(validate(self.path), 1)
        config = replace(load_config(), env='test', database_path=str(Path(self.temp.name) / 'live.sqlite3'),
                         bootstrap_admins=('admin@example.invalid',))
        restore(config, self.path, 'RESTORE STOPPED AUDITDESK')
        self.assertEqual(validate(config.database_path), 9)
        snapshot = backup(config, str(Path(self.temp.name) / 'current.sqlite3'))
        self.assertEqual(validate(snapshot), 9)
        restore(config, snapshot, 'RESTORE STOPPED AUDITDESK')
        with connect(config.database_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM audits').fetchone()[0], 2)
