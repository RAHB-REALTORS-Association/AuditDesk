"""Consistent online backup and validated offline restore."""
from contextlib import closing
from filelock import FileLock, Timeout
import os
import sqlite3
import tempfile
from pathlib import Path

from .database import SCHEMA_VERSION, connect, init_db
from .job import job_lock


def validate(path):
    with closing(sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Backup failed database integrity checks")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in range(SCHEMA_VERSION + 1):
            raise ValueError("Backup schema is unsupported")
        for table in ("listings", "audits", "runs", "email_attempts"):
            db.execute(f"SELECT id FROM {table} LIMIT 1")
        return version


def backup(config, destination):
    if config.env == "development":
        raise ValueError("Development data is disposable; backup is unavailable")
    target = Path(destination).resolve()
    if target == Path(config.database_path).resolve():
        raise ValueError("Backup destination cannot be the live database")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with connect(config.database_path) as source, closing(sqlite3.connect(target)) as output:
            source.backup(output)
        validate(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return str(target)


def restore(config, source, confirmation):
    if config.env == "development":
        raise ValueError("Development only permits synthetic fixtures; restore is unavailable")
    if confirmation != "RESTORE STOPPED AUDITDESK":
        raise ValueError("Stop AuditDesk and supply --confirm 'RESTORE STOPPED AUDITDESK'")
    source = Path(source).resolve()
    live = Path(config.database_path).resolve()
    if source == live or not source.is_file() or source.stat().st_size > 512 * 1024 * 1024:
        raise ValueError("Choose a backup file smaller than 512 MiB, separate from the live database")
    validate(source)
    live.parent.mkdir(parents=True, exist_ok=True)
    service = FileLock(str(live) + '.service.lock')
    try:
        service.acquire(timeout=0)
    except Timeout:
        raise ValueError("Stop the application before restoring") from None
    try:
        with job_lock(live):
            fd, temp = tempfile.mkstemp(prefix='.restore-', dir=live.parent)
            os.close(fd)
            try:
                with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as original, closing(sqlite3.connect(temp)) as output:
                    original.backup(output)
                init_db(temp)
                validate(temp)
                with connect(temp) as db:
                    if not config.bootstrap_admins and not db.execute("SELECT 1 FROM app_users WHERE role='admin' AND active=1").fetchone():
                        raise ValueError("Restore would leave no administrator; configure BOOTSTRAP_ADMIN_EMAILS")
                if live.exists():
                    from datetime import datetime, timezone
                    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                    backup(config, str(live) + '.before-restore-' + stamp)
                os.replace(temp, live)
            finally:
                Path(temp).unlink(missing_ok=True)
    finally:
        service.release()
    return str(live)
