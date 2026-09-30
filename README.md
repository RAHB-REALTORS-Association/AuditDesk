# MLS Audit Desk

A small internal application for selecting new Active Bridge listings for paperwork audits. The daily job records every listing considered, uses a configurable selection lottery, applies brokerage and broker cooldowns, then sends selected requests through SendGrid. The staff page shows audit history and failed-email retries.

See [Deployment](docs/DEPLOYMENT.md) for Coolify, Cloudflare Access, backup, and releases; [Roles and workflows](docs/USER_GUIDE.md) for staff instructions.

## Start the staff interface

The server listens on **127.0.0.1:8765**. Access the interface through a Cloudflare Access protected HTTPS hostname, including during interactive development:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
cp .env.example .env
# Configure CF_ACCESS_ISSUER, CF_ACCESS_AUDIENCE, PUBLIC_BASE_URL and BOOTSTRAP_ADMIN_EMAILS.
python main.py serve
```

On Windows, activate with `.venv\Scripts\Activate.ps1`, or use Docker for the same Linux runtime as deployment.

Cloudflare Access is the only web authentication method. For interactive local development, route a dedicated Access protected development hostname to the loopback server through your development tunnel. Unit tests and synthetic CLI simulation run without an identity provider or live integrations. There are no shared passwords or Basic Auth fallback.

The orange **TEST MODE** banner appears whenever `APP_ENV` is `development` or `test`. Production email requires `APP_ENV=production` and complete configuration. Test mode passes only `ADMIN_EMAIL` to SendGrid as a recipient; intended broker, office, and agent addresses appear in the subject/body for diagnosis and in the audit record.

For a time-limited live-data test, set `APP_ENV=test` and `TEST_MODE_END_AT` to an ISO 8601 timestamp with an offset, such as `2026-09-28T00:00:00-04:00`. Once that time passes, scheduled runs record `test_window_closed` without querying Bridge, and both scheduled and manual email sends are blocked. Scheduling can be disabled independently with `SCHEDULER_ENABLED=false`.

## Edit the audit email

Open **Admin → Audit request email** in the staff sidebar. Edit the subject and body, use the `{{merge_tags}}` buttons to insert listing details, and select **Preview changes** to review the rendered message without saving or sending. Select **Save template** when it is ready. The saved wording is stored in the audit database and applies to future scheduled sends and manual retries without restarting the app. Previously sent emails are unchanged.

Select text in the message body and use **B**, **I**, or **U** to apply bold, italic, or underline. Preview shows the formatted email. SendGrid receives both a formatted HTML body and a readable plain-text version. Existing saved plain-text templates remain editable and are converted to the rich editor when next saved.

Available merge tags are `{{mls_number}}`, `{{address}}`, `{{agent_name}}`, `{{brokerage_name}}`, `{{broker_name}}`, and `{{broker_first_name}}`. The broker's first name comes from Bridge `MemberFirstName`. Older saved listings use the first name from `MemberFullName` when available. The body must contain the first four tags so every request identifies the listing and agent. An unknown or incomplete tag is rejected before saving. The editor shows the test-mode recipient diagnostic in its preview while TEST MODE is active.

## Record an audit result

After the original request email is accepted, open **Record result** on its Audit history row. **Mark passed** records a pass without sending another email. For a failed audit, enter the issues, select **Preview failed notice** to review the rendered message and recipients, then select **Record fail and send notice**. The issues are saved with the audit and included through the `{{issues}}` merge tag. Each audit can receive one result, preventing duplicate failed-audit notices from repeated submissions.

Edit the follow-up wording under **Admin → Failed-audit email**. Its subject and body support the same bold, italic, and underline controls and merge tags as the original request, plus `{{issues}}`. The failure body must include `{{mls_number}}`, `{{address}}`, and `{{issues}}`. A failed SendGrid attempt can be retried from Audit history without recording another result; uncertain sends are not automatically retried. In test mode, the notice goes only to `ADMIN_EMAIL` and obeys the test-window cutoff. A test audit cannot later send a production failure notice to a broker.

## Track who is working on an audit

Open **Admin → Audit team** to add names, correct a name, remove a name from future assignments, or delete its roster entry. **Remove from list** can be undone. **Delete** removes the name from the roster and frees it for reuse; an unfinished audit assigned to that person shows **Needs reassignment** and retains the former name for context. Completed audits keep their result and former assignee name. In **Audit history**, choose a name in the **Assigned to** dropdown and save. The **Work status** column shows **Not started** when no one is assigned, **In progress** when someone is assigned, and **Completed** after a pass or fail result is recorded. Clearing an assignment returns an unfinished audit to **Not started**. Assignment changes do not send email. The roster controls work assignment. The authenticated user who makes a change is recorded separately in the activity log.

## Change the audit selection percentage

Open **Admin → Selection settings** to set a percentage from 0% to 100%, with up to two decimal places. The saved value applies to new listings in future runs without restarting the app. It does not reselect listings already processed, change earlier audit records, or reopen an ended test window. The setting is stored in the local audit database; `AUDIT_RATE` in `.env` is the starting value until an Admin value is saved. Audit managers and IT administrators can change selection settings.

## Review daily audit volume

Open **Admin → Daily audit report** for the past 90 local calendar days. It shows the number of unique new Active listings first processed by the app each day, how many were selected for audit, and the audited percentage. Manual test runs are included. Days with no completed run show unavailable values rather than a misleading zero; a completed run that found no new listings shows zero. The report is calculated from the persistent listing, audit, and run records, so it survives dashboard restarts. It does not represent all listings in the MLS if the app missed a daily intake.

## Review brokerage statistics

Open **Admin → Brokerage statistics** to compare brokerages for a rolling 3-month, 6-month, or 1-year period. Each row shows unique new Active listings first processed by the app, how many received an audit selection, the audited percentage, and passed and failed counts and rates. Pass and fail rates use completed audits only; pending audits are excluded, and a rate is unavailable until the brokerage has a completed audit. Branches with the same brokerage name (ignoring capitalization and extra spaces) are combined into one row, with their counts and rates calculated together. Select **View branches** to see each office's Bridge profile address and its own counts and rates; brokerage rows remain grouped while sorting. Listings without a brokerage name retain their Bridge office ID as a separate group. Manual test runs are included. Use **Export branded PDF** to download a management report for the selected period, including all branch addresses. Both views show when app history begins; an earlier portion of a selected period cannot be filled from missing intake history. Install PDF support with `python3 -m pip install -r requirements.txt` in the Python environment that runs the dashboard.

For a database created before office addresses were saved, run `python3 main.py backfill-office-addresses` once to retrieve the current Bridge Office profile addresses for existing listings. This command only updates local addresses; it does not select audits or send emails. New listings save the address automatically.

## Simulate a full cycle

Open **Simulation** in the staff sidebar, or run `python3 main.py simulate`. This uses synthetic listings and a temporary database. It demonstrates the 24-hour and Active filters, brokerage cooldown, audit creation, test-mode recipient substitution, a simulated SendGrid failure, idempotent second run, and successful manual retry. It does not call Bridge or SendGrid and does not change the real audit history.

## Configuration

Copy `.env.example` to `.env` and configure the desired authentication and integration settings. Keep email and scheduling disabled until the staging workflow is verified. `.env` is ignored by Git. Environment variables override `.env` values.

Local development stores SQLite in `./data/audit.sqlite3`; containers use `/app/data/audit.sqlite3` on a persistent volume.

Run `python3 main.py inspect-bridge` to validate field mappings against the live `itso` OData metadata. `bridge_fields.json` documents the exact fields used. If Bridge changes the dataset, update the mapping and rerun inspection before restarting the job.

Run the job manually with `python3 main.py run`. It queries a rolling 24-hour window, with both Active status and entry time restricted server-side. Runs use a file lock and unique listing/audit constraints. A second run over the same listings creates no new audits or emails.

## Daily schedule

Set `SCHEDULER_ENABLED=true` to let the single application process check every five minutes for the daily 8:00 a.m. run in `APP_TIMEZONE` (default America/Toronto). It retries failed intake, deduplicates completed days, and resumes from the last successful intake boundary after downtime. Keep it false for UI-only staging. There are no operating-system-specific background services to install.

The first run uses `LISTING_WINDOW_HOURS`; subsequent successful intake boundaries allow recovery across longer outages. Listings that became inactive during an outage remain excluded by the product's Active-only rule. This is not a complete historical MLS replication service.

## Selection and delivery

For `N` newly processed listings, the job draws a binomial target by giving each listing the saved selection chance (5% by default). It then excludes brokerages and brokers audited within their configured cooldown periods. Among eligible brokerages, it chooses with a square-root listing-volume weight and picks a random listing from that brokerage. Each brokerage and broker can be chosen at most once in a run. This limits volume dominance while keeping the long-run rate near the configured percentage when enough brokerages remain eligible. If cooldowns leave fewer eligible brokerages than the target, the job records fewer audits.

The audit record is committed before SendGrid is called. HTTP failures become `email_failed` and can be retried from the staff page without creating a new audit. If the response is lost, the status is `email_unknown` and automatic retry is blocked because SendGrid may already have accepted the message. Staff should check SendGrid before any manual intervention. The SendGrid message ID, when returned, and every send attempt are stored.

The broker address comes from `Property.ListOfficeKey` → `Office.OfficeBrokerKey` → `Member.MemberEmail`. The brokerage address comes from the brokerage profile's `Office.OfficeEmail` and is copied when available; the listing agent address is `Property.ListAgentEmail` (with a Member fallback). Matching addresses are sent only one copy. Missing or invalid broker or agent email prevents sending and leaves a visible failed audit for staff. Test mode still sends only to `ADMIN_EMAIL` while showing the intended recipients for review.

## Limits

Bridge and SendGrid access require valid credentials and network access. SendGrid `202 Accepted` confirms acceptance for delivery, not arrival in the inbox. The app uses SQLite and a single-host file lock; if deployed across multiple hosts, move locking and the database to a shared transactional service. The UI shows the 200 most recent listings/audits and 50 most recent runs.

## Reference

The Bridge query and metadata structure follow the [RESO Bridge API examples](https://www.reso.org/web-api-examples/mls/bridge-api-generic/). The `itso` field map was checked against live Bridge metadata on September 24, 2026.
