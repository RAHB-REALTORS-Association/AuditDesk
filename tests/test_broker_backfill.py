import random
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from audit_app.broker_backfill import backfill_broker_contacts
from audit_app.config import load_config
from audit_app.database import connect, init_db
from audit_app.job import run_job
from test_app import FakeClient, NOW, listing


class BrokerBackfillTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = replace(load_config(), env='test', email_enabled=False, rate=1,
            database_path=str(Path(directory.name)/'audit.sqlite3'), admin_email='admin@example.invalid')
        init_db(self.config.database_path)

    def test_repairs_only_eligible_unsent_missing_contacts_and_does_not_send(self):
        rows = [listing(str(i), 'office-'+str(i), 'broker-'+str(i)) for i in range(1,6)]
        run_job(self.config, FakeClient(rows), random.Random(1), NOW)
        with connect(self.config.database_path) as db:
            db.execute('UPDATE listings SET broker_email=NULL')
            db.execute("UPDATE listings SET agent_membership_class='NL7 - Subscriber' WHERE bridge_listing_id='2'")
            db.execute("UPDATE audits SET email_status='email_sent' WHERE listing_id=(SELECT id FROM listings WHERE bridge_listing_id='3')")
            db.execute("UPDATE audits SET outcome='pass' WHERE listing_id=(SELECT id FROM listings WHERE bridge_listing_id='4')")
            db.execute("UPDATE listings SET broker_email='already@example.invalid' WHERE bridge_listing_id='5'")
            db.commit()
            previous = dict(db.execute("SELECT * FROM audits WHERE listing_id=(SELECT id FROM listings WHERE bridge_listing_id='1')").fetchone())
        client = Mock()
        client.listing_by_key.side_effect = lambda key: {**listing(key, 'office-'+key, 'broker-'+key), 'broker_email':'manager@example.invalid'}
        with patch('audit_app.job.send_email') as sender:
            result = backfill_broker_contacts(self.config, client)
            sender.assert_not_called()
        self.assertEqual(result, {'total':1,'refreshed':1,'skipped':0,'needs_attention':0})
        client.listing_by_key.assert_called_once_with('1')
        with connect(self.config.database_path) as db:
            current = dict(db.execute('SELECT * FROM audits WHERE id=?',(previous['id'],)).fetchone())
            self.assertEqual(current['intended_to'], '["manager@example.invalid"]')
            self.assertEqual(current['email_status'], previous['email_status'])
            self.assertEqual(current['selected_at'], previous['selected_at'])
            self.assertEqual(db.execute('SELECT count(*) FROM email_attempts').fetchone()[0], 0)
        self.assertEqual(backfill_broker_contacts(self.config, client)['total'], 0)

    def test_development_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            backfill_broker_contacts(replace(self.config, env='development'))
