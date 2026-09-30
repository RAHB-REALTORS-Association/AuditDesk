# AuditDesk working context

## Product and architecture

- Preserve the MLS paperwork audit workflow: Bridge intake, fair selection and cooldowns, SendGrid requests, reviewer assignments, results, and reports.
- Use the Cornerstone App Blueprint skill as the implementation guide, applying relevant sections without adding unrelated features.
- Deployment target is Coolify. Cloudflare Access supplies authentication; the application validates its signed assertion and owns server-enforced authorization.
- Reviewer assignment and authenticated actor identity are separate concepts. Roles: Reviewer handles shared audits/results/retries/reports; Audit manager adds assignment/roster/templates/selection; IT administrator adds access management/activity export.

## Repository

- `origin`: https://github.com/RAHB-REALTORS-Association/AuditDesk.git
- `upstream`: https://github.com/ericmeek7/Audit-App.git
- Local `main` tracks `origin/main`. Initial reviewed baseline: `ac52c93`.
- Do not push changes to the original author's repository as part of organization development.

## Existing deployment target

- Coolify application: https://cloud.cornerstone.inc/project/v04ccsgos0skkokcocgogwg4/environment/a84sgoww00kc48owk8o44wwc/application/vw844cowkss0k8ccs8gww8ws
- User moved the skeleton to VPS06 on 2026-09-30; verified Primary Server VPS06, network coolify. The original application URL is obsolete.
- Team: Cornerstone.inc; project: Cornerstone Association; environment: staging; application: AuditDesk.
- User prefers Chrome debug for Coolify interaction. The authenticated Chrome RAHB profile was accessible through browser tooling during initial inspection.
- On 2026-09-30 the user described this as an undeployed skeleton. Read-only inspection showed status Exited, organization fork on main, Nixpacks, start command `python3 main.py serve`, exposed port `3000`, and domain `http://auditdesk.ai.rahb.local`.
- These are observed skeleton values, not approved final deployment settings. The application currently defaults to port `8765` and localhost binding. Resolve these differences before deployment.
- Target HTTPS hostname: `auditdesk.oncornerstone.app`; bootstrap administrator: `justin.hayes@cornerstone.inc`. Cloudflare issuer/audience, persistent storage, and health checks remain to be configured and verified. Do not store secret values in source or documentation.
- Preserve persistent data through deployment changes; never recreate or detach storage as a routine upgrade step.

## Local verification

- Check the project and nearby task directories for an existing virtual environment before Python commands or dependency installation. Use it when present.
- Current environment: `.venv/bin/python` (Python 3.14.5 at initial review).
- Install declared dependencies with `.venv/bin/python -m pip install -r requirements.txt`.
- Run `.venv/bin/python -m unittest discover -s tests` from the repository root. Initial baseline: 36 passing tests.
- Tests use temporary databases and mocked integrations. Do not run live intake or send email merely to verify a code change.

## Initial review priorities

- Replace shared Basic Auth with validated Access identity and application permissions.
- Add authenticated mutation history, versioned migrations, backup/recovery, container packaging, health checks, and CI.
- Correct catch-up scheduling: failed runs currently suppress another attempt that day; rolling intake can miss listings after downtime.
- Prevent failed test request emails from becoming production sends after an environment change.
- Preserve existing duplicate prevention, uncertain-delivery handling, test-recipient substitution, and business-rule coverage.

## Foundation implementation

- Work branch: `feat/coolify-access-foundation`; Flask/Gunicorn container uses port 8765 and `/app/data` (UID 10001).
- Fresh installation: no database import from Eric's local draft is required. Schema version 1 is initialized transactionally.
- No launchd services or macOS-specific data paths. Local SQLite defaults to `./data/audit.sqlite3`; file locks are portable.
- Runtime starts exactly one worker/instance. Use stop/start deployment rather than overlapping instances on this SQLite volume.
- Staging starts with EMAIL_ENABLED=false and SCHEDULER_ENABLED=false. Do not enable live integrations as part of UI verification.
- Cloudflare mode persists an automatically generated session signing key in `/app/data/session.key` unless APP_SECRET_KEY is explicitly provided.
- Use `.venv/bin/python -m pip install -r requirements.lock` for reproducible dependencies.
- See `docs/DEPLOYMENT.md`, `docs/USER_GUIDE.md`, and `CHANGELOG.md` for operations and scope.
