# Configuration reference

Start with [`.env.example`](../.env.example). CLI commands load `.env` from the working directory; existing environment variables win. Gunicorn's `audit_app.wsgi:app` reads only process environment settings. Run CLI commands from the repository root, and use masked runtime variables in Coolify.

## Environments

| Mode | Behavior |
| --- | --- |
| `development` (default) | Open demo identity, temporary synthetic database, no live intake/mail/scheduler or backup/restore |
| `test` | Persistent live listings, Cloudflare identity, enabled mail redirected exclusively to `ADMIN_EMAIL` |
| `production` | Persistent live listings, Cloudflare identity, enabled mail to actual recipients |

Development ignores live database/session paths, integration keys, real identity/bootstrap settings, email/scheduler flags, and test cutoffs. Form protection still applies. Host, port, timezone, selection defaults, and the browser origin remain configurable. `test` means live integration testing; automated unit tests mock providers independently of this environment name.

## Service and identity

| Variable | Default | Meaning |
| --- | --- | --- |
| `APP_ENV` | `development` | One of the three modes above |
| `APP_HOST` | `127.0.0.1` | Local `serve` bind address; image default is `0.0.0.0` |
| `APP_PORT` | `8765` | Local `serve` port; packaged Gunicorn binds `0.0.0.0:8765` |
| `PUBLIC_BASE_URL` | `http://127.0.0.1:8765` | Exact browser origin, no path/query; HTTPS required for test/production |
| `DATABASE_PATH` | `./data/audit.sqlite3` | Test/production database; image default is `/app/data/audit.sqlite3` |
| `APP_TIMEZONE` | `America/Toronto` | IANA zone for reports and the 08:00 daily schedule |
| `AUTH_MODE` | `cloudflare` | Required in test/production; development forces its open demo mode |
| `CF_ACCESS_ISSUER` | Empty | Required HTTPS team origin ending in `.cloudflareaccess.com` |
| `CF_ACCESS_AUDIENCE` | Empty | Required AUD of the Access application |
| `BOOTSTRAP_ADMIN_EMAILS` | Empty | Comma-separated initial IT administrator emails; protected from UI demotion |
| `APP_SECRET_KEY` | Empty | Optional override of at least 32 random characters; otherwise a restricted `session.key` is generated beside the database |

Changing an image's `APP_PORT` alone does not change its Gunicorn command or health probe. Use the packaged deployment port `8765`. Store session keys and Access configuration in runtime settings, never build arguments.

## Schedule and delivery switches

| Variable | Default | Meaning |
| --- | --- | --- |
| `SCHEDULER_ENABLED` | `false` | Enable the in-process daily scheduler in test/production |
| `EMAIL_ENABLED` | `false` | Enable request/failure delivery, including retries, in test/production |
| `TEST_MODE_END_AT` | Empty | Optional test cutoff as ISO 8601 with offset, e.g. `2026-10-15T00:00:00-04:00` |
| `ADMIN_EMAIL` | Empty | Sole actual email recipient in test mode |

Flags accept `1`, `true`, or `yes`, case-insensitively. A test cutoff blocks scheduled Bridge intake and all email sends after that time. Disabling the scheduler does not prevent a manually invoked `run` from reading Bridge. `EMAIL_ENABLED=false` blocks mail independently of intake. Development cannot activate either integration with these flags.

## Bridge and SendGrid

| Variable | Default | Meaning |
| --- | --- | --- |
| `BRIDGE_BASE_URL` | Empty in code; example uses the `itso` OData endpoint | Live dataset base URL |
| `BRIDGE_API_KEY` | Empty | Bridge credential |
| `BRIDGE_AUTH_MODE` | `bearer` | `bearer` or `query`; use the mode required by the provider |
| `BRIDGE_FIELD_MAP` | `bridge_fields.json` | JSON field map, relative to the working directory unless absolute |
| `SENDGRID_API_KEY` | Empty | SendGrid credential |
| `EMAIL_FROM_ADDRESS` | Empty | Verified sender address |
| `EMAIL_FROM_NAME` | `MLS Audit Team` | Sender display name |

The broker comes from `Property.ListOfficeKey` → `Office.OfficeBrokerKey` → `Member.MemberEmail`. Brokerage `OfficeEmail` and the listing agent are copied when available; the agent has a Member fallback. Duplicate addresses receive one copy. Missing/invalid broker or agent addresses prevent delivery even in test mode.

The map in [`bridge_fields.json`](../bridge_fields.json) describes the `itso` fields used. Run `inspect-bridge` only as an authorized live diagnostic. The query and metadata conventions follow the [RESO Bridge API examples](https://www.reso.org/web-api-examples/mls/bridge-api-generic/).

## Selection defaults

| Variable | Default | Meaning |
| --- | --- | --- |
| `AUDIT_RATE` | `0.05` | Starting probability from `0` to `1`; saved manager percentage takes precedence |
| `LISTING_WINDOW_HOURS` | `24` | Initial positive intake window; later runs extend it for catch-up |
| `BROKERAGE_COOLDOWN_DAYS` | `14` | Starting office cooldown; saved Admin setting of 0–365 whole days takes precedence |
| `BROKER_COOLDOWN_DAYS` | `14` | Nonnegative broker cooldown |

Managed selection percentage and brokerage cooldown changes affect future new listings only. They do not reselect earlier records or reopen a test window. A 100% target does not bypass cooldowns or per-run broker/brokerage limits. A zero-day office cooldown removes the between-run wait; it does not remove the one-selection-per-office-per-run limit or the separate broker cooldown.

## Email wording and merge tags

`EMAIL_SUBJECT_TEMPLATE` and `EMAIL_BODY_TEMPLATE` supply the initial request wording until a manager saves a template in SQLite. The default subject is `Listing Audit Request - MLS {mls_number}`. The example body requests listing paperwork and identifies the MLS number, address, agent, and brokerage. Literal `\n` in a configured body becomes a newline.

The editor supports `{{mls_number}}`, `{{address}}`, `{{agent_name}}`, `{{brokerage_name}}`, `{{broker_name}}`, and `{{broker_first_name}}`. Legacy single-brace fields are also accepted. Request bodies require MLS number, address, agent, and brokerage tags. Failure bodies require MLS number, address, and `{{issues}}`. Failed-audit wording is managed separately in the application; there is no separate failure-template environment variable.

Saved wording applies to future sends and retries without restarting. Preview does not send; previously sent messages are unchanged. See the [staff guide](USER_GUIDE.md) for editing instructions.

## PR previews

Use preview-only `APP_ENV=development` and `PUBLIC_BASE_URL=auto`. Coolify's generated `COOLIFY_URL` supplies the browser origin; scheme-less domains and HTTP origin routes become an HTTPS browser origin. No persistent volume or integration keys are needed. Local development should keep an explicit `http://127.0.0.1:8765` origin.

Test/production require an explicit HTTPS origin. See [Deployment](DEPLOYMENT.md) for the existing preview hostname template, Access setup, and storage requirements.
