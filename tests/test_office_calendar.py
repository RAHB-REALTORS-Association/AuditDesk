import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from audit_app.config import load_config
from audit_app.database import connect, init_db
from audit_app.job import deliver_audit, run_job
from audit_app.office_calendar import DEFAULT_HOURS, can_send_request, next_request_time, read_calendar, response_deadline, save_calendar
from audit_app.request_schedule import RequestPolicy, read_policy, response_period_text
from audit_app.responses import record_response
from audit_app.views.deadlines import deadline_view
from test_app import FakeClient, listing


def instant(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.config = replace(load_config(), env='test', email_enabled=True, rate=1,
            database_path=str(Path(self.temp.name)/'audit.sqlite3'), admin_email='admin@example.invalid')
        init_db(self.config.database_path)
        self.calendar = (DEFAULT_HOURS, set())
        self.zone = 'America/Toronto'

    def schedule_form(self, **overrides):
        form = {'response_days': '1', 'deadline_mode': 'elapsed', 'holidays': '',
                **{f'send_{day}': '1' for day in range(7)}}
        for day, interval in enumerate(DEFAULT_HOURS):
            form[f'open_{day}'], form[f'close_{day}'] = interval or ('', '')
        return {**form, **overrides}

    def test_request_time_blocks_early_delivery_and_updates_next_window(self):
        before = instant('2026-10-05T09:00:00-04:00')
        at = instant('2026-10-05T10:15:00-04:00')
        policy = RequestPolicy(send_time='10:15')
        self.assertFalse(can_send_request(before, self.zone, self.calendar, policy))
        self.assertEqual(next_request_time(before, self.zone, self.calendar, policy), at)
        self.assertTrue(can_send_request(at, self.zone, self.calendar, policy))
        sender = Mock(return_value='accepted')
        save_calendar(self.config, self.schedule_form(send_time='10:15'))
        run_job(self.config, FakeClient([listing('1')]), now=before, sender=sender)
        sender.assert_not_called()
        run_job(self.config, FakeClient([]), now=at, sender=sender, only_if_needed=True)
        self.assertEqual(sender.call_count, 1)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT response_due_at FROM audits').fetchone()[0],
                             (at + timedelta(days=1)).isoformat(timespec='seconds'))
        self.assertEqual(read_policy(self.config).send_time, '10:15')

    def test_request_time_respects_closures_opening_deadlines_and_dst(self):
        before = instant('2026-10-05T07:00:00-04:00')
        self.assertEqual(next_request_time(before, self.zone, self.calendar, RequestPolicy(send_time='07:30')),
                         instant('2026-10-05T08:30:00-04:00'))
        self.assertIsNone(next_request_time(before, self.zone, self.calendar, RequestPolicy(send_time='16:30')))
        holiday_calendar = (DEFAULT_HOURS, {'2026-10-05'})
        self.assertEqual(next_request_time(before, self.zone, holiday_calendar, RequestPolicy(send_time='10:15')),
                         instant('2026-10-06T10:15:00-04:00'))
        friday = instant('2026-10-02T09:00:00-04:00')
        self.assertEqual(next_request_time(friday, self.zone, self.calendar, RequestPolicy(send_time='10:15')),
                         instant('2026-10-05T10:15:00-04:00'))
        all_hours = ([('00:00','23:59')] * 7, set())
        gap = instant('2026-03-08T01:00:00-05:00')
        self.assertEqual(next_request_time(gap, self.zone, all_hours, RequestPolicy(send_time='02:30', deadline_mode='next_open')),
                         instant('2026-03-08T03:00:00-04:00'))

    def test_send_time_validation_and_schema_11_upgrade(self):
        save_calendar(self.config, self.schedule_form(send_time='10:15'))
        for value in ('25:00', '9:00', '10:60', '10:15:00', ' 10:15', '<script>'):
            with self.assertRaises(ValueError):
                save_calendar(self.config, self.schedule_form(send_time=value))
            self.assertEqual(read_policy(self.config).send_time, '10:15')
        with connect(self.config.database_path) as db:
            db.execute('ALTER TABLE request_schedule DROP COLUMN send_time')
            db.execute('PRAGMA user_version=11')
            db.commit()
        init_db(self.config.database_path)
        init_db(self.config.database_path)
        self.assertEqual(read_policy(self.config), RequestPolicy())
        self.assertEqual(read_calendar(self.config)[0][0], ['08:30', '16:30'])

    def test_defaults_preserve_legacy_calendar_and_deadline_behavior(self):
        self.assertEqual(read_policy(self.config), RequestPolicy())
        with connect(self.config.database_path) as db:
            db.execute('DROP TABLE request_schedule')
            db.execute('PRAGMA user_version=9')
            db.commit()
        init_db(self.config.database_path)
        init_db(self.config.database_path)
        self.assertEqual(read_policy(self.config), RequestPolicy())
        friday = instant('2026-10-02T10:00:00-04:00')
        self.assertFalse(can_send_request(friday, self.zone, self.calendar, read_policy(self.config)))
        self.assertEqual(response_deadline(friday, self.zone, self.calendar), friday + timedelta(hours=24))

    def test_friday_deadline_modes_holidays_and_business_day_count(self):
        friday = instant('2026-10-02T10:00:00-04:00')
        roll = RequestPolicy(deadline_mode='next_open')
        business = RequestPolicy(deadline_mode='business_days')
        self.assertTrue(can_send_request(friday, self.zone, self.calendar, roll))
        self.assertEqual(response_deadline(friday,self.zone,self.calendar,roll), instant('2026-10-05T08:30:00-04:00'))
        self.assertEqual(response_deadline(friday,self.zone,self.calendar,business), instant('2026-10-05T10:00:00-04:00'))
        holiday = (DEFAULT_HOURS, {'2026-10-05'})
        self.assertEqual(response_deadline(friday,self.zone,holiday,roll), instant('2026-10-06T08:30:00-04:00'))
        self.assertEqual(response_deadline(friday,self.zone,holiday,business), instant('2026-10-06T10:00:00-04:00'))
        self.assertEqual(response_deadline(friday,self.zone,holiday,RequestPolicy(response_days=2,deadline_mode='business_days')), instant('2026-10-07T10:00:00-04:00'))

    def test_send_days_are_independent_from_deadline_office_days(self):
        friday = instant('2026-10-02T10:00:00-04:00')
        policy = RequestPolicy(send_days=(4,), deadline_mode='business_days')
        self.assertTrue(can_send_request(friday,self.zone,self.calendar,policy))
        self.assertFalse(can_send_request(instant('2026-10-05T10:00:00-04:00'),self.zone,self.calendar,policy))
        self.assertEqual(response_deadline(friday,self.zone,self.calendar,policy), instant('2026-10-05T10:00:00-04:00'))
        self.assertEqual(next_request_time(instant('2026-10-05T10:00:00-04:00'),self.zone,self.calendar,policy), instant('2026-10-09T08:30:00-04:00'))
        self.assertIsNone(next_request_time(friday,self.zone,self.calendar,RequestPolicy(send_days=())))

    def test_multi_day_elapsed_windows_and_different_office_hours(self):
        friday = instant('2026-10-02T10:00:00-04:00')
        policy = RequestPolicy(response_days=3)
        self.assertTrue(can_send_request(friday,self.zone,self.calendar,policy))
        self.assertEqual(response_deadline(friday,self.zone,self.calendar,policy), instant('2026-10-05T10:00:00-04:00'))
        hours=list(DEFAULT_HOURS);hours[0]=('12:00','15:00')
        self.assertEqual(next_request_time(friday,self.zone,(hours,set()),policy), instant('2026-10-02T12:00:00-04:00'))
        business=RequestPolicy(deadline_mode='business_days')
        self.assertEqual(response_deadline(friday,self.zone,(hours,set()),business), instant('2026-10-05T12:00:00-04:00'))
        hours[0]=('08:30','09:00')
        self.assertEqual(response_deadline(friday,self.zone,(hours,set()),business), instant('2026-10-06T08:30:00-04:00'))

    def test_business_days_preserve_local_clock_across_dst(self):
        business=RequestPolicy(deadline_mode='business_days')
        for sent, due in (('2026-03-06T10:00:00-05:00','2026-03-09T10:00:00-04:00'),
                          ('2026-10-30T10:00:00-04:00','2026-11-02T10:00:00-05:00')):
            self.assertEqual(response_deadline(instant(sent),self.zone,self.calendar,business),instant(due))
        all_hours=([('08:30','16:30')]*7,set())
        start=instant('2026-03-07T08:30:00-05:00')
        self.assertEqual(response_deadline(start,self.zone,all_hours),instant('2026-03-08T09:30:00-04:00'))
        gap_calendar=([('02:30','04:00')]*7,set())
        self.assertEqual(response_deadline(instant('2026-03-07T02:30:00-05:00'),self.zone,gap_calendar,RequestPolicy(deadline_mode='next_open')),instant('2026-03-08T03:30:00-04:00'))
        self.assertEqual(response_deadline(instant('2026-03-07T02:30:00-05:00'),self.zone,gap_calendar,business),instant('2026-03-08T03:00:00-04:00'))

    def test_policy_validation_saves_atomically_with_office_calendar(self):
        save_calendar(self.config,self.schedule_form(deadline_mode='business_days',response_days='2',send_0=''))
        original=read_policy(self.config)
        self.assertEqual(original,RequestPolicy((1,2,3,4,5,6),2,'business_days'))
        for bad in ({'response_days':'0'},{'response_days':'31'},{'response_days':'1.5'},
                    {'deadline_mode':'unknown'},{'send_1':'yes'},{'close_0':'07:00'}):
            with self.assertRaises(ValueError):
                save_calendar(self.config,self.schedule_form(**bad))
            self.assertEqual(read_policy(self.config),original)
        self.assertEqual(read_calendar(self.config), (list(map(lambda value: list(value) if value else None,DEFAULT_HOURS)), set()))

    def test_policy_change_drains_queued_requests_and_freezes_accepted_deadlines(self):
        friday=instant('2026-10-02T10:00:00-04:00');sender=Mock(return_value='accepted')
        run_job(self.config,FakeClient([listing('1')]),now=friday,sender=sender)
        sender.assert_not_called()
        save_calendar(self.config,self.schedule_form(deadline_mode='business_days'))
        run_job(self.config,FakeClient([]),now=friday,only_if_needed=True,sender=sender)
        self.assertEqual(sender.call_count,1)
        self.assertIn('1 business day',sender.call_args.args[1]['response_policy'])
        with connect(self.config.database_path) as db:
            due=db.execute('SELECT response_due_at FROM audits').fetchone()[0]
        self.assertEqual(instant(due),instant('2026-10-05T10:00:00-04:00'))
        save_calendar(self.config,self.schedule_form(response_days='3',holidays='2026-10-05'))
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT response_due_at FROM audits').fetchone()[0],due)
        record_response(self.config,1,'2026-10-05T09:00:00-04:00',now=instant('2026-10-05T11:00:00-04:00'))
        with connect(self.config.database_path) as db:
            self.assertIn('Within deadline',deadline_view(self.config,db.execute('SELECT * FROM audits').fetchone()))

    def test_policy_snapshot_survives_changes_during_send_and_backup_restore(self):
        from audit_app.backup import backup, restore
        save_calendar(self.config,self.schedule_form(deadline_mode='business_days'))
        def sender(*args):
            save_calendar(self.config,self.schedule_form(response_days='3'))
            return 'accepted'
        run_job(self.config,FakeClient([listing('1')]),now=instant('2026-10-02T10:00:00-04:00'),sender=sender)
        with connect(self.config.database_path) as db:
            self.assertEqual(instant(db.execute('SELECT response_due_at FROM audits').fetchone()[0]),instant('2026-10-05T10:00:00-04:00'))
        config=replace(self.config,bootstrap_admins=('admin@example.invalid',))
        snapshot=Path(self.temp.name)/'snapshot.sqlite3'
        backup(config,snapshot)
        save_calendar(config,self.schedule_form(response_days='5'))
        restore(config,snapshot,'RESTORE STOPPED AUDITDESK')
        self.assertEqual(read_policy(config),RequestPolicy(response_days=3))

    def test_request_message_includes_response_policy_for_both_formats(self):
        from audit_app.emailer import message_parts
        info={**listing('1'),'response_policy':response_period_text(RequestPolicy(response_days=2,deadline_mode='business_days'))}
        _,plain,rich=message_parts(info,self.config,[],[])
        self.assertIn('Response period: 2 business days',plain)
        self.assertIn('Response period: 2 business days',rich)

    def test_request_window_boundaries_friday_weekend_and_holiday_eve(self):
        for value in ('2026-10-01T08:29:00-04:00', '2026-10-01T16:30:00-04:00',
                      '2026-10-02T09:00:00-04:00', '2026-10-03T09:00:00-04:00'):
            self.assertFalse(can_send_request(instant(value), self.zone, self.calendar))
        self.assertTrue(can_send_request(instant('2026-10-01T08:30:00-04:00'), self.zone, self.calendar))
        self.assertFalse(can_send_request(instant('2026-10-01T09:00:00-04:00'), self.zone, (DEFAULT_HOURS, {'2026-10-02'})))

    def test_next_window_holiday_weekend_and_unequal_daily_hours(self):
        start = instant('2026-10-02T09:00:00-04:00')
        self.assertEqual(next_request_time(start,self.zone,self.calendar),instant('2026-10-05T08:30:00-04:00'))
        holiday = (DEFAULT_HOURS, {'2026-10-05'})
        self.assertEqual(next_request_time(start,self.zone,holiday),instant('2026-10-06T08:30:00-04:00'))
        hours=list(DEFAULT_HOURS);hours[1]=('10:00','14:00')
        self.assertEqual(next_request_time(instant('2026-10-05T08:30:00-04:00'),self.zone,(hours,set())),instant('2026-10-05T10:00:00-04:00'))
        self.assertIsNone(next_request_time(start,self.zone,([None]*7,set())))

    def test_dst_uses_24_elapsed_hours(self):
        hours=[('08:30','16:30')]*7
        for before, after in (('2026-03-07T08:30:00-05:00','2026-03-08T09:30:00-04:00'),
                              ('2026-10-31T09:30:00-04:00','2026-11-01T08:30:00-05:00')):
            moment=instant(before)
            self.assertTrue(can_send_request(moment,self.zone,(hours,set())))
            self.assertEqual(moment+timedelta(hours=24),instant(after))

    def test_calendar_validation_and_persistence(self):
        form={'response_days':'1','deadline_mode':'elapsed',**{f'send_{day}':'1' for day in range(7)},'holidays':'2026-12-25\n2026-12-25\n2027-01-01'}
        for day in range(7):
            form.update({f'open_{day}':'08:30' if day<5 else '',f'close_{day}':'16:30' if day<5 else ''})
        save_calendar(self.config,form)
        self.assertEqual(read_calendar(self.config)[1],{'2026-12-25','2027-01-01'})
        for bad in ({'close_0':'08:00'},{'open_1':''},{'holidays':'2026-02-30'},{'open_0':'99:00'}):
            with self.assertRaises(ValueError):save_calendar(self.config,{**form,**bad})
        self.assertEqual(read_calendar(self.config)[0][0],['08:30','16:30'])

    def test_weekend_intake_then_queue_drains_after_daily_intake_without_duplicate(self):
        saturday=instant('2026-10-03T09:00:00-04:00');sender=Mock(return_value='accepted')
        self.assertEqual(run_job(self.config,FakeClient([listing('1')]),now=saturday,sender=sender)['selected'],1)
        sender.assert_not_called()
        with connect(self.config.database_path) as db:
            self.assertIsNone(db.execute('SELECT email_sent_at FROM audits').fetchone()[0])
            self.assertEqual(db.execute('SELECT count(*) FROM email_attempts').fetchone()[0],0)
        monday=instant('2026-10-05T08:00:00-04:00')
        run_job(self.config,FakeClient([]),now=monday,only_if_needed=True,sender=sender)
        sender.assert_not_called()
        client=Mock();client.active_new_listings.side_effect=AssertionError('No second intake')
        result=run_job(self.config,client,now=monday+timedelta(minutes=30),only_if_needed=True,sender=sender)
        self.assertEqual(result['skipped'],'already_ran_today');self.assertEqual(sender.call_count,1)
        run_job(self.config,client,now=monday+timedelta(hours=1),only_if_needed=True,sender=sender)
        self.assertEqual(sender.call_count,1)
        with connect(self.config.database_path) as db:
            row=db.execute('SELECT * FROM audits').fetchone()
            self.assertEqual(instant(row['response_due_at'])-instant(row['email_sent_at']),timedelta(hours=24))

    def test_manual_retry_obeys_calendar_and_unknown_is_not_retried(self):
        friday=instant('2026-10-02T10:00:00-04:00');sender=Mock()
        run_job(self.config,FakeClient([listing('1')]),now=friday,sender=sender)
        with connect(self.config.database_path) as db:
            db.execute("UPDATE audits SET email_status='email_failed'");db.commit()
        self.assertFalse(deliver_audit(self.config,1,retry=True,sender=sender,now=friday))
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT email_status FROM audits').fetchone()[0],'email_pending')
            db.execute("UPDATE audits SET email_status='email_unknown'");db.commit()
        self.assertFalse(deliver_audit(self.config,1,retry=True,sender=sender,now=instant('2026-10-05T09:00:00-04:00')))
        sender.assert_not_called()

    def test_receipt_time_stops_clock_without_completing_and_can_be_corrected(self):
        sent=instant('2026-10-01T09:00:00-04:00')
        run_job(self.config,FakeClient([listing('1')]),now=sent,sender=lambda *args:'accepted')
        now=sent+timedelta(hours=30)
        with connect(self.config.database_path) as db:
            audit=db.execute('SELECT * FROM audits').fetchone()
        self.assertIn('Overdue by 6h',deadline_view(self.config,audit,now=now))
        for raw in ((sent-timedelta(seconds=1)).isoformat(),(now+timedelta(seconds=1)).isoformat()):
            with self.assertRaises(ValueError):record_response(self.config,1,raw,now=now)
        record_response(self.config,1,(sent+timedelta(hours=4)).isoformat(),now=now)
        with connect(self.config.database_path) as db:
            audit=db.execute('SELECT * FROM audits').fetchone()
            self.assertIsNone(audit['outcome']);self.assertIn('Awaiting audit review',deadline_view(self.config,audit,now=now))
        with self.assertRaises(ValueError):record_response(self.config,1,now=now)
        record_response(self.config,1,reopen=True,now=now)
        with connect(self.config.database_path) as db:
            self.assertIsNone(db.execute('SELECT response_received_at FROM audits').fetchone()[0])

    def test_schema7_migration_keeps_sent_time_and_uses_legacy_deadline(self):
        sent=instant('2026-10-01T09:00:00-04:00')
        run_job(self.config,FakeClient([listing('1')]),now=sent,sender=lambda *args:'accepted')
        with connect(self.config.database_path) as db:
            db.execute('ALTER TABLE audits DROP COLUMN response_due_at')
            db.execute('ALTER TABLE audits DROP COLUMN response_received_at')
            db.execute('DROP TABLE office_calendar');db.execute('PRAGMA user_version=7');db.commit()
        init_db(self.config.database_path)
        with connect(self.config.database_path) as db:
            audit=db.execute('SELECT * FROM audits').fetchone()
            self.assertEqual(instant(audit['email_sent_at']),sent)
            self.assertIn('Overdue',deadline_view(self.config,audit,now=sent+timedelta(hours=25)))
