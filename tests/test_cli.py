"""Exercise both supported launchers using isolated development commands."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = ((str(ROOT / "main.py"),), ("-m", "audit_app"))


class CliTests(unittest.TestCase):
    def command(self, launcher, *args):
        # A fresh working directory avoids a contributor's .env. An explicit
        # field map and module path let the actual launchers run from there.
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "APP_ENV": "development", "PYTHONPATH": str(ROOT),
                   "BRIDGE_FIELD_MAP": str(ROOT / "bridge_fields.json"),
                   "PUBLIC_BASE_URL": "http://127.0.0.1:8765"}
            return subprocess.run([sys.executable, *launcher, *args], cwd=directory,
                                  env=env, text=True, capture_output=True, timeout=15)

    def test_both_launchers_complete_the_synthetic_cycle(self):
        for launcher in LAUNCHERS:
            with self.subTest(launcher=launcher):
                result = self.command(launcher, "simulate")
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["audit_records_for_new_listings"], 1)
                self.assertEqual(report["second_run"]["selected"], 0)
                self.assertTrue(report["manual_retry_succeeded"])
                self.assertEqual(report["final_email_status"], "email_sent")

    def test_development_run_uses_synthetic_data(self):
        for launcher in LAUNCHERS:
            with self.subTest(launcher=launcher):
                result = self.command(launcher, "run")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("synthetic", json.loads(result.stdout)["mode"])

    def test_development_rejects_live_and_restore_commands(self):
        for launcher in LAUNCHERS:
            for command in ("inspect-bridge", "backfill-office-addresses", "backup", "restore"):
                with self.subTest(launcher=launcher, command=command):
                    result = self.command(launcher, command)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("disposable synthetic data", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
