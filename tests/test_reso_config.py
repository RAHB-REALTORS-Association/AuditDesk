import os
import unittest
from unittest.mock import patch

from audit_app.bridge import BridgeClient, BridgeError, ResoClient, ResoError
from audit_app.config import load_config


class ResoConfigurationTests(unittest.TestCase):
    def test_legacy_settings_and_client_imports_remain_supported(self):
        with patch.dict(os.environ, {
            'APP_ENV': 'test', 'BRIDGE_BASE_URL': 'https://legacy.example.test/odata',
            'BRIDGE_API_KEY': 'legacy-key', 'BRIDGE_AUTH_MODE': 'query',
            'BRIDGE_FIELD_MAP': 'bridge_fields.json',
        }, clear=True):
            config = load_config()
        self.assertEqual(config.bridge_base_url, 'https://legacy.example.test/odata')
        self.assertEqual(config.bridge_key, 'legacy-key')
        self.assertEqual(config.bridge_auth_mode, 'query')
        self.assertIs(BridgeClient, ResoClient)
        self.assertIs(BridgeError, ResoError)

    def test_reso_settings_override_legacy_settings(self):
        with patch.dict(os.environ, {
            'APP_ENV': 'test', 'RESO_BASE_URL': 'https://mls.example.test/odata',
            'RESO_API_KEY': 'reso-key', 'RESO_AUTH_MODE': 'bearer',
            'RESO_FIELD_MAP': 'bridge_fields.json',
            'BRIDGE_BASE_URL': 'https://legacy.example.test/odata',
            'BRIDGE_API_KEY': 'legacy-key', 'BRIDGE_AUTH_MODE': 'query',
            'BRIDGE_FIELD_MAP': 'missing-legacy-map.json',
        }, clear=True):
            config = load_config()
        self.assertEqual(config.bridge_base_url, 'https://mls.example.test/odata')
        self.assertEqual(config.bridge_key, 'reso-key')
        self.assertEqual(config.bridge_auth_mode, 'bearer')
        self.assertIn('Property', config.field_map)

    def test_explicit_empty_reso_credentials_do_not_fall_back(self):
        with patch.dict(os.environ, {
            'APP_ENV': 'test', 'RESO_API_KEY': '', 'RESO_BASE_URL': '',
            'BRIDGE_API_KEY': 'legacy-key', 'BRIDGE_BASE_URL': 'https://legacy.example.test',
        }, clear=True):
            config = load_config()
        self.assertEqual(config.bridge_key, '')
        self.assertEqual(config.bridge_base_url, '')

    def test_development_ignores_reso_credentials_and_enable_flags(self):
        with patch.dict(os.environ, {
            'APP_ENV': 'development', 'RESO_BASE_URL': 'https://mls.example.test/odata',
            'RESO_API_KEY': 'reso-key', 'EMAIL_ENABLED': 'true', 'SCHEDULER_ENABLED': 'true',
        }, clear=True):
            config = load_config()
        self.assertEqual(config.bridge_base_url, '')
        self.assertEqual(config.bridge_key, '')
        self.assertFalse(config.email_enabled)
        self.assertFalse(config.scheduler_enabled)
