"""Disposable PR-preview fixtures; all delivery records are simulated."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .database import connect
from .job import run_job
from .simulation import DemoReso, _listing


def seed_development(config):
    now = datetime.now(timezone.utc)
    rows = [_listing(f"DEMO-{number}", f"office-{number}", f"broker-{number}",
                     now - timedelta(hours=number)) for number in range(1, 5)]
    # The regular workflow builds fixture records with a local stub. This config
    # never leaves this function and has no credentials or live intake client.
    demo = replace(config, env="test", email_enabled=True, rate=1)
    run_job(demo, client=DemoReso(rows), now=now, sender=lambda *args: "SIMULATED-NOT-SENT")
    with connect(config.database_path) as db:
        # Demonstrate distinct response states without touching live integrations.
        actual_now = datetime.now(timezone.utc)
        for audit_id, elapsed in ((1, 26), (2, 22), (3, 10)):
            sent = actual_now - timedelta(hours=elapsed)
            db.execute("UPDATE audits SET email_status='email_sent',email_sent_at=?,response_due_at=? WHERE id=?",
                       (sent.isoformat(), (sent + timedelta(hours=24)).isoformat(), audit_id))
            db.execute("UPDATE listings SET processing_status='email_sent' WHERE id=(SELECT listing_id FROM audits WHERE id=?)", (audit_id,))
        db.execute('UPDATE audits SET response_received_at=? WHERE id=3', ((actual_now-timedelta(hours=2)).isoformat(),))
        db.execute("UPDATE audits SET email_status='email_pending',email_sent_at=NULL,response_due_at=NULL WHERE id=4")
        db.execute("UPDATE listings SET processing_status='email_pending' WHERE id=(SELECT listing_id FROM audits WHERE id=4)")
        db.execute("INSERT INTO app_users(email,display_name,role) VALUES(?,?,?)",
                   ("reviewer@example.invalid", "Demo Reviewer", "reviewer"))
        db.commit()
