# AuditDesk contributor instructions

## Scope and workflow

- Preserve Bridge intake, fair selection and cooldowns, SendGrid delivery protections, assignments, results, and reports.
- Apply relevant guidance from the Cornerstone App Blueprint skill without adding unrelated features or replacing the existing deployment model.
- Read [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before structural changes. Update the relevant guide and changelog alongside code.
- This project uses the [MIT License](LICENSE).

## Repository

- `origin`: `https://github.com/RAHB-REALTORS-Association/AuditDesk.git` (organization fork).
- `upstream`: `https://github.com/ericmeek7/Audit-App.git` (original author).
- Local `main` tracks `origin/main`. Organization development PRs target the fork's `main`; do not push to upstream.
- Use focused feature branches. Preserve existing user changes.

## Local verification

- Before Python commands or dependency installation, check this repository and nearby task directories for `.venv`, `venv`, or a documented environment. Reuse it when present. Report the interpreter when artifact hashes or versions matter.
- Python 3.14 is the CI/container runtime. Install reproducible dependencies with the chosen interpreter's `-m pip install -r requirements.lock`.
- Run `python -m unittest discover -s tests` from the root with the project environment activated.
- Use `python main.py simulate` for isolated workflow verification. Tests use temporary databases and mocked integrations; do not run live intake or send mail merely to verify a code change.
- Container verification: `docker build -t auditdesk:local .`, then `python scripts/container_smoke.py auditdesk:local`.

## Boundaries to preserve

- Cloudflare Access authenticates test/production; the app validates the signed assertion and enforces roles/capabilities. Never trust unsigned identity or role headers.
- Assignments reference active application accounts with auditing permission; do not reintroduce a separate reviewer roster. Assignment responsibility and the authenticated actor making a change are separate. Reviewer, Audit manager, and IT administrator roles have distinct server-enforced permissions.
- Development is open with a fixed demo administrator, synthetic fixtures, and fresh temporary storage per startup. Credentials and enable flags must never activate Bridge, email, scheduling, or live storage. Backup/restore and live maintenance commands stay unavailable.
- Keep duplicate prevention, unknown-delivery handling, test-recipient substitution, test-to-production protections, and stale-form checks.
- Never commit secrets, `.env`, session keys, real listings/contacts, database exports, backups, or generated reports.

## Deployment

- Target Coolify with the existing Dockerfile, `audit_app.wsgi:app`, port `8765`, UID/GID `10001`, and persistent `/app/data` storage.
- Run exactly one Gunicorn worker/application instance per database. Use stop/start upgrades; preserve the existing persistent volume.
- New installations default email and scheduling off. Live integration tests and production mail require explicit user authorization.
- PR previews use `APP_ENV=development`, `PUBLIC_BASE_URL=auto`, and `auditdesk-pr{{pr_id}}.oncornerstone.app`, without a persistent volume or real keys.
- Keep staging and preview variables separate. Do not change shared Cloudflare zone, certificate, or tunnel settings without a verified need.
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) and [docs/CONFIGURATION.md](docs/CONFIGURATION.md) are the operational guides. [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md) preserves the dated deployment handoff; verify current settings before relying on historical values.
