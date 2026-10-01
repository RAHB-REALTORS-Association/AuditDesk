# Architecture and module boundaries

AuditDesk is a modular monolith: one Python process serves staff pages, runs the optional scheduler, and owns a SQLite database. The production image uses one Gunicorn worker with four threads. A service lock prevents overlapping processes from owning the same database; a separate job lock serializes intake and delivery work.

## Entry points and presentation

| Module | Responsibility |
| --- | --- |
| `main.py`, `audit_app/__main__.py` | Thin launchers for the same CLI |
| `audit_app/cli.py` | Parse commands, load `.env` without overriding the environment, configure JSON logging, invoke services |
| `audit_app/wsgi.py` | Create the deployment app and start its runtime under Gunicorn |
| `audit_app/application.py` | Flask app factory, identity/request guards, authorization, routes, mutation orchestration, exports, errors, and response headers |
| `audit_app/runtime.py` | Service lock, interrupted-work recovery, optional scheduler, local HTTP startup |
| `audit_app/web.py` | Page composition, navigation, tab capability map, history queries, and form revisions |
| `audit_app/views/common.py` | Escaping, local time, status badges, sort headings, recipients, and CSRF form tokens |
| `audit_app/views/workflow.py` | Outcome forms and isolated simulation presentation |
| `audit_app/views/management.py` | Selection settings, access management, and activity views |
| `audit_app/views/emails.py` | Email template editor and rendered previews |
| `audit_app/views/reports.py` | Daily and brokerage HTML reports |
| `audit_app/static/` | Shared styles, navigation/sorting/branch scripts, email editor script, and branding |

Views format data and construct HTML. Routes authorize and validate HTTP input before calling workflow services. Visibility checks in navigation do not substitute for server-side permissions. `templates.py` handles email content and sanitization; it is separate from browser presentation in `views/`.

## Workflow and infrastructure

| Module | Responsibility |
| --- | --- |
| `config.py` | Environment parsing, immutable configuration, development isolation, web identity/origin validation |
| `database.py` | SQLite connections, schema initialization, transactions, record revisions, activity records |
| `security.py` | Signed Cloudflare Access assertions, application identities/roles/capabilities, CSRF, bootstrap access |
| `bridge.py` | OData metadata and listing/contact reads, field mapping, bounded transient retries |
| `job.py` | Active intake window, fair selection/cooldowns, duplicate prevention, request delivery and retries |
| `emailer.py` | Recipient validation, test redirection, SendGrid request/notice transport |
| `outcomes.py` | Single-assignment results and failure-notice delivery |
| `assignment.py`, `settings.py` | Individual/bulk account assignments and managed selection percentage |
| `templates.py` | Merge tags, sanitized rich/plain email content, saved request/failure wording |
| `report.py`, `brokerage_report.py`, `brokerage_pdf.py` | Daily aggregates, grouped brokerage/branch statistics, branded PDF generation |
| `backup.py` | Consistent online backup and validated offline restore |
| `office_backfill.py` | Address maintenance for older imported records |
| `development.py`, `simulation.py` | Synthetic preview fixtures and isolated workflow demonstration |

Keep domain changes in their existing modules; avoid adding another service or moving everything into generic utility folders. Integrations receive configuration explicitly and must retain their development-mode guards.

## Data ownership

Bridge supplies listing, agent, office, and broker facts. Intake stores the information used for the audit locally; this is an audit history, not continuous replication of the MLS. Office backfill retrieves current addresses for older records without reselecting audits.

Application-owned state includes selections, delivery attempts, reviewer assignments, results/issues, templates, settings, application users, and activity history. Assignments reference application user IDs; eligibility follows the active account and its role capabilities. Authenticated identity records who performed a mutation. Bulk assignment validates the entire batch and writes assignments and activity events in one transaction.

Test/production persist SQLite at `/app/data/audit.sqlite3`. The generated session signing key is stored beside it unless overridden. Development creates a fresh temporary directory per app startup, strips live settings, and seeds synthetic records. It never opens the configured live database.

Schema version 2 initializes transactionally. It replaces the development-only reviewer roster and its assignments with an `assignee_user_id` foreign key to application accounts. Initialization supports baseline/version-1 databases, drops the obsolete roster fields/table, and refuses unknown newer versions. See [deployment](DEPLOYMENT.md) for migration and recovery constraints.

## Selection and delivery lifecycle

1. Intake queries Active listings from the rolling window, extended to cover the last successful intake boundary after downtime.
2. Unique source identifiers exclude previously processed listings. New records enter a binomial selection target using the managed percentage.
3. Brokerage/broker cooldowns remove ineligible candidates. Square-root volume weighting balances remaining brokerages; each broker/brokerage can be selected once per run.
4. An audit and its recipient routing are committed before SendGrid is called. Missing/invalid contacts leave a visible failure.
5. Each send attempt is recorded. Accepted sends become `email_sent`; explicit errors become `email_failed`; a lost response can become `email_unknown`.
6. An accepted original request can receive one result. Passed results send no notice; failed results retain issues and a separate notice delivery history.

Pending requests resume on the next job. Explicit failures can be retried without creating another audit. Unknown deliveries are never automatically resent. Service startup converts interrupted sending states to unknown and interrupted runs to failed. Test audits cannot later send production requests or failure notices.

The scheduler checks every five minutes after 08:00 in `APP_TIMEZONE`. Completed intake suppresses another scheduled run that day; failed intake can retry. The Active-only rule still excludes listings that became inactive during downtime.

## Identity and mutations

Test/production require signed Access identity with the configured issuer/audience and an active application role. Unknown/disabled identities are denied. Bootstrap administrators are protected. Development has a fixed demo administrator.

Mutations check capability, origin, CSRF token, bounded form input, and the rendered activity revision. Successful changes append activity history. Stale forms are rejected rather than overwriting newer work. Backup and restore remain operator CLI actions.

## Validation and deployment

Regression tests cover workflows and denied HTTP paths with synthetic data and mocked providers. Container smoke tests exercise the actual Gunicorn image, persistent restart, and disposable previews. See [Contributing](../CONTRIBUTING.md) for commands and [Deployment](DEPLOYMENT.md) for operations. The deployment entry point is unchanged.
