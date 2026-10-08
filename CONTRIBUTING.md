# Contributing to AuditDesk

Preserve the RESO Web API intake, fair selection, cooldowns, delivery protections, assignments, results, and reporting workflow. The [architecture guide](docs/ARCHITECTURE.md) maps these responsibilities.

## Local setup

Use Python 3.14. Check this repository and nearby task directories for `.venv`, `venv`, or a documented environment before creating one or installing dependencies. Reuse the existing interpreter when available.

For a new environment, run from the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
cp .env.example .env
python main.py serve
```

Windows activation is `.venv\Scripts\Activate.ps1`. Development uses disposable synthetic storage, a demo administrator, and no live integrations. Edits disappear on restart.

`requirements.txt` defines permitted dependency ranges; `requirements.lock` pins runtime and transitive dependencies used by CI and Docker. Dependency changes should update both as appropriate, review the resulting pins, and verify with the lock file.

## Commands

Both `python main.py COMMAND` and `python -m audit_app COMMAND` are supported. Run from the repository root so `.env` and the default field map resolve correctly.

| Command | Purpose | External effects |
| --- | --- | --- |
| `serve` | Start the local Flask server | Development is synthetic; test/production can run enabled scheduling |
| `simulate` | Demonstrate the complete audit cycle | Temporary synthetic data, no MLS API or SendGrid calls |
| `run` | Run intake and selection | Development simulates; test/production queries the MLS and may send enabled mail |
| `inspect-reso` (`inspect-bridge` alias) | Validate the field map against RESO Web API metadata | Live MLS API reads; unavailable in development |
| `backfill-listing-boards` | Verify historical listing boards and agent MLS identifiers without selecting audits or sending email | RESO Web API reads and local provenance/activity updates; unavailable in development |
| `backfill-broker-contacts` | Repair missing broker contacts on eligible unsent audits | MLS reads and snapshot/recipient updates; no selection or delivery; unavailable in development |
| `backfill-membership-classes` | Verify historical listing agent membership classes | MLS Member reads and class/activity updates, no selection or delivery; unavailable in development |
| `backfill-office-addresses` | Fill older listing office addresses | Live MLS API reads and local address updates; unavailable in development |
| `backup --output PATH` | Create a consistent database backup | New sensitive backup file; unavailable in development |
| `restore --input PATH --confirm 'RESTORE STOPPED AUDITDESK'` | Restore a stopped service | Replaces database state; follow the deployment guide |

The deployment server is `audit_app.wsgi:app` under Gunicorn. `serve` is a local development convenience. Runtime environment variables configure Gunicorn; `.env` loading belongs to the CLI.

## Verification

Use the project virtual environment:

```sh
python -m unittest discover -s tests
python main.py simulate
docker build -t auditdesk:local .
python scripts/container_smoke.py auditdesk:local
```

Unit tests use temporary databases, synthetic contacts, and mocked providers. For workflow changes, cover meaningful behavior, including permission denial, duplicates, and delivery uncertainty where relevant. Use `.invalid` example contacts for fixtures.

The smoke script creates and removes its own containers and temporary volume. It checks non-root startup, health, rejection without signed identity, persistence, and sandbox resets. It never invokes live intake or sends mail. Browser changes should also be checked for keyboard use, form preservation, and narrow-screen layout.

## Pull requests

Branch from the latest `origin/main`. `origin` is [Cornerstone's AuditDesk fork](https://github.com/RAHB-REALTORS-Association/AuditDesk); `upstream` is the original author's repository. Open organization development PRs against the fork's `main`.

Describe the resulting behavior, why it changed, and checks performed. Include configuration, schema, or deployment impact when applicable. Update the relevant guide and [changelog](CHANGELOG.md) alongside code. CI runs regression tests, container checks, and dependency auditing. `v*` tags trigger image releases.

Never commit `.env`, credentials, tokens, real listing/contact exports, databases, backups, session keys, or generated reports. Keep live integration tests separate from routine verification. Preserve `/app/data` during deployment work and keep one service instance per database.

Report vulnerabilities through [SECURITY.md](SECURITY.md). Contributions are licensed under the project's [MIT License](LICENSE).
