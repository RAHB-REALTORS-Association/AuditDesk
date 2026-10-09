"""Recovery uploads are validated without changing the live database."""
import io
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from audit_app.backup import backup, restore, restore_details, stage_restore, validate
from audit_app.config import load_config
from audit_app.database import connect, init_db


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = replace(load_config(), env='test', database_path=str(Path(self.temp.name)/'live.sqlite3'),
                              bootstrap_admins=('admin@example.invalid',))
        init_db(self.config.database_path)
        with connect(self.config.database_path) as db:
            db.execute("INSERT INTO selection_settings VALUES(1,'7','2026')")
            db.commit()
        self.snapshot = Path(self.temp.name)/'snapshot.sqlite3'
        backup(self.config,self.snapshot)

    def test_staging_preserves_original_schema_and_live_state_then_offline_restore_works(self):
        with connect(self.snapshot) as db:
            db.execute('DROP TABLE workflow_settings')
            db.execute('PRAGMA user_version=3')
            db.commit()
        with connect(self.config.database_path) as db:
            db.execute("UPDATE selection_settings SET rate_percent='20'")
            db.commit()
        identifier=stage_restore(self.config,io.BytesIO(self.snapshot.read_bytes()))
        details=restore_details(self.config,identifier)
        self.assertEqual(details['schema'],3)
        self.assertEqual(Path(details['path']).stat().st_mode & 0o777,0o600)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT rate_percent FROM selection_settings').fetchone()[0],'20')
            self.assertEqual(db.execute("SELECT action FROM activity_events ORDER BY id DESC").fetchone()[0], 'backup.restore_staged')
        restore(self.config,details['path'],'RESTORE STOPPED AUDITDESK')
        self.assertEqual(validate(self.config.database_path),10)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT rate_percent FROM selection_settings').fetchone()[0],'7')
        self.assertEqual(len(list(Path(self.temp.name).glob('live.sqlite3.before-restore-*'))),1)

    def test_invalid_newer_and_oversized_uploads_leave_no_staged_files(self):
        for value in (b'',b'not a database'):
            with self.assertRaises(ValueError):
                stage_restore(self.config,io.BytesIO(value))
        with connect(self.snapshot) as db:
            db.execute('PRAGMA user_version=99'); db.commit()
        with self.assertRaises(ValueError):
            stage_restore(self.config,io.BytesIO(self.snapshot.read_bytes()))
        with patch('audit_app.backup.MAX_BACKUP_BYTES',4), self.assertRaises(ValueError):
            stage_restore(self.config,io.BytesIO(b'12345'))
        self.assertEqual(list((Path(self.temp.name)/'restore-uploads').iterdir()),[])
        self.assertEqual(validate(self.config.database_path),10)

    def test_administrator_lockout_and_non_audit_databases_are_rejected(self):
        with self.assertRaises(ValueError):
            stage_restore(replace(self.config,bootstrap_admins=()),io.BytesIO(self.snapshot.read_bytes()))
        foreign = Path(self.temp.name)/'foreign.sqlite3'
        with sqlite3.connect(foreign) as db:
            db.execute('CREATE TABLE unrelated(id INTEGER)')
        with self.assertRaises(ValueError):
            stage_restore(self.config,io.BytesIO(foreign.read_bytes()))
        for identifier in ('../live','bad','a'*32):
            with self.assertRaises(ValueError):restore_details(self.config,identifier)
        with self.assertRaises(ValueError):
            stage_restore(replace(self.config,env='development'),io.BytesIO(self.snapshot.read_bytes()))
