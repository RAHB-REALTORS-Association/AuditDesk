import random
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from audit_app.bridge import BridgeClient, BridgeError
from audit_app.config import load_config
from audit_app.database import connect
from audit_app.job import run_job, deliver_audit
from audit_app.refresh import refresh_audits
from test_app import listing, FakeClient, NOW


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.config = replace(load_config(), env='test', database_path=str(Path(self.temp.name)/'audit.sqlite3'),
                              bridge_base_url='https://bridge.example.invalid/dataset',bridge_key='fake',
                              email_enabled=True,admin_email='admin@example.invalid',rate=1)
        old = {**listing('1'),'broker_email':None}
        run_job(self.config,FakeClient([old]),random.Random(1),NOW)
        self.client=Mock();self.client.listing_by_key.return_value={**listing('1'),'broker_email':'corrected@example.invalid'}

    def test_refresh_rebuilds_recipients_without_sending_then_explicit_retry_works(self):
        with connect(self.config.database_path) as db:
            old=dict(db.execute('SELECT * FROM audits').fetchone())
        with patch('audit_app.job.send_email') as sender:
            result=refresh_audits(self.config,[1],self.client);sender.assert_not_called()
        self.assertEqual(result,{'refreshed':1,'skipped':0,'needs_attention':0})
        with connect(self.config.database_path) as db:
            audit=dict(db.execute('SELECT * FROM audits').fetchone())
            self.assertEqual(audit['email_status'],'email_failed')
            self.assertEqual(audit['intended_to'],'["corrected@example.invalid"]')
            self.assertEqual(audit['actual_recipients'],'["admin@example.invalid"]')
            for field in ('id','selected_at','selection_metadata','listing_id','test_mode'):
                self.assertEqual(audit[field],old[field])
            self.assertEqual(db.execute('SELECT count(*) FROM email_attempts').fetchone()[0],0)
        sender=Mock(return_value='mock-message')
        self.assertTrue(deliver_audit(self.config,1,retry=True,sender=sender,now=NOW))
        self.assertEqual(sender.call_args.args[3],['corrected@example.invalid'])

    def test_refresh_skips_sent_unknown_and_completed_and_blocks_development(self):
        for status in ('email_sent','email_unknown','email_sending'):
            with connect(self.config.database_path) as db:
                db.execute('UPDATE audits SET email_status=?',(status,));db.commit()
            self.assertEqual(refresh_audits(self.config,[1],self.client)['skipped'],1)
        self.client.listing_by_key.assert_not_called()
        with self.assertRaises(ValueError):refresh_audits(replace(self.config,env='development'),[1],self.client)

    def test_bridge_failure_and_concurrent_change_leave_listing_untouched(self):
        self.client.listing_by_key.side_effect=BridgeError('Bridge unavailable')
        with self.assertRaises(ValueError):refresh_audits(self.config,[1],self.client)
        def changed(key):
            with connect(self.config.database_path) as db:
                db.execute("UPDATE audits SET email_status='email_unknown'");db.commit()
            return listing(key)
        self.client.listing_by_key.side_effect=changed
        with self.assertRaises(ValueError):refresh_audits(self.config,[1],self.client)
        with connect(self.config.database_path) as db:
            self.assertIsNone(db.execute('SELECT broker_email FROM listings').fetchone()[0])

    def test_refresh_updates_provenance_and_blocks_now_nonmember(self):
        self.client.listing_by_key.return_value={**listing('1'),'agent_mls_id':'NONMEM'}
        self.assertEqual(refresh_audits(self.config,[1],self.client)['needs_attention'],1)
        sender=Mock()
        self.assertFalse(deliver_audit(self.config,1,retry=True,sender=sender,now=NOW));sender.assert_not_called()

    def test_targeted_bridge_read_ignores_intake_window_and_refreshes_contacts(self):
        client=BridgeClient(self.config)
        client._collection=Mock(return_value=iter([{'ListingKey':'old','ListingId':'old','StandardStatus':'Inactive',
            'OriginalEntryTimestamp':'2020-01-01T00:00:00Z','OriginatingSystemName':'Cornerstone','ListAgentMlsId':'MEMBER','ListOfficeKey':'office'}]))
        client._one=Mock(side_effect=[{'MemberMlsSecurityClass':'MEMBER'},
                                     {'OfficeBrokerKey':'broker','OfficeEmail':'office@example.invalid'},
                                     {'MemberEmail':'corrected@example.invalid','MemberStatus':'Active'}])
        current=client.listing_by_key('old')
        self.assertEqual(current['broker_email'],'corrected@example.invalid')
        self.assertNotIn('OriginalEntryTimestamp',client._collection.call_args.args[1]['$filter'])
        self.assertEqual(current['status'],'Inactive')
