import re
import sqlite3
import subprocess
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from audit_app.application import create_app
from audit_app.backup import backup, restore, validate
from audit_app.config import load_config, validate_web_config
from audit_app.database import connect, init_db
from audit_app.emailer import EmailError
from audit_app.job import deliver_audit, run_job
from audit_app.security import AccessVerifier
from test_app import FakeClient, NOW, listing


class FoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = replace(load_config(), env="test", database_path=str(Path(self.temp.name)/'audit.sqlite3'),
                              auth_mode='cloudflare', access_issuer='https://example.cloudflareaccess.com',
                              access_audience='test-audience', public_url='https://audit.example.com',
                              secret_key='test-secret-'*5, bootstrap_admins=('admin@example.com',),
                              admin_email='admin@example.com', rate=1, email_enabled=True)
        self.verifier = AccessVerifier(self.config)
        self.verifier.keys.get_signing_key_from_jwt = Mock(return_value=SimpleNamespace(key=self.private.public_key()))
        self.app = create_app(self.config, self.verifier)
        self.client = self.app.test_client()
        with connect(self.config.database_path) as db:
            for role in ('reviewer','manager'):
                db.execute('INSERT INTO app_users(email,subject,role) VALUES(?,?,?)', (role+'@example.com',role,role))
            db.commit()

    def token(self, role='admin', **overrides):
        claims = dict(sub=role, email=role+'@example.com', iss=self.config.access_issuer,
                      aud=self.config.access_audience, iat=int(time.time())-2, exp=int(time.time())+600)
        claims.update(overrides)
        return jwt.encode(claims, self.private, algorithm='RS256')

    def get(self, path='/', role='admin', client=None):
        return (client or self.client).get(path, base_url=self.config.public_url,
                                         headers={'Cf-Access-Jwt-Assertion':self.token(role)})

    def post(self, path, form, role='admin', page='/', client=None, **headers):
        client=client or self.client
        response=self.get(page, role, client)
        csrf=re.search(r'name="token" value="([^"]+)"', response.text)
        revision=re.search(r'name="revision" value="(\d+)"', response.text)
        # Empty audit queues have no forms; obtain session and revision directly for test setup.
        with client.session_transaction(base_url=self.config.public_url) as sess:
            token=sess['csrf']
        with connect(self.config.database_path) as db:
            current=db.execute('SELECT COALESCE(max(id),0) FROM activity_events').fetchone()[0]
        data={'token':csrf[1] if csrf else token,'revision':revision[1] if revision else str(current),**form}
        return client.post(path, data=data, base_url=self.config.public_url,
                           headers={'Cf-Access-Jwt-Assertion':self.token(role), 'Origin':self.config.public_url, **headers})

    def test_authentication_fails_closed_and_health_is_public(self):
        self.assertEqual(self.client.get('/healthz').status_code,200)
        for path in ('/', '/static/app.css', '/reports/brokerages.pdf'):
            self.assertEqual(self.client.get(path).status_code,401)
        for overrides in ({'aud':'wrong'}, {'iss':'https://wrong.cloudflareaccess.com'}, {'exp':1}, {'sub':17}, {'email':None}):
            r=self.client.get('/',base_url=self.config.public_url,headers={'Cf-Access-Jwt-Assertion':self.token(**overrides)})
            self.assertEqual(r.status_code,401,r.text)
        badkey=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        forged=jwt.encode(dict(sub='admin',email='admin@example.com',iss=self.config.access_issuer,aud=self.config.access_audience,iat=int(time.time()),exp=int(time.time())+30),badkey,algorithm='RS256')
        self.assertEqual(self.client.get('/',headers={'Cf-Access-Jwt-Assertion':forged}).status_code,401)
        self.assertEqual(self.get(role='unknown').status_code,403)

    def test_pdf_download_paginates_large_groups_for_all_periods(self):
        stamp = datetime.now(timezone.utc).isoformat()
        with connect(self.config.database_path) as db:
            for number in range(50):
                db.execute("""INSERT INTO listings(bridge_listing_id,mls_number,status,entry_timestamp,
                    address,brokerage_id,brokerage_name,brokerage_address,first_processed_at,processing_status)
                    VALUES(?,?,'Active',?,'Example',?,'Example Realty',?,?,'processed_not_selected')""",
                    (str(number),str(number),stamp,f"office-{number}",f"{number} Example Street, Hamilton ON",stamp))
            db.commit()
        for period in ('3m', '6m', '1y'):
            response = self.get('/reports/brokerages.pdf?period='+period, role='reviewer')
            self.assertEqual(response.status_code,200,response.data[:200])
            self.assertEqual(response.mimetype,'application/pdf')
            self.assertEqual(response.headers['Content-Disposition'],
                             f'attachment; filename="auditdesk-brokerages-{period}.pdf"')
            self.assertTrue(response.data.startswith(b'%PDF-'))
            self.assertIn(b'%%EOF',response.data[-20:])
        self.assertEqual(self.get('/reports/brokerages.pdf?period=2m').status_code,400)

    def test_role_boundaries_cover_routes_and_navigation(self):
        r=self.get(role='reviewer')
        self.assertEqual(r.status_code,200)
        self.assertNotIn('Access management',r.text)
        self.assertNotIn('Selection settings',r.text)
        for page in ('users','activity','admin','template'):
            self.assertEqual(self.get('/?tab='+page,role='reviewer').status_code,403)
        for path,form in (('/users',{}),('/admin/selection-rate',{'rate_percent':'100'}),('/admin/brokerage-cooldown',{'cooldown_days':'7'}),('/assignment/1',{}),('/assignments',{'audit_ids':['1'],'assignee_user_id':'2'}),('/template',{})):
            self.assertEqual(self.post(path,form,role='reviewer').status_code,403)
        self.assertEqual(self.get('/?tab=users',role='manager').status_code,403)
        self.assertEqual(self.get('/?tab=template',role='manager').status_code,200)
        self.assertEqual(self.get('/activity.csv',role='manager').status_code,403)

    def test_bulk_assignment_validates_batch_and_records_each_actor(self):
        with connect(self.config.database_path) as db:
            for number in (1, 2, 3):
                db.execute("""INSERT INTO listings(id,bridge_listing_id,mls_number,status,entry_timestamp,
                    address,first_processed_at,processing_status)
                    VALUES(?,?,?,'Active','2026','Example','2026','selected_for_audit')""", (number,str(number),str(number)))
                db.execute("""INSERT INTO audits(id,listing_id,selected_at,intended_to,intended_cc,
                    actual_recipients,test_mode,email_status,selection_metadata)
                    VALUES(?,?,'2026','[]','[]','[]',1,'email_sent','{}')""", (number,number))
            db.commit()
        data={'audit_ids':['1','2'], 'assignee_user_id':'2'}
        self.assertEqual(self.post('/assignments', data, role='manager').status_code,303)
        with connect(self.config.database_path) as db:
            self.assertEqual([row[0] for row in db.execute('SELECT assignee_user_id FROM audits ORDER BY id')], [2,2,None])
            self.assertEqual([row[0] for row in db.execute("SELECT actor FROM activity_events WHERE action='audit.assigned'")], ['manager','manager'])
        for ids in ([], ['1','999'], ['1','1'], ['invalid'], ['1']*201):
            response=self.post('/assignments', {'audit_ids':ids, 'assignee_user_id':'3'})
            self.assertEqual(response.status_code,400,response.text)
        self.assertEqual(self.post('/assignments',data, Origin='https://evil.example').status_code,403)
        self.assertEqual(self.post('/assignments',{**data, 'assignee_user_id':['2','3']}).status_code,400)
        self.assertEqual(self.post('/assignment/1', {'reviewer_id':'1'}).status_code,400)
        with connect(self.config.database_path) as db:
            self.assertEqual([row[0] for row in db.execute('SELECT assignee_user_id FROM audits ORDER BY id')], [2,2,None])
            db.execute("UPDATE app_users SET active=0 WHERE id=2")
            db.commit()
        self.assertEqual(self.post('/assignments',data).status_code,400)
        self.assertEqual(self.post('/assignments',{**data,'assignee_user_id':'unassigned'}).status_code,303)
        with connect(self.config.database_path) as db:
            self.assertEqual([row[0] for row in db.execute('SELECT assignee_user_id FROM audits')], [None,None,None])

    def test_stale_bulk_assignment_changes_nothing(self):
        run_job(self.config, FakeClient([listing('1')]),now=NOW,sender=lambda *args:'fake')
        page=self.get()
        token=re.search(r'name="token" value="([^"]+)"',page.text)[1]
        revision=re.search(r'name="revision" value="(\d+)"',page.text)[1]
        self.assertEqual(self.post('/assignment/1',{'assignee_user_id':'2'}).status_code,303)
        response=self.client.post('/assignments',base_url=self.config.public_url,
            headers={'Cf-Access-Jwt-Assertion':self.token(), 'Origin':self.config.public_url},
            data={'token':token,'revision':revision,'audit_ids':['1'],'assignee_user_id':'3'})
        self.assertEqual(response.status_code,409,response.text)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT assignee_user_id FROM audits').fetchone()[0],2)

    def test_simulation_is_not_a_staff_page(self):
        for role in ('reviewer', 'manager', 'admin'):
            self.assertNotIn('Simulation', self.get(role=role).text)
            self.assertEqual(self.get('/?tab=simulation', role=role).status_code, 404)

    def test_removed_roster_routes_are_not_available(self):
        for role in ('reviewer', 'manager', 'admin'):
            self.assertEqual(self.get('/?tab=reviewers', role=role).status_code, 404)
            self.assertEqual(self.post('/reviewers', {'name': 'Unused'}, role=role).status_code, 404)
            self.assertEqual(self.post('/reviewers/1', {'action': 'delete'}, role=role).status_code, 404)

    def test_csrf_origin_input_limits_and_role_changes(self):
        self.assertEqual(self.post('/admin/selection-rate',{'rate_percent':'7'},Origin='https://evil.example').status_code,403)
        self.assertEqual(self.post('/admin/selection-rate',{'rate_percent':'7','token':'forged'}).status_code,403)
        self.assertEqual(self.post('/admin/selection-rate',{'rate_percent':'7','revision':'not-an-int'}).status_code,400)
        self.assertEqual(self.post('/admin/selection-rate',{'rate_percent':'x'*40000}).status_code,413)
        response=self.post('/admin/selection-rate',{'rate_percent':'7'})
        self.assertEqual(response.status_code,303,response.text)
        response=self.post('/admin/brokerage-cooldown',{'cooldown_days':'21'},role='manager',page='/?tab=admin')
        self.assertEqual(response.status_code,303,response.text)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT days FROM brokerage_cooldown_settings').fetchone()[0],21)
            self.assertEqual(db.execute("SELECT actor FROM activity_events WHERE action='selection.brokerage_cooldown_updated'").fetchone()[0],'manager')
        with connect(self.config.database_path) as db:
            row=db.execute("SELECT * FROM activity_events WHERE action='selection.updated'").fetchone()
            self.assertEqual(row['actor'],'admin')
            db.execute("UPDATE app_users SET active=0 WHERE subject='reviewer'")
            db.commit()
        self.assertEqual(self.get(role='reviewer').status_code,403)

    def test_stale_form_is_rejected_atomically(self):
        r=self.get('/?tab=admin')
        token=re.search(r'name="token" value="([^"]+)"',r.text)[1]
        rev=re.search(r'name="revision" value="(\d+)"',r.text)[1]
        self.assertEqual(self.post('/admin/selection-rate',{'rate_percent':'7'}).status_code,303)
        stale=self.client.post('/admin/selection-rate',base_url=self.config.public_url,
                               headers={'Cf-Access-Jwt-Assertion':self.token(),'Origin':self.config.public_url},
                               data={'token':token,'revision':rev,'rate_percent':'99'})
        self.assertEqual(stale.status_code,409,stale.text)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT rate_percent FROM selection_settings').fetchone()[0],'7')

    def test_bootstrap_protection_and_access_management(self):
        response=self.post('/users',{'email':'new@example.com','display_name':'New Person','role':'reviewer','active':'1','version':'0'},page='/?tab=users')
        self.assertEqual(response.status_code,303,response.text)
        self.assertEqual(self.get(role='new').status_code,200)
        response=self.post('/users',{'email':'admin@example.com','role':'reviewer','version':'1','active':'1'},page='/?tab=users')
        self.assertEqual(response.status_code,400)
        self.assertIn('Bootstrap',response.text)
        # An existing subject cannot claim another invited email; nor can a second subject claim a bound email.
        response=self.client.get('/',base_url=self.config.public_url,headers={'Cf-Access-Jwt-Assertion':self.token('other',email='admin@example.com')})
        self.assertEqual(response.status_code,403)

    def test_template_preview_preserves_input_and_assignment_result_routes(self):
        response=self.post('/template',{'subject':'Bad','body':'Custom unsaved wording','body_format':'plain','action':'preview'},page='/?tab=template')
        self.assertEqual(response.status_code,400,response.text)
        self.assertIn('Custom unsaved wording',response.text)
        run_job(self.config, FakeClient([listing('1')]),now=NOW,sender=lambda *args:'fake')
        self.assertEqual(self.post('/assignment/1',{'assignee_user_id':'2'},role='manager').status_code,303)
        response=self.post('/outcome/1',{'outcome':'passed','action':'record'},role='reviewer')
        self.assertEqual(response.status_code,303,response.text)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute("SELECT actor FROM activity_events WHERE action='audit.result'").fetchone()[0],'reviewer')

    def test_test_request_cannot_be_retried_in_production(self):
        def fail(*args): raise EmailError('not accepted')
        run_job(self.config,FakeClient([listing('1')]),now=NOW,sender=fail)
        sender=Mock()
        self.assertFalse(deliver_audit(replace(self.config,env='production'),1,retry=True,sender=sender))
        sender.assert_not_called()
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT email_status FROM audits').fetchone()[0],'email_blocked')

    def test_email_disable_blocks_sender_and_failed_run_can_retry(self):
        sender=Mock()
        run_job(replace(self.config,email_enabled=False),FakeClient([listing('1')]),now=NOW,sender=sender)
        sender.assert_not_called()
        # Separate DB so the completed run above does not suppress today's retry.
        cfg=replace(self.config,database_path=str(Path(self.temp.name)/'retry.sqlite3'))
        bad=Mock();bad.active_new_listings.side_effect=RuntimeError('unavailable')
        with self.assertRaises(RuntimeError):run_job(cfg,bad,now=NOW,only_if_needed=True,sender=sender)
        result=run_job(cfg,FakeClient([]),now=NOW+timedelta(minutes=5),only_if_needed=True,sender=sender)
        self.assertNotIn('skipped',result)

    def test_intake_catches_up_from_last_success(self):
        run_job(self.config,FakeClient([]),now=NOW,sender=Mock())
        client=Mock();client.active_new_listings.return_value=[]
        run_job(self.config,client,now=NOW+timedelta(days=3),sender=Mock())
        self.assertEqual(client.active_new_listings.call_args.args[0],NOW)

    def test_backup_restore_integrity_and_refuse_live_restore(self):
        file=Path(self.temp.name)/'backup.sqlite3'
        backup(self.config,file)
        self.assertEqual(validate(file),3)
        with self.assertRaises(FileExistsError):backup(self.config,file)
        with self.assertRaises(ValueError):restore(self.config,file,'wrong')
        from audit_app.runtime import start_runtime
        lock,stop=start_runtime(self.config)
        try:
            with self.assertRaises(ValueError):restore(self.config,file,'RESTORE STOPPED AUDITDESK')
        finally:
            stop.set();lock.release()
        self.assertEqual(Path(restore(self.config,file,'RESTORE STOPPED AUDITDESK')),Path(self.config.database_path).resolve())
        self.assertTrue(list(Path(self.temp.name).glob('*.before-restore-*')))
        bad=Path(self.temp.name)/'bad.sqlite3';bad.write_bytes(b'not a database')
        with self.assertRaises(sqlite3.DatabaseError):validate(bad)

    def test_migrate_original_schema_and_preserve_data(self):
        # Exact baseline schema, not a synthetic facsimile of the current migration.
        source=subprocess.check_output(['git','show','ac52c93:audit_app/database.py'],text=True)
        namespace={};exec(source,namespace)
        old=str(Path(self.temp.name)/'old.sqlite3')
        namespace['init_db'](old)
        with connect(old) as db:
            db.execute("INSERT INTO audit_reviewers(name,created_at,updated_at) VALUES('Existing','2026','2026')")
            db.commit()
        init_db(old);init_db(old)
        with connect(old) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],3)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='audit_reviewers'").fetchone())
            db.execute('PRAGMA user_version=99')
        with self.assertRaises(ValueError):init_db(old)

    def test_session_secret_is_generated_and_persisted(self):
        cfg=replace(self.config,secret_key='')
        app=create_app(cfg,self.verifier)
        key=Path(cfg.database_path).parent/'session.key'
        self.assertGreaterEqual(len(key.read_text()),32)
        self.assertEqual(create_app(cfg,self.verifier).secret_key,app.secret_key)
        self.assertEqual(key.stat().st_mode & 0o777,0o600)

    def test_concurrent_unrelated_change_does_not_interrupt_started_send(self):
        def fail(*args): raise EmailError('rejected')
        run_job(self.config,FakeClient([listing('1')]),now=NOW,sender=fail)
        def sender(*args):
            # Simulate another thread committing while the network request is in progress.
            import threading
            def other():
                with connect(self.config.database_path) as db:
                    db.execute("INSERT INTO activity_events(actor,action,target) VALUES('other','other.change','1')")
                    db.commit()
            thread=threading.Thread(target=other);thread.start();thread.join()
            return 'accepted-after-concurrent-change'
        with patch('audit_app.job.send_email',side_effect=sender):
            response=self.post('/retry/1',{},role='reviewer')
        self.assertEqual(response.status_code,303,response.text)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT email_status FROM audits').fetchone()[0],'email_sent')

    def test_only_access_auth_is_supported(self):
        for mode in ('local', 'basic', 'none'):
            cfg = replace(self.config, auth_mode=mode)
            with self.assertRaises(ValueError):validate_web_config(cfg)
        basic = self.client.get('/', base_url=self.config.public_url,
                                headers={'Authorization': 'Basic c3RhZmY6cGFzc3dvcmQ='})
        self.assertEqual(basic.status_code, 401)
        self.assertNotIn('WWW-Authenticate', basic.headers)
        response=self.get()
        self.assertIn('Secure',response.headers.get('Set-Cookie',''))
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertEqual(self.client.get('/',base_url='https://evil.example').status_code,400)

    def test_simulation_works_with_safe_disabled_defaults(self):
        from audit_app.simulation import simulate_cycle
        result = simulate_cycle(replace(self.config, email_enabled=False))
        self.assertTrue(result['manual_retry_succeeded'])
        self.assertEqual(result['final_email_status'], 'email_sent')
