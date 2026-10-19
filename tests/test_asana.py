import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from audit_app.asana import follow_up
from audit_app.database import connect, init_db


class AsanaTests(unittest.TestCase):
    def test_long_issues_have_bounded_draft_and_complete_copy_details(self):
        audit={'id':12,'mls_number':'ABC & 123','address':'Example Street','issues':'x'*5000}
        title, notes, draft=follow_up(SimpleNamespace(public_url='https://example.invalid'),audit)
        self.assertIn(audit['issues'],notes)
        self.assertEqual(parse_qs(urlsplit(draft).query)['name'][0],title)
        self.assertIn('[Full issues available in AuditDesk]',parse_qs(urlsplit(draft).query)['notes'][0])
        self.assertIn('/?tab=outcome&id=12',parse_qs(urlsplit(draft).query)['notes'][0])
        self.assertLess(len(draft),2000)

    def test_version4_migration_adds_task_link_and_preserves_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'audit.sqlite3'
            init_db(path)
            with connect(path) as db:
                db.execute('ALTER TABLE audits DROP COLUMN asana_task_url')
                db.execute("INSERT INTO selection_settings VALUES(1,'7','2026')")
                db.execute('PRAGMA user_version=4'); db.commit()
            init_db(path); init_db(path)
            with connect(path) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],10)
                self.assertIn('asana_task_url',{row['name'] for row in db.execute('PRAGMA table_info(audits)')})
                self.assertEqual(db.execute('SELECT rate_percent FROM selection_settings').fetchone()[0],'7')
