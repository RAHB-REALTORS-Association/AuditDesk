"""Bounded local paperwork storage. No extraction or workflow side effects."""
import hashlib
import io
import os
import re
import secrets
import warnings
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
from pypdf import PdfReader
from werkzeug.utils import secure_filename

from .board_scope import require_cornerstone_audit
from .database import connect
from .security import event

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_PAGES = 100
MAX_DOCUMENTS = 50


def document_directory(config):
    return Path(config.database_path).resolve().parent / 'documents'


def document_path(directory, name):
    if not isinstance(name, str) or not re.fullmatch(r'[0-9a-f]{32}\.(pdf|png|jpg)', name):
        raise ValueError('Document storage reference is invalid.')
    path = Path(directory) / name
    if path.is_symlink():
        raise ValueError('Document storage reference is invalid.')
    return path


def inspect_document(content):
    """Validate structure and count pages; preserve the original uploaded bytes."""
    if not content or len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError('Choose a PDF, PNG or JPEG file no larger than 20 MiB.')
    try:
        if content.startswith(b'%PDF-'):
            reader = PdfReader(io.BytesIO(content), strict=True)
            if reader.is_encrypted and not reader.decrypt(''):
                raise ValueError('Remove the PDF password before uploading.')
            count = len(reader.pages)
            if not 1 <= count <= MAX_PAGES:
                raise ValueError('Choose a PDF containing between 1 and 100 pages.')
            return 'application/pdf', 'pdf', count
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format not in {'PNG', 'JPEG'}:
                    raise ValueError('Choose a PDF, PNG or JPEG file.')
                kind = image.format
                image.verify()
            # Verify encoded structure and decoded pixel data independently.
            with Image.open(io.BytesIO(content)) as image:
                image.load()
        return ('image/png', 'png', 1) if kind == 'PNG' else ('image/jpeg', 'jpg', 1)
    except ValueError:
        raise
    except Exception:
        raise ValueError('The document could not be read. Choose a valid, unencrypted PDF, PNG or JPEG.') from None


def upload_document(config, audit_id, upload):
    if config.env == 'development':
        raise ValueError('Real paperwork cannot be uploaded in the development sandbox. Use the synthetic sample.')
    # Check scope/revision before reading the file. Recheck inside the write transaction.
    with connect(config.database_path) as db:
        require_cornerstone_audit(db, audit_id)
    content = upload.stream.read(MAX_DOCUMENT_BYTES + 1)
    return _store(config, audit_id, content, upload.filename, 'reviewer_upload')


def attach_sample(config, audit_id):
    if config.env != 'development':
        raise ValueError('Synthetic samples are only available in development.')
    from reportlab.pdfgen.canvas import Canvas
    output = io.BytesIO()
    canvas = Canvas(output, invariant=True)
    canvas.drawString(72, 720, 'SYNTHETIC DEMO PAPERWORK - NOT AN OREA FORM')
    canvas.drawString(72, 690, 'Example Seller / 123 Demo Street / $500,000')
    canvas.save()
    return _store(config, audit_id, output.getvalue(), 'synthetic-paperwork.pdf', 'synthetic')


def _store(config, audit_id, content, filename, source):
    media_type, extension, pages = inspect_document(content)
    filename = secure_filename(filename or '')[:180] or ('paperwork.' + extension)
    digest = hashlib.sha256(content).hexdigest()
    directory = document_directory(config)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = document_path(directory, secrets.token_hex(16) + '.' + extension)
    created = False
    try:
        with connect(config.database_path) as db:
            if not db.in_transaction:
                db.execute('BEGIN IMMEDIATE')
            require_cornerstone_audit(db, audit_id)
            duplicate = db.execute('SELECT id FROM audit_documents WHERE audit_id=? AND sha256=?', (audit_id, digest)).fetchone()
            if duplicate:
                return duplicate['id'], False
            if db.execute('SELECT count(*) FROM audit_documents WHERE audit_id=?', (audit_id,)).fetchone()[0] >= MAX_DOCUMENTS:
                raise ValueError('This audit already has 50 documents. Contact an administrator.')
            from flask import g, has_request_context
            actor = g.principal.email if has_request_context() else 'system'
            audit = db.execute('SELECT test_mode FROM audits WHERE id=?', (audit_id,)).fetchone()
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            with os.fdopen(fd, 'wb') as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            cursor = db.execute('''INSERT INTO audit_documents
                (audit_id,storage_name,filename,media_type,byte_count,sha256,page_count,uploaded_at,uploaded_by,source,test_mode)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (audit_id, path.name, filename, media_type, len(content), digest, pages,
                    datetime.now(timezone.utc).isoformat(), actor, source, audit['test_mode']))
            document_id = cursor.lastrowid
            db.executemany('INSERT INTO document_pages(document_id,page_number) VALUES(?,?)',
                           [(document_id, page) for page in range(1, pages + 1)])
            event(db, 'document.uploaded', audit_id, f'document {document_id}; {pages} pages; {len(content)} bytes; {source}')
            db.commit()
        return document_id, True
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise


def read_document(config, audit_id, document_id):
    with connect(config.database_path) as db:
        require_cornerstone_audit(db, audit_id)
        row = db.execute('SELECT * FROM audit_documents WHERE id=? AND audit_id=?', (document_id, audit_id)).fetchone()
    if not row:
        from werkzeug.exceptions import NotFound
        raise NotFound('Document not found on this audit.')
    path = document_path(document_directory(config), row['storage_name'])
    try:
        content = path.read_bytes()
    except OSError:
        raise ValueError('Document file is unavailable. Contact an administrator to restore a complete backup.') from None
    if len(content) != row['byte_count'] or hashlib.sha256(content).hexdigest() != row['sha256']:
        raise ValueError('Document integrity check failed. Contact an administrator.')
    return dict(row), content
