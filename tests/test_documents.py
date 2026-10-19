"""Synthetic paperwork ingestion, denied paths and complete recovery."""
import io
import re
import shutil
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from pypdf import PdfWriter
from werkzeug.datastructures import FileStorage

import test_foundation as foundation
from audit_app.application import create_app
from audit_app.backup import backup, restore, stage_restore, restore_details, validate
from audit_app.database import connect, init_db
from audit_app.documents import document_directory, read_document, upload_document


def pdf(pages=2, encrypted=False):
    output = io.BytesIO()
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    if encrypted:
        writer.encrypt('synthetic-password')
    writer.write(output)
    return output.getvalue()


class DocumentTests(unittest.TestCase):
    token = foundation.FoundationTests.token
    get = foundation.FoundationTests.get
    post = foundation.FoundationTests.post

    @classmethod
    def setUpClass(cls):
        foundation.FoundationTests.setUpClass.__func__(cls)

    def setUp(self):
        foundation.FoundationTests.setUp(self)
        foundation.FoundationTests.seed_asana_audits(self)
        with connect(self.config.database_path) as db:
            db.execute("UPDATE listings SET agent_membership_class='MEMBER'")
            db.execute("UPDATE audits SET email_sent_at='2026-10-01T12:00:00+00:00',response_due_at='2026-10-02T12:00:00+00:00'")
            db.commit()

    def upload(self, content=None, audit=3, role='admin', Origin=None, **values):
        return self.post(f'/audits/{audit}/documents',
                         {'document': (io.BytesIO(pdf() if content is None else content), '../../seller.pdf'), **values},
                         page=f'/?tab=outcome&id={audit}', role=role,
                         **({'Origin': Origin} if Origin else {}))

    def test_upload_pages_duplicate_download_and_no_workflow_changes(self):
        self.assertEqual(self.upload(role='reviewer').status_code, 303)
        self.assertEqual(self.upload(role='reviewer').status_code, 303)
        with connect(self.config.database_path) as db:
            row = db.execute('SELECT * FROM audit_documents').fetchone()
            self.assertEqual(db.execute('SELECT count(*) FROM audit_documents').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM document_pages').fetchone()[0], 2)
            self.assertEqual(row['filename'], 'seller.pdf')
            self.assertEqual(row['uploaded_by'], 'reviewer@example.com')
            self.assertEqual(row['test_mode'], 1)
            audit = db.execute('SELECT outcome,response_received_at,response_due_at FROM audits WHERE id=3').fetchone()
            self.assertEqual(tuple(audit), (None, None, '2026-10-02T12:00:00+00:00'))
            self.assertEqual(db.execute("SELECT count(*) FROM activity_events WHERE action='document.uploaded'").fetchone()[0], 1)
        path = document_directory(self.config) / row['storage_name']
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        response = self.get(f'/audits/3/documents/{row["id"]}', role='reviewer')
        self.assertEqual(response.data, pdf())
        self.assertIn('attachment;', response.headers['Content-Disposition'])
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.get(f'/audits/2/documents/{row["id"]}').status_code, 404)
        self.assertIn('seller.pdf', self.get('/?tab=outcome&id=3').text)

    def test_rejects_scope_identity_origin_csrf_revision_and_extra_files(self):
        self.assertEqual(self.upload(token='wrong').status_code, 403)
        self.assertEqual(self.upload(Origin='https://evil.invalid').status_code, 403)
        self.assertEqual(self.upload(revision='999').status_code, 409)
        self.assertEqual(self.client.post('/audits/3/documents', data={}).status_code, 401)
        with connect(self.config.database_path) as db:
            db.execute("UPDATE listings SET originating_system_name='OTHER' WHERE id=2")
            db.commit()
        self.assertEqual(self.upload(audit=2).status_code, 400)
        self.assertEqual(self.upload(audit=999).status_code, 400)
        self.assertEqual(self.upload(extra=(io.BytesIO(pdf()), 'extra.pdf')).status_code, 413)
        with connect(self.config.database_path) as db:
            db.execute("UPDATE app_users SET active=0 WHERE role='reviewer'")
            db.commit()
        self.assertEqual(self.upload(role='reviewer').status_code, 403)
        self.assertEqual(list(document_directory(self.config).glob('*')), [])

    def test_malformed_password_oversized_and_page_limit_rejected(self):
        for content in (b'', b'<html>not a PDF</html>', b'%PDF-1.7\nbroken', pdf(encrypted=True), pdf(101)):
            self.assertEqual(self.upload(content).status_code, 400)
        with patch('audit_app.documents.MAX_DOCUMENT_BYTES', 50):
            self.assertEqual(self.upload().status_code, 400)
        with patch('audit_app.application.MAX_DOCUMENT_BYTES', 1):
            self.assertEqual(self.upload(b'x' * (300 * 1024)).status_code, 413)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM audit_documents').fetchone()[0], 0)

    def test_images_completed_audits_pending_audits_and_rollback(self):
        for audit, kind in ((1, 'PNG'), (2, 'JPEG')):
            output = io.BytesIO()
            Image.new('RGB', (20, 20), 'white').save(output, format=kind)
            self.assertEqual(self.upload(output.getvalue(), audit=audit).status_code, 303)
        with connect(self.config.database_path) as db:
            db.execute("UPDATE audits SET email_status='email_pending' WHERE id=3")
            db.commit()
        self.assertIn('Attach paperwork', self.get('/?tab=outcome&id=3').text)
        before = list(document_directory(self.config).iterdir())
        with patch('audit_app.documents.event', side_effect=RuntimeError('synthetic failure')):
            self.assertEqual(self.upload().status_code, 500)
        self.assertEqual(list(document_directory(self.config).iterdir()), before)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM audit_documents').fetchone()[0], 2)

    def test_pdf_permissions_without_user_password_are_accepted_unchanged(self):
        output = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.encrypt('', owner_password='synthetic-owner-password')
        writer.write(output)
        original = output.getvalue()
        self.assertEqual(self.upload(original).status_code, 303)
        self.assertEqual(read_document(self.config, 3, 1)[1], original)

    def test_complete_backup_staging_and_restore_to_fresh_volume(self):
        self.assertEqual(self.upload().status_code, 303)
        snapshot = Path(self.temp.name) / 'complete.sqlite3'
        backup(self.config, snapshot)
        self.assertEqual(validate(snapshot), 10)
        fresh = replace(self.config, database_path=str(Path(self.temp.name) / 'fresh' / 'audit.sqlite3'))
        init_db(fresh.database_path)
        identifier = stage_restore(fresh, io.BytesIO(snapshot.read_bytes()))
        self.assertFalse(document_directory(fresh).exists())
        restore(fresh, restore_details(fresh, identifier)['path'], 'RESTORE STOPPED AUDITDESK')
        metadata, content = read_document(fresh, 3, 1)
        self.assertEqual(content, pdf())
        with connect(fresh.database_path) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='backup_document_payloads'").fetchone())
            self.assertEqual(db.execute('SELECT count(*) FROM document_pages').fetchone()[0], 2)
        backup(fresh, Path(self.temp.name) / 'again.sqlite3')
        self.assertEqual((document_directory(fresh) / metadata['storage_name']).stat().st_mode & 0o777, 0o600)

    def test_incomplete_tampered_backup_and_missing_original_rejected(self):
        self.upload()
        snapshot = Path(self.temp.name) / 'tampered.sqlite3'
        backup(self.config, snapshot)
        with connect(snapshot) as db:
            db.execute("UPDATE backup_document_payloads SET content=x'00'")
            db.commit()
        with self.assertRaises(ValueError):
            stage_restore(self.config, io.BytesIO(snapshot.read_bytes()))
        with connect(snapshot) as db:
            db.execute('DROP TABLE backup_document_payloads')
            db.commit()
        with self.assertRaises(ValueError):
            validate(snapshot)
        shutil.rmtree(document_directory(self.config))
        target = Path(self.temp.name) / 'incomplete.sqlite3'
        with self.assertRaises(OSError):
            backup(self.config, target)
        self.assertFalse(target.exists())

    def test_version9_migration_preserves_audits_and_is_idempotent(self):
        with connect(self.config.database_path) as db:
            db.execute('DROP TABLE document_pages')
            db.execute('DROP TABLE audit_documents')
            db.execute('PRAGMA user_version=9')
            db.commit()
        init_db(self.config.database_path)
        init_db(self.config.database_path)
        with connect(self.config.database_path) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 10)
            self.assertEqual(db.execute('SELECT count(*) FROM audits').fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT count(*) FROM audit_documents').fetchone()[0], 0)
            self.assertIsNone(db.execute('PRAGMA foreign_key_check').fetchone())

    def test_development_rejects_real_upload_and_only_stores_generated_sample(self):
        app = create_app(replace(self.config, env='development'))
        self.addCleanup(app.extensions['auditdesk_sandbox'].cleanup)
        client = app.test_client()
        sandbox = app.extensions['auditdesk_config']
        with connect(sandbox.database_path) as db:
            audit = db.execute('SELECT id FROM audits LIMIT 1').fetchone()[0]
        page = client.get(f'/?tab=outcome&id={audit}').text
        self.assertIn('Attach synthetic sample', page)
        self.assertNotIn('type="file"', page)
        token = re.search(r'name="token" value="([^"]+)"', page)[1]
        revision = re.search(r'name="revision" value="(\d+)"', page)[1]
        response = client.post(f'/audits/{audit}/documents', data={'document': (io.BytesIO(pdf()), 'real.pdf')})
        self.assertEqual(response.status_code, 400)
        response = client.post(f'/audits/{audit}/documents/sample', data={'token': token, 'revision': revision}, headers={'Origin': sandbox.public_url})
        self.assertEqual(response.status_code, 303)
        with connect(sandbox.database_path) as db:
            self.assertEqual(db.execute('SELECT source FROM audit_documents').fetchone()[0], 'synthetic')
        with self.assertRaises(ValueError):
            upload_document(sandbox, audit, FileStorage(stream=io.BytesIO(pdf()), filename='real.pdf'))
        self.assertEqual(self.post('/audits/3/documents/sample', {}).status_code, 400)
