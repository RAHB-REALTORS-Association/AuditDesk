# AuditDesk deployment

## Architecture and identity boundary

One Python application serves the existing audit workflow through Flask/Gunicorn. SQLite, application users/roles, templates, and append-only activity records live at `/app/data/audit.sqlite3`. There is one Gunicorn worker with four threads and at most one application instance per volume. File locks protect the scheduler and prevent two service processes from owning the database. Locks work across macOS, Linux, and Windows; the deployment container runs Linux.

Cloudflare Access authenticates the person. AuditDesk validates the RS256 signature against the configured team's JWKS, issuer, application audience, expiry, issued-at time, subject, and email. No unsigned email or role header is trusted. Service tokens without person identity are not supported. An invited email is bound to its verified subject on first use; a changed email or different subject requires IT intervention rather than silently transferring access.

Application roles are read from SQLite on every request. Unknown and disabled people are denied even when Access permits them. Bootstrap emails are active IT administrators and cannot be demoted through the UI. Remove an address from the runtime bootstrap list first if it should become an ordinary managed account. There is no public registration.

See [Cloudflare JWT validation](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/) and [Coolify persistent storage](https://coolify.io/docs/applications/configuration/persistent-storage).

## First staging deployment

The existing Coolify application is recorded in `AGENTS.md`; do not create a duplicate. This is a fresh installation, not an import of the original developer's computer database.

1. Build and test the exact commit; use Dockerfile build pack, `/Dockerfile`, base directory `/`, and exposed port `8765`. Clear the old `python3 main.py serve` start-command override so Docker's Gunicorn command is used. Do not map this port publicly.
2. Mount a named persistent volume at `/app/data`. It must be writable by UID/GID `10001`. Never reuse an unrelated application volume. Do not delete or detach it on upgrades.
3. Configure `https://auditdesk.oncornerstone.app` and TLS. DNS must route through Cloudflare to the existing Coolify ingress/tunnel. Protect the entire hostname with a self-hosted Access application. Do not add authentication bypass policies. Restrict the origin network to the trusted ingress where possible.
4. Use the following runtime configuration. Store Bridge and SendGrid keys as masked runtime-only values, never build arguments. AuditDesk creates its session signing key in `/app/data/session.key` with restricted permissions; an optional APP_SECRET_KEY overrides it.

| Variable | Staging value |
| --- | --- |
| APP_ENV | test |
| AUTH_MODE | cloudflare |
| PUBLIC_BASE_URL | https://auditdesk.oncornerstone.app |
| APP_HOST / APP_PORT | 0.0.0.0 / 8765 |
| DATABASE_PATH | /app/data/audit.sqlite3 |
| APP_TIMEZONE | America/Toronto |
| CF_ACCESS_ISSUER | HTTPS origin of this account's Access team domain |
| CF_ACCESS_AUDIENCE | AUD of the AuditDesk Access application |
| APP_SECRET_KEY | Optional; omit to generate and persist `/app/data/session.key` automatically |
| BOOTSTRAP_ADMIN_EMAILS | justin.hayes@cornerstone.inc |
| EMAIL_ENABLED | false |
| SCHEDULER_ENABLED | false |

5. Configure internal HTTP health check `GET /healthz` on port `8765`. This endpoint reveals only readiness/schema and does not require login. Probe inside the container rather than creating an Access bypass.
6. Disable overlapping/rolling instances for this SQLite deployment. Stop the old instance before starting its replacement; brief upgrade downtime is deliberate. The database service lock rejects an overlapping worker.
7. Deploy the exact reviewed commit/image. Verify health, login, bootstrap Admin, denied uninvited identity, role restrictions, and persistence after restart. Confirm the mounted path.
8. Keep email/scheduler off for initial UI review. Configure Bridge, SendGrid, verified sender and test recipient separately. Enable email in `APP_ENV=test` only for an explicitly requested live integration test; only `ADMIN_EMAIL` receives messages. Production delivery requires a deliberate `APP_ENV=production` change.

All authenticated responses use `no-store`. Do not add a Cloudflare Cache Everything rule. Avoid trusting arbitrary forwarding headers. Origin validation compares the browser Origin to PUBLIC_BASE_URL exactly. Authenticated POSTs also require a signed-session CSRF token, a current record revision, bounded form data, and the action's capability. Rate limits use the validated subject.

## Build and verification

Use the existing `.venv` when present. For a new environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m unittest discover -s tests
docker build -t auditdesk:local .
python scripts/container_smoke.py auditdesk:local
```

Windows can use `.venv\Scripts\Activate.ps1`; Docker gives identical deployment behavior on all hosts. The disposable smoke test verifies non-root startup, readiness, unauthenticated rejection, and data persistence across restart. It does not query Bridge or send email.

## Schedule and delivery recovery

The in-process scheduler checks every five minutes after 08:00 in APP_TIMEZONE. Successful intake suppresses another scheduled intake that day; failures can retry. After a successful intake, subsequent runs query from at least its start boundary, covering outages while database uniqueness prevents reselection. The Active-only rule remains: a listing inactive at recovery time is not included. First installation only imports the configured rolling window.

Committed pending request emails resume on the next job. Interrupted `email_sending` attempts become `email_unknown` at service startup and are never automatically resent. Check SendGrid before resolving an unknown state. Failed notices can be resumed manually from the audit queue. Test audits cannot send production requests or failure notices after an environment change. Turning EMAIL_ENABLED off prevents all sender calls, including retries.

## Backup and restore

Backups contain private listing/contact data and application access grants. Restrict access and use encrypted storage outside the application host. Configure an operator-owned daily backup and retention schedule before production use; a persistent volume is not a backup.

Online backup uses SQLite's consistent backup API:

```sh
python main.py backup --output /app/data/backups/auditdesk-YYYYMMDD.sqlite3
```

The destination must be new. Backups are created with mode 0600. Copy the resulting artifact into the organization's backup system. Verify periodic restore drills with a separate instance and outbound integrations disabled.

For restore, stop the service first and run the CLI in a one-off container with the same volume and runtime identity configuration:

```sh
python main.py restore --input /app/data/backups/auditdesk-YYYYMMDD.sqlite3 --confirm 'RESTORE STOPPED AUDITDESK'
```

Restore refuses a running service, checks integrity, foreign keys and supported schema, prepares a temporary database, preserves administrator access, saves a pre-restore backup, and replaces the database atomically. Files above 512 MiB are rejected. Resume the service and verify login and record counts. Restore rolls back all data to the backup time, so newer changes can be lost.

Schema version 1 initializes a fresh database transactionally. Existing baseline schema is accepted by the tested initialization code, but no original installation data migration is required for this project. Newer unknown schema versions are rejected.

## Releases and rollback

CI runs tests and builds/smoke-tests a container. Version tags publish an image to GHCR; pin an immutable digest for production. Staging can build the exact reviewed Git commit through Coolify's existing Git integration. Keep release notes and the previous image digest.

Before upgrading, take a verified backup and inspect schema compatibility. Stop/start the single instance while preserving the mount. A code rollback is safe only if the older image supports the current schema. Prefer forward corrections; do not restore data just to revert code.

## Deliberate scope

This remains a focused audit application. No document upload, directory synchronization, publication scheduling, multi-tenancy, or distributed queues were added. Template Save deliberately changes future email wording; previews do not send. Recorded outcomes remain single-assignment. Template publication/version history and an in-app restore uploader are outside this foundation. Existing sanitized rich-email editor scripts still require CSP `unsafe-inline`; external scripts and frames are blocked. The shared record-revision check is conservative: another user's change can require a refresh even on a different record.
