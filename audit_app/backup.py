"""Consistent online backup and validated offline restore."""
from contextlib import closing
from filelock import FileLock, Timeout
import os
import hashlib
import re
import secrets
import shutil
import sqlite3
import tempfile
from pathlib import Path

from .database import SCHEMA_VERSION, connect, init_db
from .job import job_lock


MAX_BACKUP_BYTES = 512 * 1024 * 1024


def validate(path):
    try:
        with open(path, 'rb') as source:
            if source.read(16) != b'SQLite format 3\x00':
                raise ValueError('Choose an AuditDesk SQLite backup file.')
        with closing(sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)) as db:
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('PRAGMA query_only=ON')
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("Backup failed database integrity checks")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in range(SCHEMA_VERSION + 1):
                raise ValueError("Backup schema is unsupported")
            required = {
                'listings': 'id,bridge_listing_id,mls_number,status,entry_timestamp,address,first_processed_at,processing_status',
                'audits': 'id,listing_id,selected_at,intended_to,intended_cc,actual_recipients,test_mode,email_status,selection_metadata',
                'runs': 'id,started_at,finished_at,status,fetched_count,new_count,selected_count,error',
                'email_attempts': 'id,audit_id,attempted_at,status,actual_recipients',
            }
            for table, columns in required.items():
                kind = db.execute('SELECT type FROM sqlite_master WHERE name=?', (table,)).fetchone()
                if not kind or kind[0] != 'table':
                    raise ValueError('Backup is missing AuditDesk tables.')
                db.execute(f'SELECT {columns} FROM {table} LIMIT 0')
            return version
    except (sqlite3.DatabaseError, OSError):
        raise ValueError('Backup could not be read as a valid AuditDesk database.') from None


def _check_administrator(config, path):
    with connect(path, check_revision=False) as db:
        if not config.bootstrap_admins and not db.execute("SELECT 1 FROM app_users WHERE role='admin' AND active=1").fetchone():
            raise ValueError("Restore would leave no administrator; configure BOOTSTRAP_ADMIN_EMAILS")


def restore_details(config, identifier):
    if not re.fullmatch(r'[0-9a-f]{32}', identifier):
        raise ValueError('Choose a validated restore upload.')
    path = Path(config.database_path).resolve().parent / 'restore-uploads' / (identifier + '.sqlite3')
    if not path.is_file() or path.is_symlink():
        raise ValueError('Restore upload is unavailable. Upload the backup again.')
    version = validate(path)
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as db:
        counts = {table: db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                  for table in ('listings', 'audits', 'runs')}
    with path.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    return {'id': identifier, 'path': str(path), 'schema': version, 'bytes': path.stat().st_size,
            'sha256': digest, **counts}


def stage_restore(config, stream):
    """Validate an upload and a migrated copy; never replace live state over HTTP."""
    if config.env == 'development':
        raise ValueError('Backup and restore are unavailable in the disposable development sandbox.')
    directory = Path(config.database_path).resolve().parent / 'restore-uploads'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    identifier = secrets.token_hex(16)
    path = directory / (identifier + '.sqlite3')
    try:
        with path.open('xb') as output:
            os.chmod(path, 0o600)
            size = 0
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BACKUP_BYTES:
                    raise ValueError('Choose a backup file no larger than 512 MiB.')
                output.write(chunk)
        validate(path)
        with tempfile.TemporaryDirectory(prefix='auditdesk-restore-check-') as temporary:
            prepared = Path(temporary) / 'prepared.sqlite3'
            shutil.copyfile(path, prepared)
            os.chmod(prepared, 0o600)
            # This is an isolated copy, not the live HTTP mutation database.
            init_db(prepared, check_revision=False)
            validate(prepared)
            _check_administrator(config, prepared)
        details = restore_details(config, identifier)
        from .security import event
        with connect(config.database_path) as db:
            event(db, 'backup.restore_staged', identifier, f"schema {details['schema']}; SHA-256 {details['sha256']}")
            db.commit()
        return identifier
    except Exception:
        path.unlink(missing_ok=True)
        raise


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
    if source == live or not source.is_file() or source.stat().st_size > MAX_BACKUP_BYTES:
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
                _check_administrator(config, temp)
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
