"""Mixed-board intake must never turn foreign or unidentified listings into audits."""
import random
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from audit_app.board_scope import backfill_listing_boards
from audit_app.bridge import BridgeClient
from audit_app.config import load_config
from audit_app.database import connect, init_db
from audit_app.job import run_job, choose_fairly
from test_app import NOW, listing


class BoardScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config=replace(load_config(),env='test',database_path=str(Path(self.temp.name)/'audit.sqlite3'),
                            bridge_base_url='https://bridge.example.invalid/dataset',bridge_key='fake-key',
                            rate=1,email_enabled=False,admin_email='admin@example.invalid',test_end_at=None)
        init_db(self.config.database_path)

    def test_mixed_board_intake_and_selection_reject_foreign_and_missing(self):
        ours=listing('ours')
        foreign={**listing('foreign'),'originating_system_name':'BRREA'}
        unknown={**listing('unknown'),'originating_system_name':None}
        client=Mock();client.active_new_listings.return_value=iter([foreign,unknown,ours])
        result=run_job(self.config,client=client,rng=random.Random(1),now=NOW)
        self.assertEqual((result['new'],result['selected']),(1,1))
        with connect(self.config.database_path) as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT mls_number,originating_system_name FROM listings')],[('ours','Cornerstone')])
        chosen=choose_fairly([foreign,unknown,ours],[],self.config,rng=random.Random(1))
        self.assertEqual([row[0]['mls_number'] for row in chosen],['ours'])

    def test_bridge_query_and_response_filter_before_enrichment(self):
        client=BridgeClient(self.config)
        fields=self.config.field_map['Property']
        def raw(board,key):
            return {fields['listing_id']:key,fields['mls_number']:key,fields['status']:'Active',
                    fields['entry_timestamp']:listing(key)['entry_timestamp'],fields['originating_system_name']:board,fields['agent_mls_id']:'MEMBER'}
        client.inspect_metadata=Mock()
        client._collection=Mock(return_value=iter([raw('BRREA','foreign'),raw(None,'unknown'),raw('Cornerstone','ours')]))
        client._one=Mock(return_value={})
        rows=list(client.active_new_listings(NOW.replace(hour=0),NOW))
        self.assertEqual([row['listing_id'] for row in rows],['ours'])
        params=client._collection.call_args[0][1]
        self.assertIn("OriginatingSystemName eq 'Cornerstone'",params['$filter'])
        self.assertIn('OriginatingSystemName',params['$select'])
        self.assertEqual(client._one.call_count,3)

    def test_backfill_classifies_old_records_without_selection_or_mail(self):
        with connect(self.config.database_path) as db:
            for key in ('ours','foreign','missing'):
                db.execute("""INSERT INTO listings(bridge_listing_id,mls_number,status,entry_timestamp,address,
                    first_processed_at,processing_status) VALUES(?,?,'Active','2026','Example','2026','processed_not_selected')""",(key,key))
            db.commit()
        client=Mock();client._collection.return_value=iter([{'ListingKey':'ours','OriginatingSystemName':'Cornerstone','ListAgentMlsId':'MEMBER'},
                                                           {'ListingKey':'foreign','OriginatingSystemName':'BRREA','ListAgentMlsId':'MEMBER'}])
        with patch('audit_app.job.send_email') as sender:
            result=backfill_listing_boards(self.config,client)
            sender.assert_not_called()
        self.assertEqual(result,{'checked':3,'cornerstone':1,'other_boards':1,'unverified':1,'nonmembers':0})
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM audits').fetchone()[0],0)
            self.assertEqual(db.execute("SELECT originating_system_name FROM listings WHERE bridge_listing_id='missing'").fetchone()[0],None)
            self.assertEqual(db.execute("SELECT originating_system_name FROM listings WHERE bridge_listing_id='foreign'").fetchone()[0],'BRREA')
        with self.assertRaises(ValueError):backfill_listing_boards(replace(self.config,env='development'),client)

    def test_nonmembers_and_unknown_identifiers_never_enter_selection(self):
        ours=listing('member')
        interboard={**listing('interboard'),'agent_mls_id':' nonmem '}
        unknown={**listing('unknown'),'agent_mls_id':None}
        # Member listings with missing contacts still enter the usual contact-error workflow.
        ours['broker_email']=None
        client=Mock();client.active_new_listings.return_value=iter([interboard,unknown,ours])
        result=run_job(self.config,client=client,rng=random.Random(1),now=NOW)
        self.assertEqual((result['new'],result['selected']),(1,1))
        self.assertEqual([x[0]['mls_number'] for x in choose_fairly([interboard,unknown,ours],[],self.config,random.Random(1))],['member'])
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT agent_mls_id FROM listings').fetchone()[0],'DEMO-MEMBER')
            self.assertEqual(db.execute('SELECT email_status FROM audits').fetchone()[0],'email_failed')

    def test_bridge_nonmember_response_rejected_before_contact_lookups(self):
        client=BridgeClient(self.config);client.inspect_metadata=Mock()
        client._collection=Mock(return_value=iter([
            {'ListingKey':'interboard','OriginatingSystemName':'Cornerstone','ListAgentMlsId':'NONMEM'},
            {'ListingKey':'unknown','OriginatingSystemName':'Cornerstone','ListAgentMlsId':None}]))
        client._one=Mock()
        self.assertEqual(list(client.active_new_listings(NOW.replace(hour=0),NOW)),[])
        client._one.assert_not_called()
        self.assertIn("ListAgentMlsId ne 'NONMEM'",client._collection.call_args[0][1]['$filter'])

    def test_schema6_upgrade_and_backfill_identifies_existing_nonmember(self):
        with connect(self.config.database_path) as db:
            db.execute('ALTER TABLE listings DROP COLUMN agent_mls_id')
            db.execute("INSERT INTO listings(bridge_listing_id,mls_number,status,entry_timestamp,address,first_processed_at,processing_status,originating_system_name) VALUES('old','old','Active','2026','Example','2026','email_failed','Cornerstone')")
            db.execute('PRAGMA user_version=6');db.commit()
        init_db(self.config.database_path)
        with connect(self.config.database_path) as db:
            self.assertIsNone(db.execute('SELECT agent_mls_id FROM listings').fetchone()[0])
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],7)
        client=Mock();client._collection.return_value=iter([{'ListingKey':'old','OriginatingSystemName':'Cornerstone','ListAgentMlsId':'NONMEM'}])
        with patch('audit_app.job.send_email') as sender:
            result=backfill_listing_boards(self.config,client)
            sender.assert_not_called()
        self.assertEqual(result,{'checked':1,'cornerstone':0,'other_boards':0,'unverified':0,'nonmembers':1})
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT agent_mls_id FROM listings').fetchone()[0],'NONMEM')
            self.assertEqual(db.execute('SELECT processing_status FROM listings').fetchone()[0],'email_failed')

    def test_schema5_upgrade_leaves_historical_board_unverified(self):
        with connect(self.config.database_path) as db:
            db.execute('ALTER TABLE listings DROP COLUMN originating_system_name')
            db.execute("""INSERT INTO listings(bridge_listing_id,mls_number,status,entry_timestamp,address,
                first_processed_at,processing_status) VALUES('old','old','Active','2026','Example','2026','processed_not_selected')""")
            db.execute('PRAGMA user_version=5');db.commit()
        init_db(self.config.database_path)
        with connect(self.config.database_path) as db:
            self.assertIsNone(db.execute('SELECT originating_system_name FROM listings').fetchone()[0])
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],7)
