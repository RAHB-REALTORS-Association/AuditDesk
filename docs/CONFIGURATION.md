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

Flags accept `1`, `true`, or `yes`, case-insensitively. A test cutoff blocks scheduled RESO Web API intake and all email sends after that time. Disabling the scheduler does not prevent a manually invoked `run` from reading the MLS. `EMAIL_ENABLED=false` blocks mail independently of intake. Development cannot activate either integration with these flags.

## RESO Web API and SendGrid

| Variable | Default | Meaning |
| --- | --- | --- |
| `RESO_BASE_URL` | Empty in code; example uses the `itso` OData endpoint | Live dataset base URL |
| `RESO_API_KEY` | Empty | MLS Web API credential |
| `RESO_AUTH_MODE` | `bearer` | `bearer` or `query`; use the mode required by the provider |
| `RESO_FIELD_MAP` | `bridge_fields.json` | JSON field map, relative to the working directory unless absolute |
| `SENDGRID_API_KEY` | Empty | SendGrid credential |
| `EMAIL_FROM_ADDRESS` | Empty | Verified sender address |
| `EMAIL_FROM_NAME` | `MLS Audit Team` | Sender display name |

The broker comes from `Property.ListOfficeKey` → `Office.OfficeBrokerKey` → `Member.MemberEmail`. Brokerage `OfficeEmail` and the listing agent are copied when available; the agent has a Member fallback. Duplicate addresses receive one copy. Missing/invalid broker or agent addresses prevent delivery even in test mode.

The map in [`bridge_fields.json`](../bridge_fields.json) describes the `itso` fields used. Run `inspect-reso` only as an authorized live diagnostic. The client uses [RESO Web API](https://www.reso.org/reso-web-api/) metadata and OData queries. Bridge is the current provider, not the protocol.

### Provider configuration and compatibility

`RESO_BASE_URL`, `RESO_API_KEY`, `RESO_AUTH_MODE` and `RESO_FIELD_MAP` are the preferred names. Existing `BRIDGE_BASE_URL`, `BRIDGE_API_KEY`, `BRIDGE_AUTH_MODE` and `BRIDGE_FIELD_MAP` remain supported when their RESO equivalent is absent. An explicitly set RESO value, including an empty value, takes precedence. Existing deployments do not need renamed variables to upgrade. The default `bridge_fields.json` filename and stored `bridge_listing_id` are historical names; neither requires Bridge as the provider. `inspect-bridge` remains an alias for `inspect-reso`.

To configure another MLS service, supply its HTTPS OData service root, provisioned credential, supported authentication mode, and field map. The client reads Property, Member and Office resources, metadata, timestamps and pagination. It supports a provisioned bearer token or the provider's `access_token` query mode; it does not perform OAuth token acquisition/renewal. Validate resource/field availability with `inspect-reso` and verify broker relationships and membership-code meanings against that dataset before live intake. The current field map, Cornerstone board boundary, NONMEM and NL7 conventions are dataset/business requirements, not guarantees supplied by the transport standard. Other providers have not been integration-tested.

## Managed workflow settings and starting defaults

| Variable | Default | Meaning |
| --- | --- | --- |
| `AUDIT_RATE` | `0.05` | Starting probability from `0` to `1`; saved manager percentage takes precedence |
| `LISTING_WINDOW_HOURS` | `24` | Starting intake window; saved Manage value of 1–168 hours takes precedence; catch-up may extend it |
| `BROKERAGE_COOLDOWN_DAYS` | `14` | Starting office cooldown; saved Manage setting of 0–365 whole days takes precedence |
| `BROKER_COOLDOWN_DAYS` | `14` | Starting broker cooldown; saved Manage value of 0–365 days takes precedence |

Managed selection percentage, office/broker cooldown, and listing window changes affect future new listings only. They do not reselect earlier records or reopen a test window. A 100% target does not bypass cooldowns or per-run broker/brokerage limits. A zero-day office cooldown removes the between-run wait; it does not remove the one-selection-per-office-per-run limit or the separate broker cooldown.

The **Manage → Selection** page groups these business controls; saved values persist in SQLite and apply to future runs without a restart. Environment values remain starting defaults for databases without saved settings. Changes do not rewrite historical records. Identity, credentials, sender verification, timezone, test recipient/cutoff, and live email/scheduler switches remain deployment configuration.

## Email wording and merge tags

`EMAIL_SUBJECT_TEMPLATE` and `EMAIL_BODY_TEMPLATE` supply the initial request wording until a manager saves a template in SQLite. The default subject is `Listing Audit Request - MLS {mls_number}`. The example body requests listing paperwork and identifies the MLS number, address, agent, and brokerage. Literal `\n` in a configured body becomes a newline.

The editor supports `{{mls_number}}`, `{{address}}`, `{{agent_name}}`, `{{brokerage_name}}`, `{{broker_name}}`, and `{{broker_first_name}}`. Legacy single-brace fields are also accepted. Request bodies require MLS number, address, agent, and brokerage tags. Failure bodies require MLS number, address, and `{{issues}}`. Failed-audit wording is managed separately in the application; there is no separate failure-template environment variable.

Saved wording applies to future sends and retries without restarting. Preview does not send; previously sent messages are unchanged. See the [staff guide](USER_GUIDE.md) for editing instructions.

## PR previews

Use preview-only `APP_ENV=development` and `PUBLIC_BASE_URL=auto`. Coolify's generated `COOLIFY_URL` supplies the browser origin; scheme-less domains and HTTP origin routes become an HTTPS browser origin. No persistent volume or integration keys are needed. Local development should keep an explicit `http://127.0.0.1:8765` origin.

Test/production require an explicit HTTPS origin. See [Deployment](DEPLOYMENT.md) for the existing preview hostname template, Access setup, and storage requirements.

## Office availability

Weekly office hours and explicit holiday/full-day closure dates are managed in the database under **Manage → Hours & holidays**, rather than environment variables. Defaults are 08:30–16:30 Monday–Friday; no holidays are assumed. `APP_TIMEZONE` is also the office calendar timezone. Request delivery requires both the send time and its 24-hour deadline to fall in open hours. `SCHEDULER_ENABLED` must be true for queued requests to drain automatically; `EMAIL_ENABLED` and test-window protections still apply. Sent deadlines are fixed and survive calendar changes.

## Listing eligibility

**Manage → Listing eligibility** stores excluded membership-class codes and additional agent MLS IDs in SQLite. NL7 is excluded by default; NONMEM and non-Cornerstone boards remain mandatory exclusions. Codes are case-insensitive, deduplicated and limited to 50 per field. Managers and administrators may preview and save rules. Preview compares proposed rules with stored snapshots only; it does not query the MLS or send mail.

The `Member.membership_class` field mapping currently uses `MemberMlsSecurityClass`. Metadata must contain this string field. Before enabling live intake with a new mapping, verify its value against a known NL7 listing agent: metadata presence alone does not establish which ITSO field contains the MUC code. Infrastructure field mappings remain an IT configuration; business exclusion codes are managed in the app.

The same rules govern intake, selection, request/failure delivery, retries, refreshes, staff actions, lists and reports. Known excluded records remain stored but are hidden from ordinary lists/reports; their reasons can be reviewed in the eligibility preview. Historical membership classes start unverified: records remain visible if their board and MLS ID are eligible, but cannot send requests or failed-audit notices until verified. New intake missing a membership class fails visibly and rolls back its listing/selection transaction, allowing a later corrected run to reconsider those listings. Changes do not reselect already processed listings or automatically retry failed mail.
