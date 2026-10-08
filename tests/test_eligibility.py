import random
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from audit_app.bridge import BridgeClient, BridgeError
from audit_app.config import load_config
from audit_app.database import connect, init_db
from audit_app.eligibility import eligibility_reason, membership_code, read_rules, rules_from_form, save_rules
from audit_app.eligibility_backfill import backfill_membership_classes
from audit_app.job import deliver_audit, run_job
from audit_app.refresh import refresh_audits
from test_app import FakeClient, NOW, listing


class EligibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = replace(load_config(), env='test', rate=1, email_enabled=False,
            database_path=str(Path(self.temp.name)/'audit.sqlite3'),
            bridge_base_url='https://bridge.example.invalid/dataset', bridge_key='fake',
            admin_email='admin@example.invalid')
        init_db(self.config.database_path)

    def test_super_subscribers_are_excluded_even_with_a_member_mls_id(self):
        subscriber = {**listing('subscriber'), 'agent_membership_class': ' nl7 '}
        result = run_job(self.config, FakeClient([subscriber, listing('member')]), random.Random(1), NOW)
        self.assertEqual((result['new'], result['selected']), (1, 1))
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM audits').fetchone()[0], 1)
        self.assertIn('NL7', eligibility_reason(subscriber))

    def test_descriptive_membership_labels_block_existing_and_new_requests(self):
        for value in (' nl7 - Authorized User Subscriber with Input ', 'NL7 – Description', 'NL7 — Description'):
            self.assertEqual(membership_code(value), 'NL7')
            self.assertIn('NL7', eligibility_reason({**listing('blocked'), 'agent_membership_class':value}))
        self.assertEqual(membership_code('NL70 - Other class'), 'NL70')
        self.assertEqual(membership_code('SP1 - Salesperson Full Input'), 'SP1')
        run_job(self.config, FakeClient([listing('1')]), random.Random(1), NOW)
        with connect(self.config.database_path) as db:
            db.execute("UPDATE listings SET agent_membership_class='NL7 - Authorized User Subscriber with Input'")
            db.commit()
            self.assertEqual(db.execute('SELECT count(*) FROM listings WHERE listing_allowed(originating_system_name,agent_mls_id,agent_membership_class)').fetchone()[0], 0)
        sender = Mock()
        self.assertFalse(deliver_audit(replace(self.config, email_enabled=True), 1, retry=True, sender=sender, now=NOW))
        sender.assert_not_called()

    def test_unknown_intake_fails_without_processing_or_selecting_any_listing(self):
        unknown = {**listing('unknown'), 'agent_membership_class': None}
        with self.assertRaisesRegex(BridgeError, 'membership class'):
            run_job(self.config, FakeClient([listing('member'), unknown]), random.Random(1), NOW)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM listings').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT status FROM runs').fetchone()[0], 'failed')

    def test_save_validation_normalization_history_and_restart(self):
        saved = save_rules(self.config, {'membership_classes': 'nl7, NL7\nTEST', 'agent_ids': 'abc-1'})
        self.assertEqual(saved.membership_classes, ('NL7', 'TEST'))
        self.assertEqual(saved.agent_ids, ('ABC-1',))
        with connect(self.config.database_path) as db:
            self.assertEqual(read_rules(db), saved)
            self.assertEqual(db.execute('SELECT action FROM activity_events').fetchone()[0], 'settings.eligibility_updated')
        for raw in ("NL7' or 1=1", 'x'*41, ','.join(str(i) for i in range(51))):
            with self.assertRaises(ValueError):
                save_rules(self.config, {'membership_classes': raw})
        with connect(self.config.database_path) as db:
            self.assertEqual(read_rules(db), saved)
        # Clearing optional exclusions never admits a different board or NONMEM.
        rules = rules_from_form({})
        self.assertIsNotNone(eligibility_reason({**listing('x'), 'agent_mls_id':'NONMEM'}, rules))
        self.assertIsNotNone(eligibility_reason({**listing('x'), 'originating_system_name':'BRREA'}, rules))

    def test_saved_rules_block_pending_and_retry_before_any_email_attempt(self):
        run_job(self.config, FakeClient([listing('1')]), random.Random(1), NOW)
        save_rules(self.config, {'membership_classes': 'MEMBER', 'agent_ids': ''})
        sender = Mock()
        live = replace(self.config, email_enabled=True)
        self.assertFalse(deliver_audit(live, 1, sender=sender, now=NOW))
        self.assertFalse(deliver_audit(live, 1, retry=True, sender=sender, now=NOW))
        sender.assert_not_called()
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM email_attempts').fetchone()[0], 0)
            self.assertIn('MEMBER', db.execute('SELECT last_error FROM audits').fetchone()[0])
            self.assertEqual(db.execute('SELECT count(*) FROM listings WHERE listing_allowed(originating_system_name,agent_mls_id,agent_membership_class)').fetchone()[0], 0)

    def test_historical_unknown_class_stays_visible_but_cannot_send(self):
        run_job(self.config, FakeClient([listing('1')]), random.Random(1), NOW)
        with connect(self.config.database_path) as db:
            db.execute('UPDATE listings SET agent_membership_class=NULL')
            db.commit()
        sender = Mock()
        self.assertFalse(deliver_audit(replace(self.config, email_enabled=True), 1, sender=sender, now=NOW))
        sender.assert_not_called()
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM listings WHERE listing_allowed(originating_system_name,agent_mls_id,agent_membership_class)').fetchone()[0], 1)
            self.assertIn('unverified', db.execute('SELECT last_error FROM audits').fetchone()[0])

    def test_bridge_exclusion_precedes_office_and_broker_lookups(self):
        client = BridgeClient(self.config)
        client._one = Mock(return_value={'MemberMlsSecurityClass':'NL7'})
        row = {**listing('1'), 'office_id':'office', 'office_name':'Example office'}
        current = client._enrich_listing(row)
        self.assertEqual(current['agent_membership_class'], 'NL7')
        self.assertIsNone(current['broker_email'])
        self.assertEqual(client._one.call_count, 1)
        self.assertEqual(client._one.call_args.args[0], 'Member')

    def test_bridge_query_uses_saved_agent_exclusions_and_checks_returned_rows(self):
        rules = save_rules(self.config, {'membership_classes':'NL7', 'agent_ids':'blocked-agent'})
        client = BridgeClient(self.config, rules=rules)
        client.inspect_metadata = Mock()
        client._collection = Mock(return_value=iter([{
            'ListingKey':'blocked', 'OriginatingSystemName':'Cornerstone', 'ListAgentMlsId':' BLOCKED-agent '}]))
        client._one = Mock()
        self.assertEqual(list(client.active_new_listings(NOW.replace(hour=0), NOW)), [])
        self.assertIn("ListAgentMlsId ne 'BLOCKED-AGENT'", client._collection.call_args.args[1]['$filter'])
        client._one.assert_not_called()

    def test_custom_field_map_must_supply_membership_class(self):
        mapping = {resource: dict(fields) for resource, fields in self.config.field_map.items()}
        del mapping['Member']['membership_class']
        client = BridgeClient(replace(self.config, field_map=mapping))
        client._get = Mock()
        with self.assertRaisesRegex(BridgeError, 'must configure membership_class'):
            client.inspect_metadata()
        client._get.assert_not_called()

    def test_refresh_applies_rules_and_rejects_concurrent_rule_change(self):
        run_job(self.config, FakeClient([listing('1')]), random.Random(1), NOW)
        client = Mock()
        client.listing_by_key.return_value = {**listing('1'), 'agent_membership_class':'NL7'}
        self.assertEqual(refresh_audits(self.config, [1], client)['needs_attention'], 1)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT agent_membership_class FROM listings').fetchone()[0], 'NL7')
        def changed(key):
            save_rules(self.config, {'membership_classes':'MEMBER'})
            return listing(key)
        client.listing_by_key.side_effect = changed
        with self.assertRaisesRegex(ValueError, 'settings changed'):
            refresh_audits(self.config, [1], client)

    def test_schema8_upgrade_preserves_audit_and_requires_membership_verification(self):
        run_job(self.config, FakeClient([listing('1')]), random.Random(1), NOW)
        with connect(self.config.database_path) as db:
            original = dict(db.execute('SELECT * FROM audits').fetchone())
            db.execute('ALTER TABLE listings DROP COLUMN agent_membership_class')
            db.execute('DROP TABLE listing_eligibility')
            db.execute('PRAGMA user_version=8')
            db.commit()
        init_db(self.config.database_path)
        with connect(self.config.database_path) as db:
            self.assertEqual(dict(db.execute('SELECT * FROM audits').fetchone()), original)
            self.assertIsNone(db.execute('SELECT agent_membership_class FROM listings').fetchone()[0])
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 9)
        client = Mock(); client._one.return_value = {'MemberMlsSecurityClass':'NL7'}
        with patch('audit_app.job.send_email') as sender:
            self.assertEqual(backfill_membership_classes(self.config, client), {'checked':1,'verified':1,'unverified':0})
            sender.assert_not_called()
        with connect(self.config.database_path) as db:
            self.assertEqual(dict(db.execute('SELECT * FROM audits').fetchone()), original)
        with self.assertRaises(ValueError):
            backfill_membership_classes(replace(self.config, env='development'), client)

    def test_interrupted_membership_verification_resumes_committed_records(self):
        run_job(self.config, FakeClient([listing('1'), listing('2')]), random.Random(1), NOW)
        with connect(self.config.database_path) as db:
            db.execute('UPDATE listings SET agent_membership_class=NULL')
            db.commit()
        client = Mock()
        client._one.side_effect = [{'MemberMlsSecurityClass':'NL7'}, KeyboardInterrupt()]
        with self.assertRaises(KeyboardInterrupt):
            backfill_membership_classes(self.config, client)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM listings WHERE agent_membership_class IS NULL').fetchone()[0], 1)
        client._one.side_effect = None
        client._one.return_value = {'MemberMlsSecurityClass':'MEMBER'}
        self.assertEqual(backfill_membership_classes(self.config, client),
                         {'checked':1,'verified':1,'unverified':0})
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM activity_events WHERE action='listing.membership_verified'").fetchone()[0], 2)
