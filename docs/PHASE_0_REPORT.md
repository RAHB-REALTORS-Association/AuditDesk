# Phase 0: paperwork ingestion

2026-10-09 · Status: implemented, awaiting Justin's PR review. Phases 1 and 2 have not started.

## What changed

Reviewers, Audit managers and IT administrators can attach one PDF, PNG or JPEG at a time on an eligible audit's result page and download its original later. Schema 10 adds documents and page records. Original bytes live in a private directory beside SQLite, normally `/app/data/documents`; metadata records the audit, SHA-256, sanitized filename, page count, timestamp, uploader, source and audit test-mode provenance. Duplicate bytes on the same audit return the existing record. Completed and unsent audits can retain paperwork.

The routes preserve signed identity, capability, listing eligibility, origin, CSRF, rate limiting and activity-revision checks. A failed transaction removes the new file. Downloads are authenticated attachments with no-store and hash validation. Uploading does not record receipt, reset a deadline, record a result, send mail or contact Argus.

Development rejects real files before parsing multipart input and offers a server-generated synthetic sample in temporary storage. No real samples were committed or installed into a development database. No live MLS or mail integration was used for verification.

## Backup and deployment impact

The existing SQLite backup artifact now embeds referenced originals in a backup-only payload table. Export uses the consistent database snapshot, checks each immutable original, and fails if a file is missing/corrupt. Restore staging validates all references and hashes without writing live originals. Stopped-service restore writes private files before replacing SQLite, rejects conflicting originals, and removes the backup payload table from the restored database. Document-free historical backups remain supported. An ordinary database-only copy is insufficient once attachments exist.

Keep the existing single-worker deployment, UID/GID 10001 and persistent volume. Take a pre-upgrade backup and stop/start for schema 10; schema-9 rollback needs the pre-upgrade backup. No new integration keys or networking are required. The only added dependency is pure-Python `pypdf==6.20.0` for PDF structure/page validation; Pillow already ships transitively and validates images. No rendering, OCR or model runtimes run in AuditDesk.

## What was measured

- Local verification interpreter: `/Users/justinh/AuditDesk/Audit-App/.venv/bin/python`, Python 3.14.5. PDF parser: pypdf 6.20.0.
- Full regression suite: 159 tests passed, including nine focused document tests. Coverage includes uploads/downloads, per-page persistence, original-byte preservation, duplicate prevention, completed/unsent audits, missing identities, disabled roles, foreign/missing audits, CSRF/origin rejection, stale forms, multipart/size/page limits, password rejection, permissions-only encryption, images, transaction cleanup, migration from version 9, complete fresh-volume recovery and incomplete/tampered backups.
- Isolated `main.py simulate`: passed, with no MLS API or SendGrid calls.
- Docker build and repository container smoke: passed for non-root startup, health, identity denial, persistent restart including private synthetic paperwork and its metadata, report downloads, synthetic development and reset on restart. Dependency audit: no known vulnerabilities found.
- Browser: checked synthetic attachment on the result page; the link, confirmation and separate response/result controls appeared. The standard file-upload route is exercised with synthetic files in authenticated HTTP tests.
- Both supplied PDFs passed upload validation in memory; neither was stored as an attachment. Preliminary structure only: the ITSO residential property-information form has 10 pages and the member-full printout has 3; each has text on every page and no canonical AcroForm fields or page widgets. This is not a representative inventory or an extraction accuracy result.

## What surprised us and current limits

The supplied ITSO form uses permission encryption with an empty user password. Rejecting every encrypted PDF would reject useful readable forms, so the validator permits empty-password access and preserves original bytes, while rejecting files that require a user password. The form contains reference/conditional requirements as well as field labels; text availability alone does not establish that completed answers are present or reliably located.

Bounded intake defaults are 20 MiB per file, 100 pages and 50 documents per audit. Complete backups retain the existing 512 MiB restore limit. These are explicit spike limits, not volume measurements; a larger recovery format will be needed before attachments exceed that budget. Page rows currently identify source page numbers; Argus later owns rendering and extraction. Strict PDF validation may reject damaged files a viewer can repair. No automatic retention/deletion or orphan cleanup runs; a process crash between file write and database commit can leave an unreferenced private file. Browser attachment controls submit separately from an unsaved result form; attach paperwork before writing review issues.

## Inbound-email matching proposal — not implemented

1. Add a cryptographically random per-audit reply alias/token, with at least 128 bits of entropy, separate from browser CSRF/session keys. Persist its association and lifecycle; put the alias in the outgoing request's Reply-To after mail routing is approved. It must not authorize browser access or document downloads.
2. Receive messages through Justin's chosen mailbox reader or inbound-mail provider. Use a dedicated authenticated service boundary or polling adapter, isolated from browser identity routes. Do not expose an unsigned webhook or relax Cloudflare Access for staff routes. Provider, mailbox/domain and transport remain Justin's decisions.
3. Match a valid exact reply token to one current eligible audit. Invalid, conflicting or multiple tokens go to the staff queue. For tokenless replies only, an exact normalized MLS number can match when it identifies exactly one eligible, sent, open audit and there is no contradictory evidence; otherwise queue it. Never infer a match from an address, agent name, fuzzy subject or attachment content. The sender's address is context, not authority.
4. Deduplicate message ingestion by provider/mailbox plus stable message ID; deduplicate attachments by audit plus SHA-256. Reuse the same file validation and storage boundaries. Bound message/attachment counts and sizes. Do not fetch external links or active content. Unmatched/ambiguous messages remain private in a staff queue for explicit attachment decisions and activity history.
5. Optionally record response receipt using the original message receipt time after a successful unambiguous match. Preserve an existing manual receipt and the accepted send/deadline, validate time ordering, and never reopen a timer or create a result. Queue any correction for staff rather than silently changing an existing receipt. Keep this option off until Justin approves its semantics.
6. Test synthetic messages for retries, multiple tokens, duplicate attachments, forwarded replies, excluded/closed audits, missing MLS references, oversized messages, unauthorized submissions and immutable deadlines before enabling it.

## Decisions for review

Justin reviews this PR/report before Phases 1 and 2. Confirm the current reply mailbox and preferred inbound path, the token matching proposal (especially tokenless fallback), whether inbound receipt should be automatic, and paperwork/raw-output retention before enabling production collection or building inbound integration. The proposed first extraction targets remain the listing agreement and MLS data form; exact versions and field priorities belong to Phase 3. Actual sample collection and the agreed shadow period/label owners remain outstanding.
