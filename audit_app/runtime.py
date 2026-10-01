"""Single-instance scheduler and recovery. No external calls unless explicitly enabled."""
from filelock import FileLock, Timeout
import logging
import threading
from pathlib import Path

from .database import connect
from .job import job_lock, run_job


def start_runtime(config):
    handle = FileLock(str(Path(config.database_path).resolve()) + '.service.lock')
    try:
        handle.acquire(timeout=0)
    except Timeout:
        raise RuntimeError("AuditDesk requires one application instance per database") from None
    # Keep the service lock open for the process lifetime.
    with job_lock(config.database_path), connect(config.database_path) as db:
        for column in ("email_status", "failure_email_status"):
            db.execute(f"UPDATE audits SET {column}='email_unknown' WHERE {column}='email_sending'")
        for table in ("email_attempts", "failure_email_attempts"):
            db.execute(f"UPDATE {table} SET status='email_unknown',error='Application stopped before delivery was confirmed' WHERE status='started'")
        db.execute("UPDATE runs SET status='failed',error='Application stopped during intake' WHERE status='running'")
        db.commit()
    stop = threading.Event()
    def scheduler():
        while not stop.is_set():
            try:
                run_job(config, only_if_needed=True)
            except Exception as error:
                logging.getLogger("audit_app").error("scheduled_run_failed type=%s", type(error).__name__)
            stop.wait(300)
    if config.env != "development" and config.scheduler_enabled:
        threading.Thread(target=scheduler, daemon=True, name="auditdesk-scheduler").start()
    return handle, stop


def serve(config):
    """Start the local HTTP server with the same runtime as the deployment app."""
    from .application import create_app
    app = create_app(config)
    config = app.extensions["auditdesk_config"]
    app.extensions["auditdesk_runtime"] = start_runtime(config)
    app.run(host=config.host, port=config.port, debug=False)
