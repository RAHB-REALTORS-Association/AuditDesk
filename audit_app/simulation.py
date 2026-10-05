"""Isolated, fully synthetic demonstration of the daily audit flow."""

import json
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .database import connect, init_db
from .emailer import EmailError
from .job import deliver_audit, run_job


class DemoLottery:
    def __init__(self):
        self.draws = iter((0.01, 0.99))

    def random(self):
        return next(self.draws)

    def choices(self, choices, weights, k):
        return [choices[0]]

    def choice(self, choices):
        return choices[0]


class DemoReso:
    def __init__(self, rows):
        self.rows = rows

    def active_new_listings(self, start, end):
        for row in self.rows:
            entered = datetime.fromisoformat(row["entry_timestamp"])
            if row["status"] == "Active" and start <= entered < end:
                yield row


class DemoSender:
    def __init__(self):
        self.attempts = []

    def __call__(self, config, listing, audit_id, intended_to, intended_cc, actual):
        self.attempts.append({
            "intended_to": list(intended_to), "intended_cc": list(intended_cc),
            "actual_recipients": list(actual), "test_mode": config.test_mode,
        })
        if len(self.attempts) == 1:
            raise EmailError("Simulated SendGrid HTTP 503")
        return "SIMULATED-MESSAGE-ID"


def _listing(number, office, broker, entered, status="Active"):
    return {
        "originating_system_name": "Cornerstone", "agent_mls_id": "DEMO-MEMBER", "agent_membership_class": "MEMBER", "listing_id": number,
        "mls_number": number,
        "status": status,
        "entry_timestamp": entered.isoformat(timespec="seconds"),
        "address": f"{number} Example Avenue, Toronto ON",
        "agent_id": f"agent-{number}",
        "agent_name": f"Agent {number}",
        "agent_email": f"agent-{number}@example.invalid",
        "brokerage_id": office,
        "brokerage_name": f"Brokerage {office}",
        "brokerage_email": f"{office}@example.invalid",
        "broker_id": broker,
        "broker_name": f"Broker {broker}",
        "broker_email": f"{broker}@example.invalid",
    }


def simulate_cycle(config):
    now = datetime(2026, 9, 24, 13, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory(prefix="mls-audit-demo-") as folder:
        demo_config = replace(config, env="test", email_enabled=True, test_end_at=None, database_path=str(Path(folder) / "demo.sqlite3"),
                              admin_email="admin@example.invalid", sendgrid_key="", rate=0.05)
        init_db(demo_config.database_path)
        previous_at = (now - timedelta(days=1)).isoformat(timespec="seconds")
        with connect(demo_config.database_path) as db:
            old = db.execute("""INSERT INTO listings(agent_mls_id,originating_system_name,bridge_listing_id,mls_number,status,entry_timestamp,address,brokerage_id,brokerage_name,broker_id,broker_name,first_processed_at,processing_status)
                VALUES('DEMO-MEMBER','Cornerstone',?,?,?,?,?,?,?,?,?,?,?)""", ("previous-audit", "PREVIOUS", "Active", previous_at, "Previous Example Avenue",
                                                "office-a", "Brokerage office-a", "broker-a", "Broker broker-a", previous_at, "email_sent")).lastrowid
            db.execute("""INSERT INTO audits(listing_id,selected_at,brokerage_id,brokerage_name,broker_id,broker_name,agent_name,intended_to,intended_cc,actual_recipients,test_mode,email_status,selection_metadata)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (old, previous_at, "office-a", "Brokerage office-a", "broker-a", "Broker broker-a",
                                                      "Previous Agent", "[]", "[]", "[]", 1, "email_sent", "{}"))
            db.commit()
        rows = [
            _listing("DEMO-A", "office-a", "broker-a", now - timedelta(hours=2)),
            _listing("DEMO-B", "office-b", "broker-b", now - timedelta(hours=1)),
            _listing("DEMO-OLD", "office-c", "broker-c", now - timedelta(hours=30)),
            _listing("DEMO-INACTIVE", "office-d", "broker-d", now - timedelta(hours=1), status="Pending"),
        ]
        sender = DemoSender()
        first = run_job(demo_config, DemoReso(rows), DemoLottery(), now, sender=sender)
        with connect(demo_config.database_path) as db:
            selected = db.execute("""SELECT a.id, a.email_status, l.mls_number, a.intended_to, a.intended_cc, a.actual_recipients
                FROM audits a JOIN listings l ON l.id=a.listing_id WHERE l.mls_number='DEMO-B'""").fetchone()
            first_status = selected["email_status"]
            considered = {row["mls_number"]: row["processing_status"] for row in db.execute("SELECT mls_number,processing_status FROM listings WHERE mls_number LIKE 'DEMO-%'")}
        second = run_job(demo_config, DemoReso(rows), DemoLottery(), now, sender=sender)
        retried = deliver_audit(demo_config, selected["id"], retry=True, sender=sender, now=now)
        with connect(demo_config.database_path) as db:
            final_status = db.execute("SELECT email_status FROM audits WHERE id=?", (selected["id"],)).fetchone()[0]
            attempt_count = db.execute("SELECT count(*) FROM email_attempts WHERE audit_id=?", (selected["id"],)).fetchone()[0]
            selected_count = db.execute("SELECT count(*) FROM audits WHERE listing_id IN (SELECT id FROM listings WHERE mls_number LIKE 'DEMO-%')").fetchone()[0]
        return {
            "mode": "isolated synthetic test; no MLS API or SendGrid network call",
            "bridge_rows": len(rows), "server_filtered_rows": first["fetched"],
            "excluded_old_or_inactive": ["DEMO-OLD", "DEMO-INACTIVE"],
            "first_run": first, "candidate_statuses_after_first_run": considered,
            "cooldown_blocked_brokerage": "office-a",
            "selected_listing": selected["mls_number"], "audit_records_for_new_listings": selected_count,
            "intended_to": json.loads(selected["intended_to"]), "intended_cc": json.loads(selected["intended_cc"]),
            "actual_recipients": json.loads(selected["actual_recipients"]),
            "first_email_status": first_status, "second_run": second,
            "manual_retry_succeeded": retried, "final_email_status": final_status,
            "send_attempts": attempt_count, "simulated_message_id": "SIMULATED-MESSAGE-ID",
        }


# Compatibility for callers using the original synthetic client name.
DemoBridge = DemoReso
