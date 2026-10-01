import unittest
from datetime import datetime, timezone
from email.message import Message
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from audit_app.bridge import BridgeClient, BridgeError, retry_after_seconds


class BridgeRetryTests(unittest.TestCase):
    def client(self):
        return BridgeClient(SimpleNamespace(env='test', bridge_base_url='https://bridge.example.test/dataset',
                                           bridge_key='test-key', bridge_auth_mode='bearer'))

    def error(self, code, retry_after=None):
        headers = Message()
        if retry_after is not None:
            headers['Retry-After'] = retry_after
        return HTTPError('https://bridge.example.test/dataset/Member', code, 'Error', headers, None)

    def response(self):
        result = MagicMock()
        result.__enter__.return_value.read.return_value = b'{"value":[]}'
        return result

    def test_rate_limit_waits_for_provider_delay_then_resumes(self):
        with patch('audit_app.bridge.urllib.request.urlopen', side_effect=[self.error(429, '75'), self.response()]) as request, \
                patch('audit_app.bridge.time.sleep') as sleep:
            self.assertEqual(self.client()._get('https://bridge.example.test/dataset/Member'), b'{"value":[]}')
            sleep.assert_called_once_with(75)
            self.assertEqual(request.call_count, 2)

    def test_rate_limit_without_valid_header_waits_a_full_minute(self):
        with patch('audit_app.bridge.urllib.request.urlopen', side_effect=[self.error(429, 'invalid'), self.response()]), \
                patch('audit_app.bridge.time.sleep') as sleep:
            self.client()._get('https://bridge.example.test/dataset/Member')
            sleep.assert_called_once_with(60)

    def test_http_date_and_invalid_retry_headers(self):
        now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        self.assertEqual(retry_after_seconds('Thu, 01 Oct 2026 00:02:00 GMT', now), 120)
        for value in ('invalid', 'nan', 'inf', None):
            self.assertIsNone(retry_after_seconds(value, now))

    def test_long_provider_delay_does_not_retry_early(self):
        with patch('audit_app.bridge.urllib.request.urlopen', side_effect=self.error(429, '3600')) as request, \
                patch('audit_app.bridge.time.sleep') as sleep:
            with self.assertRaisesRegex(BridgeError, 'retry intake later'):
                self.client()._get('https://bridge.example.test/dataset/Member')
            self.assertEqual(request.call_count, 1)
            sleep.assert_not_called()

    def test_permission_failure_is_not_retried(self):
        with patch('audit_app.bridge.urllib.request.urlopen', side_effect=self.error(403)) as request, \
                patch('audit_app.bridge.time.sleep') as sleep:
            with self.assertRaisesRegex(BridgeError, '403'):
                self.client()._get('https://bridge.example.test/dataset/Member')
            self.assertEqual(request.call_count, 1)
            sleep.assert_not_called()

    def test_repeated_throttling_is_bounded(self):
        with patch('audit_app.bridge.urllib.request.urlopen', side_effect=self.error(429, '60')) as request, \
                patch('audit_app.bridge.time.sleep') as sleep:
            with self.assertRaisesRegex(BridgeError, '429'):
                self.client()._get('https://bridge.example.test/dataset/Member')
            self.assertEqual(request.call_count, 6)
            self.assertEqual(sleep.call_count, 5)
