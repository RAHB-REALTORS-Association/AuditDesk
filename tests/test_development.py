import os
import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from audit_app.application import create_app
from audit_app.bridge import BridgeClient, BridgeError
from audit_app.config import load_config, validate_web_config
from audit_app.database import connect
from audit_app.emailer import EmailError, _post_message, send_email, send_failure_email
from audit_app.job import deliver_audit, run_job
from audit_app.outcomes import deliver_failure_notice
from audit_app.runtime import start_runtime


class DevelopmentTests(unittest.TestCase):
    def test_all_staff_views_and_email_editor_asset_are_available(self):
        app = self.app()
        client = app.test_client()
        for tab in ('audits', 'listings', 'runs', 'simulation', 'admin',
                    'template', 'failure_template', 'report', 'brokerages', 'users', 'activity'):
            with self.subTest(tab=tab):
                response = client.get('/?tab=' + tab)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'DEVELOPMENT SANDBOX', response.data)
                if tab in ('template', 'failure_template'):
                    self.assertIn(b'/static/template-editor.js', response.data)
        asset = client.get('/static/template-editor.js')
        self.addCleanup(asset.close)
        self.assertEqual(asset.status_code, 200)
        self.assertIn(b'hiddenBody.value = editor.innerHTML', asset.data)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.live = Path(self.temp.name) / 'live.sqlite3'
        self.live.write_bytes(b'Existing live data must remain untouched')
        self.original = self.live.read_bytes()
        with patch.dict(os.environ, {'APP_ENV': 'development', 'BRIDGE_API_KEY': 'real-bridge-key',
                                     'SENDGRID_API_KEY': 'real-sendgrid-key', 'EMAIL_ENABLED': 'true',
                                     'SCHEDULER_ENABLED': 'true', 'DATABASE_PATH': str(self.live),
                                     'TEST_MODE_END_AT': 'ignored-even-if-invalid'}, clear=True):
            self.config = load_config()

    def app(self):
        # Config instances supplied directly must be just as safe as env loading.
        cfg = replace(self.config, database_path=str(self.live), bridge_key='entered-key',
                      bridge_base_url='https://bridge.example.test/dataset', sendgrid_key='entered-key',
                      email_enabled=True, scheduler_enabled=True)
        with patch('urllib.request.urlopen', side_effect=AssertionError('No network allowed')):
            app = create_app(cfg, verifier=Mock(side_effect=AssertionError('No identity provider')))
        self.addCleanup(app.extensions['auditdesk_sandbox'].cleanup)
        return app

    def test_keys_flags_and_live_database_are_ignored(self):
        self.assertEqual(self.config.auth_mode, 'open')
        self.assertFalse(self.config.email_enabled)
        self.assertFalse(self.config.scheduler_enabled)
        self.assertEqual(self.config.bridge_key, '')
        self.assertEqual(self.config.sendgrid_key, '')
        self.assertEqual(self.config.database_path, '')
        first, second = self.app(), self.app()
        a = first.extensions['auditdesk_config']
        b = second.extensions['auditdesk_config']
        self.assertNotEqual(a.database_path, b.database_path)
        self.assertFalse(a.email_enabled)
        self.assertFalse(a.scheduler_enabled)
        with connect(a.database_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM audits').fetchone()[0], 4)
            self.assertTrue(all(row[0].startswith('DEMO-') for row in db.execute('SELECT mls_number FROM listings')))
            db.execute("UPDATE app_users SET display_name='Changed locally'")
            db.commit()
        with connect(b.database_path) as db:
            self.assertEqual(db.execute("SELECT display_name FROM app_users WHERE role='reviewer'").fetchone()[0], 'Demo Reviewer')
        self.assertEqual(self.live.read_bytes(), self.original)

    def test_open_ui_still_protects_forms_and_records_synthetic_identity(self):
        app = self.app()
        client = app.test_client()
        response = client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('DEVELOPMENT SANDBOX', response.text)
        self.assertIn('DEMO-1', response.text)
        with client.get('/static/app.css') as asset:
            self.assertEqual(asset.status_code, 200)
        self.assertEqual(client.get('/?tab=users').status_code, 200)
        self.assertNotIn('Secure;', response.headers.get('Set-Cookie', ''))
        page = client.get('/?tab=users')
        data = {'token': re.search(r'name="token" value="([^"]+)"', page.text)[1],
                'revision': re.search(r'name="revision" value="(\d+)"', page.text)[1], 'email': 'preview@example.invalid', 'display_name': 'Preview Reviewer', 'role': 'reviewer', 'active': '1', 'version': '0'}
        self.assertEqual(client.post('/users', data=data, headers={'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(client.post('/users', data=data, headers={'Origin': self.config.public_url}).status_code, 303)
        with connect(app.extensions['auditdesk_config'].database_path) as db:
            self.assertEqual(db.execute("SELECT actor FROM activity_events WHERE action='access.updated'").fetchone()[0], 'development-sandbox')

    def test_live_boundaries_block_even_with_directly_supplied_keys(self):
        config = replace(self.config, email_enabled=True, scheduler_enabled=True,
                         bridge_key='entered', bridge_base_url='https://bridge.example.test/dataset', sendgrid_key='entered')
        with patch('urllib.request.urlopen') as network:
            with self.assertRaisesRegex(BridgeError, 'development'):
                BridgeClient(config)
            client = BridgeClient(replace(config, env='test'))
            client.config = config
            with self.assertRaisesRegex(BridgeError, 'development'):
                client._get('https://bridge.example.test/dataset/Property')
            for send in (lambda: send_email(config, {}, 1, [], [], []),
                         lambda: send_failure_email(config, {}, 1, 'issues'),
                         lambda: _post_message(config, 1, [], [], [], '', '', '', 'audit_request')):
                with self.assertRaisesRegex(EmailError, 'development'):
                    send()
            sender = Mock()
            self.assertFalse(deliver_audit(config, 1, sender=sender))
            self.assertFalse(deliver_failure_notice(config, 1, sender=sender))
            report = run_job(config, client=Mock(), sender=sender)
            self.assertIn('synthetic', report['mode'])
            sender.assert_not_called()
            network.assert_not_called()

    def test_scheduler_cannot_be_enabled_and_other_modes_cannot_be_open(self):
        config = self.app().extensions['auditdesk_config']
        with patch('audit_app.runtime.threading.Thread') as thread:
            lock, stop = start_runtime(replace(config, scheduler_enabled=True))
            stop.set()
            lock.release()
            thread.assert_not_called()
        for mode in ('test', 'production'):
            with self.assertRaises(ValueError):
                validate_web_config(replace(self.config, env=mode, auth_mode='open'))

    def test_generated_pr_domain_uses_cloudflare_https_origin(self):
        for generated in ('http://auditdesk-pr12.oncornerstone.app', 'auditdesk-pr12.oncornerstone.app'):
            with patch.dict(os.environ, {'APP_ENV': 'development', 'PUBLIC_BASE_URL': 'auto',
                                         'COOLIFY_URL': generated}, clear=True):
                config = load_config()
            self.assertEqual(config.public_url, 'https://auditdesk-pr12.oncornerstone.app')
            validate_web_config(config)
        with patch.dict(os.environ, {'APP_ENV': 'development', 'PUBLIC_BASE_URL': 'auto'}, clear=True):
            with self.assertRaises(ValueError):
                load_config()
