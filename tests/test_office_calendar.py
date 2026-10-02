import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from audit_app.config import load_config
from audit_app.database import connect, init_db
from audit_app.job import deliver_audit, run_job
from audit_app.office_calendar import DEFAULT_HOURS, can_send_request, next_request_time, read_calendar, save_calendar
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
        form={'holidays':'2026-12-25\n2026-12-25\n2027-01-01'}
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
