# MLS Audit Desk

A small internal application for selecting new Active Bridge listings for paperwork audits. The daily job records every listing considered, uses a configurable selection lottery, applies brokerage and broker cooldowns, then sends selected requests through SendGrid. The staff page shows audit history and failed-email retries.

## Start the staff interface

The interface runs at **http://127.0.0.1:8765**. Start it with:

```sh
python3 main.py serve
```

Sign in with `APP_USERNAME` and `APP_PASSWORD` from the local `.env` file. It binds to localhost by default. For access from other computers, place it behind an authenticated HTTPS reverse proxy; do not expose its Basic Auth endpoint directly to the internet.

The orange **TEST MODE** banner appears whenever `APP_ENV` is `development` or `test`. Production email requires `APP_ENV=production` and complete configuration. Test mode passes only `ADMIN_EMAIL` to SendGrid as a recipient; intended broker, office, and agent addresses appear in the subject/body for diagnosis and in the audit record.

For a time-limited live-data test, set `APP_ENV=test` and `TEST_MODE_END_AT` to an ISO 8601 timestamp with an offset, such as `2026-09-28T00:00:00-04:00`. Once that time passes, scheduled runs record `test_window_closed` without querying Bridge, and both scheduled and manual email sends are blocked. The launchd schedule remains installed until removed.

## Edit the audit email

Open **Admin → Audit request email** in the staff sidebar. Edit the subject and body, use the `{{merge_tags}}` buttons to insert listing details, and select **Preview changes** to review the rendered message without saving or sending. Select **Save template** when it is ready. The saved wording is stored in the audit database and applies to future scheduled sends and manual retries without restarting the app. Previously sent emails are unchanged.

Select text in the message body and use **B**, **I**, or **U** to apply bold, italic, or underline. Preview shows the formatted email. SendGrid receives both a formatted HTML body and a readable plain-text version. Existing saved plain-text templates remain editable and are converted to the rich editor when next saved.

Available merge tags are `{{mls_number}}`, `{{address}}`, `{{agent_name}}`, `{{brokerage_name}}`, `{{broker_name}}`, and `{{broker_first_name}}`. The broker's first name comes from Bridge `MemberFirstName`. Older saved listings use the first name from `MemberFullName` when available. The body must contain the first four tags so every request identifies the listing and agent. An unknown or incomplete tag is rejected before saving. The editor shows the test-mode recipient diagnostic in its preview while TEST MODE is active.

## Record an audit result

After the original request email is accepted, open **Record result** on its Audit history row. **Mark passed** records a pass without sending another email. For a failed audit, enter the issues, select **Preview failed notice** to review the rendered message and recipients, then select **Record fail and send notice**. The issues are saved with the audit and included through the `{{issues}}` merge tag. Each audit can receive one result, preventing duplicate failed-audit notices from repeated submissions.

Edit the follow-up wording under **Admin → Failed-audit email**. Its subject and body support the same bold, italic, and underline controls and merge tags as the original request, plus `{{issues}}`. The failure body must include `{{mls_number}}`, `{{address}}`, and `{{issues}}`. A failed SendGrid attempt can be retried from Audit history without recording another result; uncertain sends are not automatically retried. In test mode, the notice goes only to `ADMIN_EMAIL` and obeys the test-window cutoff. A test audit cannot later send a production failure notice to a broker.

## Track who is working on an audit

Open **Audit team** to add names, correct a name, or remove a name from future assignments. Removing a name keeps it on audits already assigned to that person; it can be restored later. In **Audit history**, choose a name in the **Assigned to** dropdown and save. The **Work status** column shows **Not started** when no one is assigned, **In progress** when someone is assigned, and **Completed** after a pass or fail result is recorded. Clearing an assignment returns an unfinished audit to **Not started**. Assignment changes do not send email. The staff login is shared, so the selected name is a manual assignment rather than an authenticated user identity.

## Change the audit selection percentage

Open **Admin → Selection settings** to set a percentage from 0% to 100%, with up to two decimal places. The saved value applies to new listings in future runs without restarting the app. It does not reselect listings already processed, change earlier audit records, or reopen an ended test window. The setting is stored in the local audit database; `AUDIT_RATE` in `.env` is the starting value until an Admin value is saved. The current shared dashboard login can access Admin settings.

## Simulate a full cycle

Open **Simulation** in the staff sidebar, or run `python3 main.py simulate`. This uses synthetic listings and a temporary database. It demonstrates the 24-hour and Active filters, brokerage cooldown, audit creation, test-mode recipient substitution, a simulated SendGrid failure, idempotent second run, and successful manual retry. It does not call Bridge or SendGrid and does not change the real audit history.

## Configuration

Copy `.env.example` to `.env`, add the Bridge and SendGrid keys, a verified sender address, an administrator address, and a long staff password. `.env` is ignored by Git. Environment variables override `.env` values.

Keep the SQLite database in `~/Library/Application Support/MLS Audit Desk/`. The macOS login service can access that folder reliably; keeping the runtime database under Documents caused intermittent file-access errors on this Mac.

Run `python3 main.py inspect-bridge` to validate field mappings against the live `itso` OData metadata. `bridge_fields.json` documents the exact fields used. If Bridge changes the dataset, update the mapping and rerun inspection before restarting the job.

Run the job manually with `python3 main.py run`. It queries a rolling 24-hour window, with both Active status and entry time restricted server-side. Runs use a file lock and unique listing/audit constraints. A second run over the same listings creates no new audits or emails.

## Daily schedule

The included macOS launchd file runs the job daily at **8:00 a.m. local machine time**. Keep the Mac's timezone set to America/Toronto. The launchd files assume the project is at `~/Documents/Audit App`. If you put it elsewhere, update the `cd` command in each plist before installing. They contain no credentials or personal home-directory path. Install the daily job with:

For a laptop that may sleep at 8:00 a.m., set `WAKE_CATCHUP=true` while the dashboard service is running. After the Mac wakes, the dashboard checks whether a run has occurred since that morning's 8:00 a.m. boundary and starts one if needed. The check runs every five minutes while the dashboard is active. It uses the same job lock and listing deduplication as the scheduled run.

```sh
cp launchd/com.cornerstone.mls-audit.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cornerstone.mls-audit.plist
```

To remove the schedule, run `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.cornerstone.mls-audit.plist`.

The interface can also be started automatically at login:

```sh
cp launchd/com.cornerstone.mls-audit-ui.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cornerstone.mls-audit-ui.plist
```

## Selection and delivery

For `N` newly processed listings, the job draws a binomial target by giving each listing the saved selection chance (5% by default). It then excludes brokerages and brokers audited within their configured cooldown periods. Among eligible brokerages, it chooses with a square-root listing-volume weight and picks a random listing from that brokerage. Each brokerage and broker can be chosen at most once in a run. This limits volume dominance while keeping the long-run rate near the configured percentage when enough brokerages remain eligible. If cooldowns leave fewer eligible brokerages than the target, the job records fewer audits.

The audit record is committed before SendGrid is called. HTTP failures become `email_failed` and can be retried from the staff page without creating a new audit. If the response is lost, the status is `email_unknown` and automatic retry is blocked because SendGrid may already have accepted the message. Staff should check SendGrid before any manual intervention. The SendGrid message ID, when returned, and every send attempt are stored.

The broker address comes from `Property.ListOfficeKey` → `Office.OfficeBrokerKey` → `Member.MemberEmail`. The brokerage address comes from the brokerage profile's `Office.OfficeEmail` and is copied when available; the listing agent address is `Property.ListAgentEmail` (with a Member fallback). Matching addresses are sent only one copy. Missing or invalid broker or agent email prevents sending and leaves a visible failed audit for staff. Test mode still sends only to `ADMIN_EMAIL` while showing the intended recipients for review.

## Limits

Bridge and SendGrid access require valid credentials and network access. SendGrid `202 Accepted` confirms acceptance for delivery, not arrival in the inbox. The app uses SQLite and a single-host file lock; if deployed across multiple hosts, move locking and the database to a shared transactional service. The UI shows the 200 most recent listings/audits and 50 most recent runs.

## Reference

The Bridge query and metadata structure follow the [RESO Bridge API examples](https://www.reso.org/web-api-examples/mls/bridge-api-generic/). The `itso` field map was checked against live Bridge metadata on September 24, 2026.
