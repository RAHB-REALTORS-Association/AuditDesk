# Historical project context

Archived from the initial contributor handoff on October 1, 2026. Commit IDs, deployed settings, earlier authorizations, and investigation priorities below describe that handoff; verify current runtime settings before operations. Current contributor rules are in [AGENTS.md](../AGENTS.md).

## Original working context

### Product and architecture

- Preserve the MLS paperwork audit workflow: Bridge intake, fair selection and cooldowns, SendGrid requests, reviewer assignments, results, and reports.
- Use the Cornerstone App Blueprint skill as the implementation guide, applying relevant sections without adding unrelated features.
- Deployment target is Coolify. In test/production, Cloudflare Access supplies authentication; the application validates its signed assertion and owns server-enforced authorization.
- Reviewer assignment and authenticated actor identity are separate concepts. Roles: Reviewer handles shared audits/results/retries/reports; Audit manager adds assignment/roster/templates/selection; IT administrator adds access management/activity export.

### Repository

- `origin`: https://github.com/RAHB-REALTORS-Association/AuditDesk.git
- `upstream`: https://github.com/ericmeek7/Audit-App.git
- Local `main` tracks `origin/main`. Initial reviewed baseline: `ac52c93`.
- Do not push changes to the original author's repository as part of organization development.

### Existing deployment target

- Coolify application: https://cloud.cornerstone.inc/project/v04ccsgos0skkokcocgogwg4/environment/a84sgoww00kc48owk8o44wwc/application/vw844cowkss0k8ccs8gww8ws
- User moved the skeleton to VPS06 on 2026-09-30; verified Primary Server VPS06, network coolify. The original application URL is obsolete.
- Team: Cornerstone.inc; project: Cornerstone Association; environment: staging; application: AuditDesk.
- User prefers Chrome debug for Coolify interaction. The authenticated Chrome RAHB profile was accessible through browser tooling during initial inspection.
- Staging was deployed on 2026-09-30 (2026-10-01 UTC) from commit `07f153b677ef19052cfb1a308fad72a7eebaa656` on `feat/coolify-access-foundation`. Dockerfile build pack, port 8765, no public port mapping, and the default image command replace the original Nixpacks skeleton.
- HTTPS hostname: `auditdesk.oncornerstone.app`; bootstrap administrator: `justin.hayes@cornerstone.inc`. Cloudflare Access uses the existing Staff policy; signed identity validation and roles live in the app. Real browser sign-in reached the administrator UI. Do not store secret values in source or documentation.
- Volume `vw844cowkss0k8ccs8gww8ws-audit-data` mounts at `/app/data`; database and session key were created with UID/GID 10001. Automatic deployments are disabled; PR previews are enabled with separate development variables. Consistent container names prevent overlapping SQLite owners.
- On 2026-09-30 the user authorized live-listing test emails exclusively to `justin.hayes@cornerstone.inc`, and daily scheduling around 08:00 America/Toronto. APP_ENV remains test; EMAIL_ENABLED and SCHEDULER_ENABLED are true. The first live intake failed on Bridge HTTP 429 before committing any listings or sending email; retry handling now honors Retry-After with bounded retries and a full-minute fallback. Runtime commit `33fac09bde01c502e23df68714413e94fb57a842` completed intake of 295 listings, selected 18, and SendGrid accepted 16 administrator-only messages. Two audits had recipient validation failures. Test mail is one message per selected listing, not a digest.
- Coolify v4.0.0-beta.397 overrides the image's health probe with curl/wget. The image includes curl; omitting it caused an unhealthy container and Traefik 404. The corrected proxy readiness request returns HTTP 200.
- User confirms the wildcard certificate is at Cloudflare's edge and their routing uses a tunnel. Leave shared certificate/zone/tunnel settings alone unless a specific routing fault is verified.
- Preserve persistent data through deployment changes; never recreate or detach storage as a routine upgrade step.

### Local verification

- Check the project and nearby task directories for an existing virtual environment before Python commands or dependency installation. Use it when present.
- Current environment: `.venv/bin/python` (Python 3.14.5 at initial review).
- Install declared dependencies with `.venv/bin/python -m pip install -r requirements.txt`.
- Run `.venv/bin/python -m unittest discover -s tests` from the repository root. Initial baseline: 36 passing tests.
- Tests use temporary databases and mocked integrations. Do not run live intake or send email merely to verify a code change.

### Initial review priorities

- Replace shared Basic Auth with validated Access identity and application permissions.
- Add authenticated mutation history, versioned migrations, backup/recovery, container packaging, health checks, and CI.
- Correct catch-up scheduling: failed runs currently suppress another attempt that day; rolling intake can miss listings after downtime.
- Prevent failed test request emails from becoming production sends after an environment change.
- Preserve existing duplicate prevention, uncertain-delivery handling, test-recipient substitution, and business-rule coverage.

### Foundation implementation

- Work branch: `feat/coolify-access-foundation`; Flask/Gunicorn container uses port 8765 and `/app/data` (UID 10001).
- Fresh installation: no database import from Eric's local draft is required. Schema version 1 is initialized transactionally.
- No launchd services or macOS-specific data paths. Test/production SQLite defaults to `./data/audit.sqlite3`; development uses fresh temporary synthetic storage. File locks are portable.
- Runtime starts exactly one worker/instance. Use stop/start deployment rather than overlapping instances on this SQLite volume.
- New installations start with EMAIL_ENABLED=false and SCHEDULER_ENABLED=false. Live integration testing requires explicit user authorization; the current staging test authorization is recorded above.
- Cloudflare mode persists an automatically generated session signing key in `/app/data/session.key` unless APP_SECRET_KEY is explicitly provided.
- Use `.venv/bin/python -m pip install -r requirements.lock` for reproducible dependencies.
- See `docs/DEPLOYMENT.md`, `docs/USER_GUIDE.md`, and `CHANGELOG.md` for operations and scope.

### Development and PR previews

- Development is open with a fixed demo admin, synthetic fixtures and a fresh temporary database per app startup. Never permit live Bridge access or email/scheduling in that mode, even with credentials/flags supplied. Backup/restore is disabled.
- Preview hostname template: `auditdesk-pr{{pr_id}}.oncornerstone.app`. Preview-only `APP_ENV=development` and `PUBLIC_BASE_URL=auto`; auto derives the HTTPS browser origin from Coolify `COOLIFY_URL`. Preview keys are blank, and no persistent volume is needed.
- Keep the main staging app in Cloudflare-authenticated live-data test mode with its existing administrator-only mail and daily schedule.
