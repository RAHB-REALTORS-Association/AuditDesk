# AuditDesk deployment

See [Configuration](CONFIGURATION.md) for environment variables, [Architecture](ARCHITECTURE.md) for module boundaries, and [Contributing](../CONTRIBUTING.md) for local development. Gunicorn reads runtime environment variables directly; the CLI also loads `.env` from the working directory.

## Architecture and identity boundary

One Python application serves the existing audit workflow through Flask/Gunicorn. SQLite, application users/roles, templates, and append-only activity records live at `/app/data/audit.sqlite3`. There is one Gunicorn worker with four threads and at most one application instance per volume. File locks protect the scheduler and prevent two service processes from owning the database. Locks work across macOS, Linux, and Windows; the deployment container runs Linux.

Cloudflare Access authenticates the person. AuditDesk validates the RS256 signature against the configured team's JWKS, issuer, application audience, expiry, issued-at time, subject, and email. No unsigned email or role header is trusted. Service tokens without person identity are not supported. An invited email is bound to its verified subject on first use; a changed email or different subject requires IT intervention rather than silently transferring access.

Application roles are read from SQLite on every request. Unknown and disabled people are denied even when Access permits them. Bootstrap emails are active IT administrators and cannot be demoted through the UI. Remove an address from the runtime bootstrap list first if it should become an ordinary managed account. There is no public registration.

See [Cloudflare JWT validation](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/) and [Coolify persistent storage](https://coolify.io/docs/applications/configuration/persistent-storage).

## Disposable PR previews

Use the preview template `auditdesk-pr{{pr_id}}.oncornerstone.app`. Preview runtime variables must use `APP_ENV=development`, `PUBLIC_BASE_URL=auto`, `APP_HOST=0.0.0.0`, and `APP_PORT=8765`. `auto` reads Coolify's generated per-PR `COOLIFY_URL` and selects the HTTPS browser origin for Cloudflare's edge TLS. Keep real integration keys out of preview environments. These settings belong to Coolify's separate preview-variable group; staging remains `APP_ENV=test`.

Development opens with a fixed demo administrator and four synthetic audit requests, whose sent records are simulated. It never validates Access identities, queries Bridge, starts the scheduler, or sends request/failure mail. Keys, recipient addresses, enable flags and configured database/session paths are ignored. CSRF, origin, host, input and stale-form checks still apply. Each process creates a fresh temporary SQLite database; edits disappear on restart. Backup/restore and live-data maintenance commands are unavailable in this mode. `main.py run` runs the isolated synthetic simulation.

No persistent volume is needed for previews. The Dockerfile no longer declares an anonymous volume; staging/production retain their explicit Coolify `/app/data` mount. Route preview hostnames through the existing Cloudflare tunnel/ingress and edge wildcard certificate. Cloudflare Access protection for the real staging hostname stays in place.

## First staging deployment

The existing Coolify application is recorded in [historical project context](PROJECT_CONTEXT.md); verify the current application before deployment and do not create a duplicate. Initial setup used a fresh installation, not an import of the original developer's computer database.

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

5. Configure internal HTTP health check `GET /healthz` on port `8765`. This endpoint reveals only readiness/schema and does not require login. Probe inside the container rather than creating an Access bypass. The image includes curl because Coolify v4.0.0-beta.397 overrides the Dockerfile's Python probe with its dashboard HTTP probe; without curl the otherwise ready container becomes unhealthy and Traefik returns 404.
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

Bridge reads retry transient failures up to six attempts. Rate limiting honors numeric or HTTP-date Retry-After headers; without a valid header, waits start at one minute and increase. A requested delay above 15 minutes stops intake for a later retry instead of requesting early. Retry logs contain status and delay, never credentials or request URLs.

The in-process scheduler checks every five minutes after 08:00 in APP_TIMEZONE. Successful intake suppresses another scheduled intake that day; failures can retry. After a successful intake, subsequent runs query from at least its start boundary, covering outages while database uniqueness prevents reselection. The Active-only rule remains: a listing inactive at recovery time is not included. First installation only imports the configured rolling window.

Committed pending request emails resume when the office calendar permits both sending and the 24-hour deadline. The enabled scheduler checks the queue every five minutes, even when daily intake is already complete. Configure hours and explicit holiday/closure dates under Manage → Hours & holidays before enabling delivery. Interrupted `email_sending` attempts become `email_unknown` at service startup and are never automatically resent. Check SendGrid before resolving an unknown state. Failed notices can be resumed manually from the audit queue. Test audits cannot send production requests or failure notices after an environment change. Turning EMAIL_ENABLED off prevents all sender calls, including retries.

## Backup and restore

Backups contain private listing/contact data and application access grants. Restrict access and use encrypted storage outside the application host. Configure an operator-owned daily backup and retention schedule before production use; a persistent volume is not a backup.

Administrators can download a consistent database snapshot from **Manage → Recovery**. The same page accepts a backup up to 512 MiB, checks its integrity, schema, migration, and administrator access, then stages the original file privately and displays its hash, record counts, and the exact offline restore command. Uploading never replaces live records. Remove staged files after recovery.

Online backup also uses SQLite's consistent backup API from the CLI:

```sh
python main.py backup --output /app/data/backups/auditdesk-YYYYMMDD.sqlite3
```

The destination must be new. Backups are created with mode 0600. Copy the resulting artifact into the organization's backup system. Verify periodic restore drills with a separate instance and outbound integrations disabled.

For restore, stop the service first and run the CLI in a one-off container with the same volume and runtime identity configuration:

```sh
python main.py restore --input /app/data/backups/auditdesk-YYYYMMDD.sqlite3 --confirm 'RESTORE STOPPED AUDITDESK'
```

Restore refuses a running service, checks integrity, foreign keys and supported schema, prepares a temporary database, preserves administrator access, saves a pre-restore backup, and replaces the database atomically. Files above 512 MiB are rejected. Resume the service and verify login and record counts. Restore rolls back all data to the backup time, so newer changes can be lost.

Schema version 8 initializes a fresh database transactionally and accepts baseline/version-1/version-2/version-3/version-4/version-5/version-6/version-7 databases. Version 2 discarded the development-only audit roster and moved assignments to application user IDs; version 3 adds the managed brokerage cooldown while preserving the saved selection percentage and audit results. Version 4 adds managed broker cooldown and listing-window settings without changing saved rates, office cooldowns, assignments, or results. Version 5 adds an optional Asana task URL to audits without changing existing results or settings. Version 6 stores listing board provenance. Version 7 adds nullable `agent_mls_id` for explicit nonmember exclusions; historical records remain ineligible until both fields are backfilled. Version 8 adds persisted office hours/holidays and nullable response deadline/receipt fields, preserving existing send times and results. New deadlines are stored at accepted send time; legacy deadlines use send time plus 24 hours. Until a manager saves a cooldown, the configured `BROKERAGE_COOLDOWN_DAYS` remains effective. Back up staging before upgrading. Newer unknown schema versions are rejected; older version-2/version-3/version-4/version-5/version-6/version-7 images cannot open version-8 databases, so rollback requires a compatible image or the pre-upgrade backup.

## Releases and rollback

CI runs tests and builds/smoke-tests a container. Version tags publish an image to GHCR; pin an immutable digest for production. Staging can build the exact reviewed Git commit through Coolify's existing Git integration. Keep release notes and the previous image digest.

Before upgrading, take a verified backup and inspect schema compatibility. Stop/start the single instance while preserving the mount. A code rollback is safe only if the older image supports the current schema. Prefer forward corrections; do not restore data just to revert code.

## Deliberate scope

This remains a focused audit application. No document upload, directory synchronization, publication scheduling, multi-tenancy, or distributed queues were added. Template Save deliberately changes future email wording; previews do not send. Recorded outcomes remain single-assignment. Template publication/version history remains outside this foundation. Restore uploads only validate and stage files; database replacement remains a stopped-service CLI operation. The email editor script is served from static assets; legacy inline event handlers still require CSP `unsafe-inline`; external scripts and frames are blocked. The shared record-revision check is conservative: another user's change can require a refresh even on a different record.

## Verify listing eligibility when upgrading staging data

Take a backup, deploy schema 7 with the existing persistent volume, and run this command in the application container:

```sh
python main.py backfill-listing-boards
```

This reads `ListingKey`, `OriginatingSystemName`, and `ListAgentMlsId` from Bridge for historical records lacking board or agent MLS provenance, in bounded batches. It records the reported board, agent MLS identifier, and activity events; it does not run intake, select audits, or send email. It can be resumed after an interrupted batch. Other-board and `NONMEM` records stay stored but excluded from audit actions and listing/audit/report views. Missing Bridge records remain unverified and blocked; never label them Cornerstone based on office names. Development rejects this live-data command.

The standard field map must include `Property.originating_system_name: OriginatingSystemName` and `Property.agent_mls_id: ListAgentMlsId`. Intake filters Bridge requests and checks each returned row locally before enriching or selecting it. Scheduling and retries cannot send audit requests for an unverified, nonmember, or other-board listing, including pre-upgrade pending requests. No manual cleanup or deletion is required.
