import re
import io
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

    def test_eligibility_management_preview_save_validation_and_roles(self):
        self.seed_asana_audits()
        with connect(self.config.database_path) as db:
            db.execute("UPDATE listings SET agent_membership_class='MEMBER'")
            db.commit()
        form = {'membership_classes':'NL7, MEMBER', 'agent_ids':'', 'action':'preview'}
        self.assertEqual(self.get('/?tab=eligibility', role='reviewer').status_code, 403)
        self.assertEqual(self.post('/manage/eligibility', form, role='reviewer').status_code, 403)
        with connect(self.config.database_path) as db:
            before = db.execute('SELECT count(*) FROM activity_events').fetchone()[0]
        preview = self.post('/manage/eligibility', form, role='manager', page='/?tab=eligibility')
        self.assertEqual(preview.status_code, 200)
        self.assertIn('Preview of unsaved rules', preview.text)
        self.assertIn('Excluded membership class: MEMBER', preview.text)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM activity_events').fetchone()[0], before)
            self.assertEqual(db.execute('SELECT count(*) FROM listing_eligibility').fetchone()[0], 0)
        invalid = self.post('/manage/eligibility', {**form,'membership_classes':"NL7'",'action':'save'}, page='/?tab=eligibility')
        self.assertEqual(invalid.status_code, 400)
        self.assertIn('NL7&#x27;', invalid.text)
        self.assertIn('use up to 50 codes', invalid.text)
        self.assertEqual(self.post('/manage/eligibility', {**form,'action':'save'}, role='manager', page='/?tab=eligibility').status_code, 303)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM audits').fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT actor FROM activity_events ORDER BY id DESC LIMIT 1').fetchone()[0], 'manager')
        self.assertIn('No records match these filters', self.get('/?tab=audits').text)

    def test_delete_person_revokes_access_clears_assignments_and_preserves_audits(self):
        self.seed_asana_audits()
        with connect(self.config.database_path) as db:
            reviewer=db.execute("SELECT * FROM app_users WHERE subject='reviewer'").fetchone()
            db.execute('UPDATE audits SET assignee_user_id=? WHERE id=1',(reviewer['id'],));db.commit()
        self.assertEqual(self.post(f"/users/{reviewer['id']}/delete",{'version':str(reviewer['version'])},role='manager',page='/?tab=manage').status_code,403)
        self.assertEqual(self.post(f"/users/{reviewer['id']}/delete",{'version':'999'},page='/?tab=users').status_code,400)
        self.assertEqual(self.post(f"/users/{reviewer['id']}/delete",{'version':str(reviewer['version'])},page='/?tab=users').status_code,303)
        self.assertEqual(self.get(role='reviewer').status_code,403)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM audits').fetchone()[0],3)
            self.assertIsNone(db.execute('SELECT assignee_user_id FROM audits WHERE id=1').fetchone()[0])
        with connect(self.config.database_path) as db:
            admin=db.execute("SELECT * FROM app_users WHERE email='admin@example.com'").fetchone()
        self.assertEqual(self.post(f"/users/{admin['id']}/delete",{'version':str(admin['version'])},page='/?tab=users').status_code,400)

    def test_bulk_refresh_route_has_selection_and_accepts_multiple_ids(self):
        self.seed_asana_audits()
        page=self.get(role='reviewer').text
        self.assertIn('Refresh selected from MLS',page)
        self.assertNotIn('Assign selected to',page)
        with patch('audit_app.application.refresh_audits',return_value={'refreshed':2,'skipped':0,'needs_attention':0}) as refresh:
            response=self.post('/audits/refresh',{'audit_ids':['1','3']},role='reviewer')
            self.assertEqual(response.status_code,303)
            self.assertEqual(refresh.call_args.args[1],[1,3])
        self.assertEqual(self.post('/audits/refresh',{'audit_ids':['x']}).status_code,400)

    def test_errors_are_styled_without_authenticated_assets_and_escape_content(self):
        from audit_app.views.errors import error_page
        for response, status in ((self.client.get('/'),401),
                                 (self.get('/?tab=users',role='reviewer'),403),
                                 (self.get('/not-a-page'),404),
                                 (self.post('/manage/selection',{}),400)):
            self.assertEqual(response.status_code,status)
            self.assertIn('<!doctype html>',response.text)
            self.assertIn('<style>',response.text)
            self.assertIn('error-card',response.text)
            self.assertIn('Return to AuditDesk',response.text)
            self.assertNotIn('src="/static/',response.text)
            self.assertEqual(response.headers['Cache-Control'],'no-store')
            self.assertIn(response.headers['X-Request-ID'],response.text)
        self.assertIn('&lt;script&gt;',error_page(400,'Bad','<script>'))
        with patch('audit_app.application.web.render',side_effect=RuntimeError('private internals')):
            response=self.get()
        self.assertEqual(response.status_code,500)
        self.assertIn('error-card',response.text)
        self.assertNotIn('private internals',response.text)

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
                db.execute("""INSERT INTO listings(agent_mls_id,originating_system_name,bridge_listing_id,mls_number,status,entry_timestamp,
                    address,brokerage_id,brokerage_name,brokerage_address,first_processed_at,processing_status)
                    VALUES('DEMO-MEMBER','Cornerstone',?,?,'Active',?,'Example',?,'Example Realty',?,?,'processed_not_selected')""",
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
                db.execute("""INSERT INTO listings(agent_mls_id,originating_system_name,id,bridge_listing_id,mls_number,status,entry_timestamp,
                    address,first_processed_at,processing_status)
                    VALUES('DEMO-MEMBER','Cornerstone',?,?,?,'Active','2026','Example','2026','selected_for_audit')""", (number,str(number),str(number)))
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

    def test_recipient_errors_mark_run_and_remain_visible_on_older_runs(self):
        item = listing('routing-error')
        item['broker_email'] = ''
        sender = Mock()
        run_job(self.config, FakeClient([item]), now=NOW, sender=sender)
        sender.assert_not_called()
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT status FROM runs').fetchone()[0], 'completed_with_email_errors')
            # Existing runs from older builds still surface unresolved delivery errors.
            db.execute("UPDATE runs SET status='completed',error=NULL")
            db.commit()
        page = self.get('/?tab=runs', role='reviewer').text
        self.assertIn('Completed With Email Errors', page)
        self.assertIn('1 email(s) need attention', page)
        client = Mock()
        result = run_job(self.config, client, now=NOW, only_if_needed=True, sender=sender)
        self.assertEqual(result['skipped'], 'already_ran_today')
        client.active_new_listings.assert_not_called()

    def test_disabled_email_is_visible_as_pending_on_run_list(self):
        run_job(replace(self.config,email_enabled=False), FakeClient([listing('pending')]), now=NOW)
        page = self.get('/?tab=runs').text
        self.assertIn('Completed With Pending Email', page)
        self.assertIn('email(s) queued; waiting for a request window or email enablement', page)

    def test_lists_page_filter_and_sort_the_full_history(self):
        with connect(self.config.database_path) as db:
            for number in range(205):
                db.execute("""INSERT INTO listings(agent_mls_id,originating_system_name,bridge_listing_id,mls_number,status,entry_timestamp,
                    address,first_processed_at,processing_status) VALUES('DEMO-MEMBER','Cornerstone',?,?,'Active',?,'Example',?,?)""",
                    (str(number),f'PAGE-{number:03}',NOW.isoformat(),NOW.isoformat(),
                     'email_failed' if number % 2 else 'processed_not_selected'))
            db.commit()
        page = self.get('/?tab=listings&per_page=10&page=21',role='reviewer').text
        self.assertIn('201–205 of 205 records',page)
        self.assertIn('<strong>PAGE-000</strong>',page)
        self.assertNotIn('<strong>PAGE-204</strong>',page)
        page = self.get('/?tab=listings&per_page=10&sort=MLS+%2F+Property&direction=asc').text
        self.assertIn('<strong>PAGE-000</strong>',page)
        self.assertNotIn('<strong>PAGE-204</strong>',page)
        page = self.get('/?tab=listings&q=PAGE-20&status=email_failed').text
        self.assertIn('1–2 of 2 records',page)
        self.assertIn('<strong>PAGE-201</strong>',page)
        self.assertNotIn('<strong>PAGE-202</strong>',page)
        page = self.get('/?tab=listings&q=%27+OR+1%3D1--').text
        self.assertIn('0–0 of 0 records',page)
        self.assertIn('No records match these filters',page)
        for query in ('per_page=100000','page=0','page=abc','page=999999999999'):
            self.assertEqual(self.get('/?tab=listings&'+query).status_code,400)
        page = self.get('/?tab=listings&page=999').text
        self.assertIn('Page 9 of 9',page)

    def test_filters_and_pagination_are_present_on_every_list(self):
        for tab in ('audits','listings','runs','report','brokerages','activity'):
            with self.subTest(tab=tab):
                response = self.get('/?tab='+tab)
                self.assertEqual(response.status_code,200,response.text)
                self.assertIn('class="list-filters"',response.text)
                self.assertIn('Rows per page',response.text)
                self.assertNotIn('Daily selection ·',response.text)

    def seed_asana_audits(self):
        with connect(self.config.database_path) as db:
            for number, outcome in ((1,'failed'),(2,'passed'),(3,None)):
                db.execute("""INSERT INTO listings(agent_mls_id,originating_system_name,id,bridge_listing_id,mls_number,status,entry_timestamp,
                    address,first_processed_at,processing_status)
                    VALUES('DEMO-MEMBER','Cornerstone',?,?,?,'Active','2026-10-01T12:00:00+00:00','Example & Main','2026-10-01T12:00:00+00:00','selected_for_audit')""", (number,str(number),str(number)))
                db.execute("""INSERT INTO audits(id,listing_id,selected_at,intended_to,intended_cc,
                    actual_recipients,test_mode,email_status,selection_metadata,outcome,issues)
                    VALUES(?,?,'2026-10-01T12:00:00+00:00','[]','[]','[]',1,'email_sent','{}',?,?)""", (number,number,outcome,'Missing document <check> & details'))
            db.commit()

    def test_asana_handoff_only_for_failed_outcomes_and_safe_prefill(self):
        from html import unescape
        from urllib.parse import parse_qs, urlsplit
        self.seed_asana_audits()
        page=self.get('/?tab=outcome&id=1',role='reviewer').text
        self.assertIn('Create Asana task',page)
        self.assertIn('Copy follow-up details',page)
        self.assertRegex(page, r'id="asana-task-url"[^>]*value=""')
        self.assertIn('Create Asana task',self.get().text)
        self.assertIn('Link task / copy details',self.get().text)
        self.assertIn('https://app.asana.com/0/-/create_task?',self.get().text)
        draft=unescape(re.search(r'href="(https://app.asana.com/0/-/create_task[^"]+)"',page)[1])
        query=parse_qs(urlsplit(draft).query)
        self.assertIn('MLS 1',query['name'][0])
        self.assertIn('Missing document <check> & details',query['notes'][0])
        self.assertIn('https://audit.example.com/?tab=outcome&id=1',query['notes'][0])
        self.assertNotIn('<check>',page)
        for number in (2,3):
            self.assertNotIn('Create Asana task',self.get(f'/?tab=outcome&id={number}').text)
        # Opening/rendering the handoff never records a created Asana task.
        with connect(self.config.database_path) as db:
            self.assertIsNone(db.execute('SELECT asana_task_url FROM audits WHERE id=1').fetchone()[0])

    def test_foreign_and_unverified_audits_are_hidden_and_cannot_be_acted_on(self):
        from audit_app.job import deliver_audit
        from audit_app.outcomes import deliver_failure_notice
        from audit_app.brokerage_report import brokerage_statistics
        self.seed_asana_audits()
        with connect(self.config.database_path) as db:
            db.execute("UPDATE listings SET originating_system_name='BRREA',mls_number='FOREIGN-MARKER' WHERE id=2")
            db.execute("UPDATE listings SET originating_system_name=NULL,mls_number='UNKNOWN-MARKER' WHERE id=3")
            db.execute("UPDATE audits SET outcome='failed',issues='Missing form',email_status='email_pending',failure_email_status='email_pending' WHERE id IN (2,3)")
            db.commit()
        for tab in ('audits','listings'):
            page=self.get('/?tab='+tab).text
            self.assertNotIn('FOREIGN-MARKER',page)
            self.assertNotIn('UNKNOWN-MARKER',page)
            self.assertIn('Records awaiting eligibility verification',page)
        self.assertEqual(brokerage_statistics(self.config)['total'],1)
        sender=Mock()
        for number in (2,3):
            self.assertEqual(self.get(f'/?tab=outcome&id={number}').status_code,400)
            self.assertEqual(self.post(f'/outcome/{number}',{'outcome':'failed','action':'preview','issues':'Missing form'}).status_code,400)
            self.assertEqual(self.post(f'/outcome/{number}/asana',{'asana_task_url':'https://app.asana.com/0/1/2'}).status_code,400)
            self.assertEqual(self.post(f'/assignment/{number}',{'assignee_user_id':'2'}).status_code,400)
            self.assertFalse(deliver_audit(self.config,number,sender=sender))
            self.assertFalse(deliver_failure_notice(self.config,number,sender=sender))
        sender.assert_not_called()

    def test_nonmember_and_unknown_membership_audits_are_hidden_and_cannot_be_acted_on(self):
        from audit_app.job import deliver_audit
        from audit_app.outcomes import deliver_failure_notice
        from audit_app.brokerage_report import brokerage_statistics
        self.seed_asana_audits()
        with connect(self.config.database_path) as db:
            db.execute("UPDATE listings SET agent_mls_id=' nonmem ',mls_number='FOREIGN-MARKER' WHERE id=2")
            db.execute("UPDATE listings SET agent_mls_id=NULL,mls_number='UNKNOWN-MARKER' WHERE id=3")
            db.execute("UPDATE audits SET outcome='failed',issues='Missing form',email_status='email_pending',failure_email_status='email_pending' WHERE id IN (2,3)")
            db.commit()
        for tab in ('audits','listings'):
            page=self.get('/?tab='+tab).text
            self.assertNotIn('FOREIGN-MARKER',page)
            self.assertNotIn('UNKNOWN-MARKER',page)
            self.assertIn('Records awaiting eligibility verification',page)
        self.assertEqual(brokerage_statistics(self.config)['total'],1)
        sender=Mock()
        for number in (2,3):
            self.assertEqual(self.get(f'/?tab=outcome&id={number}').status_code,400)
            self.assertEqual(self.post(f'/outcome/{number}',{'outcome':'failed','action':'preview','issues':'Missing form'}).status_code,400)
            self.assertEqual(self.post(f'/outcome/{number}/asana',{'asana_task_url':'https://app.asana.com/0/1/2'}).status_code,400)
            self.assertEqual(self.post(f'/assignment/{number}',{'assignee_user_id':'2'}).status_code,400)
            self.assertFalse(deliver_audit(self.config,number,sender=sender))
            self.assertFalse(deliver_failure_notice(self.config,number,sender=sender))
        sender.assert_not_called()

    def test_recording_failure_opens_asana_follow_up(self):
        self.seed_asana_audits()
        with patch('audit_app.outcomes.deliver_failure_notice',return_value=False):
            response=self.post('/outcome/3',{'outcome':'failed','action':'send','issues':'Missing form'},page='/?tab=outcome&id=3')
        self.assertEqual(response.status_code,303)
        self.assertIn('tab=outcome',response.headers['Location'])
        self.assertIn('id=3',response.headers['Location'])
        self.assertIn('Create Asana task',self.get(response.headers['Location']).text)

    def test_asana_task_link_validation_csrf_revision_and_failed_only(self):
        self.seed_asana_audits()
        path='/outcome/1/asana'
        url='https://app.asana.com/1/123/project/456/task/789'
        self.assertEqual(self.post(path,{'asana_task_url':url,'token':'forged'}).status_code,403)
        for bad in ('javascript:alert(1)','https://app.asana.com.evil.test/0/1/2','https://user@app.asana.com/0/1/2','https://app.asana.com/-/create_task','https://app.asana.com/0/1/2\nBAD','x'*2049):
            self.assertEqual(self.post(path,{'asana_task_url':bad}).status_code,400)
        self.assertEqual(self.post('/outcome/2/asana',{'asana_task_url':url}).status_code,400)
        self.assertEqual(self.post('/outcome/999/asana',{'asana_task_url':url}).status_code,400)
        self.assertEqual(self.post(path,{'asana_task_url':url},role='reviewer',page='/?tab=outcome&id=1').status_code,303)
        page=self.get('/?tab=outcome&id=1').text
        self.assertIn('Open Asana task',page)
        self.assertNotIn('Create Asana task',page)
        self.assertIn('Open Asana task',self.get().text)
        self.assertNotIn('Create Asana task',self.get().text)
        self.assertEqual(self.client.post(path,base_url=self.config.public_url,data={'asana_task_url':url,'token':'bad','revision':'0'}).status_code,401)
        self.assertEqual(self.post(path,{'asana_task_url':url,'revision':'0'}).status_code,409)
        self.assertEqual(self.post(path,{'asana_task_url':''}).status_code,303)
        with connect(self.config.database_path) as db:
            self.assertIsNone(db.execute('SELECT asana_task_url FROM audits WHERE id=1').fetchone()[0])
            self.assertEqual(db.execute("SELECT actor FROM activity_events WHERE action='audit.asana_link_saved'").fetchone()[0],'reviewer')

    def test_combined_selection_save_is_atomic_and_role_checked(self):
        form = {'rate_percent':'8.5','cooldown_days':'21','broker_cooldown_days':'7','window_hours':'48'}
        self.assertEqual(self.post('/manage/selection',form,role='reviewer').status_code,403)
        self.assertEqual(self.post('/manage/selection',form,role='manager',page='/?tab=manage').status_code,303)
        for invalid in ({'rate_percent':'101'}, {'cooldown_days':'366'}, {'window_hours':'0'}):
            self.assertEqual(self.post('/manage/selection',{**form,'rate_percent':'9',**invalid},role='manager',page='/?tab=manage').status_code,400)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT rate_percent FROM selection_settings').fetchone()[0],'8.5')
            self.assertEqual(db.execute('SELECT days FROM brokerage_cooldown_settings').fetchone()[0],21)
            row=db.execute('SELECT * FROM workflow_settings').fetchone()
            self.assertEqual((row['broker_cooldown_days'],row['window_hours']),(7,48))
            self.assertEqual(db.execute("SELECT count(*) FROM activity_events WHERE action='settings.selection_updated'").fetchone()[0],1)

    def test_manage_groups_controls_and_enforces_workflow_permissions(self):
        self.assertEqual(self.get('/?tab=manage',role='reviewer').status_code,403)
        manager = self.get('/?tab=manage',role='manager').text
        self.assertIn('Save selection settings',manager)
        self.assertIn('Audit request email',manager)
        self.assertNotIn('href="/?tab=users"',manager)
        self.assertIn('href="/?tab=users"',self.get('/?tab=manage').text)
        form = {'broker_cooldown_days':'7','window_hours':'48'}
        self.assertEqual(self.post('/manage/workflow',form,role='reviewer').status_code,403)
        self.assertEqual(self.post('/manage/workflow',form,role='manager',page='/?tab=manage').status_code,303)
        with connect(self.config.database_path) as db:
            row=db.execute('SELECT * FROM workflow_settings').fetchone()
            self.assertEqual((row['broker_cooldown_days'],row['window_hours']),(7,48))
            self.assertEqual(db.execute("SELECT actor FROM activity_events WHERE action='settings.workflow_updated'").fetchone()[0],'manager')

    def test_recovery_download_upload_and_permission_guards(self):
        for role in ('reviewer','manager'):
            self.assertEqual(self.get('/?tab=recovery',role=role).status_code,403)
            self.assertEqual(self.post('/manage/backup',{},role=role).status_code,403)
        download=self.post('/manage/backup',{},page='/?tab=recovery')
        self.assertEqual(download.status_code,200,download.data[:100])
        self.assertEqual(download.mimetype,'application/octet-stream')
        self.assertIn('schema-9.sqlite3',download.headers['Content-Disposition'])
        self.assertEqual(download.headers['Cache-Control'],'no-store')
        snapshot=Path(self.temp.name)/'download.sqlite3'
        snapshot.write_bytes(download.data)
        download.close()
        self.assertEqual(validate(snapshot),9)
        def upload(value, role='admin', token=None):
            self.get('/?tab=recovery',role=role)
            with self.client.session_transaction(base_url=self.config.public_url) as session:
                csrf=session['csrf']
            with connect(self.config.database_path) as db:
                revision=db.execute('SELECT COALESCE(max(id),0) FROM activity_events').fetchone()[0]
            return self.client.post('/manage/restore', data={'backup':(io.BytesIO(value),'../../evil.sqlite3'),
                'token':csrf if token is None else token,'revision':str(revision)}, base_url=self.config.public_url,
                headers={'Cf-Access-Jwt-Assertion':self.token(role),'Origin':self.config.public_url})
        self.assertEqual(upload(snapshot.read_bytes(),role='manager').status_code,403)
        self.assertEqual(upload(snapshot.read_bytes(),token='bad').status_code,403)
        self.assertEqual(upload(b'corrupt').status_code,400)
        response=upload(snapshot.read_bytes())
        self.assertEqual(response.status_code,303,response.text)
        page=self.get(response.headers['Location']).text
        self.assertIn('Validated restore file',page)
        self.assertIn('RESTORE STOPPED AUDITDESK',page)
        self.assertIn('SHA-256',page)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute("SELECT actor FROM activity_events WHERE action='backup.restore_staged'").fetchone()[0],'admin')
        self.assertEqual(self.get('/?tab=recovery&restore=../../live').status_code,400)

    def test_intake_catches_up_from_last_success(self):
        run_job(self.config,FakeClient([]),now=NOW,sender=Mock())
        client=Mock();client.active_new_listings.return_value=[]
        run_job(self.config,client,now=NOW+timedelta(days=3),sender=Mock())
        self.assertEqual(client.active_new_listings.call_args.args[0],NOW)

    def test_backup_restore_integrity_and_refuse_live_restore(self):
        file=Path(self.temp.name)/'backup.sqlite3'
        backup(self.config,file)
        self.assertEqual(validate(file),9)
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
        with self.assertRaises(ValueError):validate(bad)

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
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],9)
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
        with patch('audit_app.job.send_email',side_effect=sender), patch('audit_app.job.can_send_request',return_value=True):
            response=self.post('/retry/1',{},role='reviewer')
        self.assertEqual(response.status_code,303,response.text)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT email_status FROM audits').fetchone()[0],'email_sent')

    def test_calendar_and_response_permissions_and_filters(self):
        self.assertEqual(self.get('/?tab=calendar',role='reviewer').status_code,403)
        form={'holidays':'2026-12-25'}
        for day in range(7):
            form.update({f'open_{day}':'08:30' if day<5 else '',f'close_{day}':'16:30' if day<5 else ''})
        self.assertEqual(self.post('/manage/calendar',form,role='reviewer').status_code,403)
        self.assertEqual(self.post('/manage/calendar',form,role='manager').status_code,303)
        self.assertIn('2026-12-25',self.get('/?tab=calendar',role='manager').text)
        run_job(self.config,FakeClient([listing('1')]),now=NOW,sender=lambda *args:'accepted')
        self.assertIn('Overdue by',self.get('/?tab=audits&response=overdue',role='reviewer').text)
        self.assertEqual(self.post('/audits/1/response',{'action':'received'},role='reviewer').status_code,303)
        self.assertIn('awaiting review',self.get('/?tab=audits&response=received',role='reviewer').text)
        self.assertNotIn('MLS 1',self.get('/?tab=audits&response=overdue',role='reviewer').text)
        self.assertEqual(self.post('/audits/1/response',{'action':'received'},role='reviewer').status_code,400)
        self.assertEqual(self.post('/audits/1/response',{'action':'reopen'},role='reviewer').status_code,303)
        self.assertEqual(self.get('/?tab=audits&response=bogus').status_code,400)

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
