"""Disposable PR-preview fixtures; all delivery records are simulated."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .database import connect
from .job import run_job
from .simulation import DemoBridge, _listing


def seed_development(config):
    now = datetime.now(timezone.utc)
    rows = [_listing(f"DEMO-{number}", f"office-{number}", f"broker-{number}",
                     now - timedelta(hours=number)) for number in range(1, 5)]
    # The regular workflow builds fixture records with a local stub. This config
    # never leaves this function and has no credentials or live intake client.
    demo = replace(config, env="test", email_enabled=True, rate=1)
    run_job(demo, client=DemoBridge(rows), now=now, sender=lambda *args: "SIMULATED-NOT-SENT")
    with connect(config.database_path) as db:
        stamp = now.isoformat(timespec="seconds")
        db.execute("INSERT INTO audit_reviewers(name,created_at,updated_at) VALUES(?,?,?)",
                   ("Demo Reviewer", stamp, stamp))
        db.commit()
