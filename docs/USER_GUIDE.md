# AuditDesk staff and manager guide

See the [documentation index](README.md) for operator, configuration, and developer guides.

Open the AuditDesk HTTPS address and sign in through Cloudflare Access. If identity succeeds but AuditDesk denies access, IT must grant your email an active application role.

| Role | Capabilities |
| --- | --- |
| Reviewer | View shared audits/listings/runs, record pass/fail results, retry failed emails, read reports and export brokerage PDFs |
| Audit manager | Reviewer capabilities plus individual/bulk assignments, selection percentage and email templates |
| IT administrator | Manager capabilities plus access management and activity-log export |

No one receives a role automatically merely by signing in. IT can change or disable access under **Access management**. Changes take effect on the next request. Bootstrap administrators are protected from demotion. Active accounts with permission to record audit results are available for assignment. Reviewer, Audit manager, and IT administrator roles currently have this permission. The activity log records the authenticated person making each change.

## Development previews

A PR preview opens without login as Demo Developer. The DEVELOPMENT SANDBOX banner identifies synthetic data. Delivery records are simulated and no email can be sent, including failed-audit notices or retries. Edits, assignments and results are available for UI testing and reset on restart. Real keys and enable flags cannot activate integrations.

## Everyday audit work

1. Review Audit history. Selected listings, intended/actual recipients, delivery state, reviewer, and outcome are shown together.
2. A manager assigns a reviewer. Unassigned unfinished audits are Not started; assigned unfinished audits are In progress. If an assigned account becomes inactive or loses auditing permission, unfinished work shows Needs reassignment.
3. Once the original request is accepted by SendGrid, choose Record result. A pass records the result without another email. For a failure, describe the issues, preview the notice, then record and send. Each audit receives one result.
4. Retry an explicitly failed email from the queue. An unknown delivery means SendGrid may have accepted it: contact IT to inspect provider delivery records before any intervention.
5. Use daily and brokerage reports for the app's intake history. Missing intake is not a zero day, and rates exclude pending results where appropriate. PDF export reflects the selected period.

Test mode redirects all messages to the configured administrator, and the banner shows that mode. EMAIL_ENABLED=false blocks all sends even in test mode. A recorded result may therefore have a pending notice until email is enabled and a permitted person resumes it.

## Choose visible columns

Use the **Columns** button at the right of a list header to show or hide columns. Its chooser floats over the list; click outside it or press Escape to close it. Audit history, listings, scheduled runs, daily reports, brokerage statistics, and the activity log each remember their own choices in your browser. The identifying column, audit selection checkboxes, and audit actions stay visible. **Show all columns** restores the default view. Press Escape to close the control.

Hiding columns changes the screen only. Sorting, bulk selection, branch expansion, and CSV/PDF exports still use the underlying records. If browser storage is unavailable, your choices apply until the page is refreshed.

## Manager controls

Selection settings affect new listings on future runs; they never reselect already processed listings. Brokerage/broker cooldowns may reduce the count below the lottery target.

Email editors provide preview and Save. Save applies to future requests and retries. Preview never sends. Failed-notice wording is managed separately. Invalid input remains in the editor for correction.

If another change occurs while a form is open, AuditDesk rejects the stale save. Use Back to retain entered text, copy it if necessary, then refresh and review the latest record before submitting again.

## Troubleshooting

- **401:** Your Access identity is missing, expired, or invalid; sign in again. IT should check issuer/audience configuration if it persists.
- **403:** Your application role, access status, request origin, or session token does not allow the operation. Reload before retrying a form; contact IT for role changes.
- **409:** The record revision changed. Refresh before saving.
- **429:** Wait one minute before submitting more actions.
- **500:** Give IT the displayed request ID; do not include tokens or credentials.

IT owns runtime configuration, backups and recovery. AuditDesk intentionally does not expose integration secrets in its interface.


## Edit the audit email

Open **Admin → Audit request email** in the staff sidebar. Edit the subject and body, use the `{{merge_tags}}` buttons to insert listing details, and select **Preview changes** to review the rendered message without saving or sending. Select **Save template** when it is ready. The saved wording is stored in the audit database and applies to future scheduled sends and manual retries without restarting the app. Previously sent emails are unchanged.

Select text in the message body and use **B**, **I**, or **U** to apply bold, italic, or underline. Preview shows the formatted email. SendGrid receives both a formatted HTML body and a readable plain-text version. Existing saved plain-text templates remain editable and are converted to the rich editor when next saved.

Available merge tags are `{{mls_number}}`, `{{address}}`, `{{agent_name}}`, `{{brokerage_name}}`, `{{broker_name}}`, and `{{broker_first_name}}`. The broker's first name comes from Bridge `MemberFirstName`. Older saved listings use the first name from `MemberFullName` when available. The body must contain the first four tags so every request identifies the listing and agent. An unknown or incomplete tag is rejected before saving. The editor shows the test-mode recipient diagnostic in its preview while TEST MODE is active.


## Record an audit result

After the original request email is accepted, open **Record result** on its Audit history row. **Mark passed** records a pass without sending another email. For a failed audit, enter the issues, select **Preview failed notice** to review the rendered message and recipients, then select **Record fail and send notice**. The issues are saved with the audit and included through the `{{issues}}` merge tag. Each audit can receive one result, preventing duplicate failed-audit notices from repeated submissions.

Edit the follow-up wording under **Admin → Failed-audit email**. Its subject and body support the same bold, italic, and underline controls and merge tags as the original request, plus `{{issues}}`. The failure body must include `{{mls_number}}`, `{{address}}`, and `{{issues}}`. A failed SendGrid attempt can be retried from Audit history without recording another result; uncertain sends are not automatically retried. In test mode, the notice goes only to `ADMIN_EMAIL` and obeys the test-window cutoff. A test audit cannot later send a production failure notice to a broker.


## Track who is working on an audit

IT manages accounts and roles under **Admin → Access management**. Only active accounts whose role permits auditing appear in assignment dropdowns. There is no separate audit-team list. In **Audit history**, choose an account in the **Assigned to** dropdown and save.

For bulk assignment, select audit rows with their checkboxes, or use the heading checkbox to select all displayed audits (up to the latest 200). The toolbar shows the selection count. Choose an account under **Assign selected to**, then select **Apply to selected**. Choose **Unassigned — clear assignment** to remove assignments from the selected rows. **Clear selection** unchecks rows without changing their assignments. Selection survives table sorting, but resets after saving or refreshing.

The **Work status** column shows **Not started** when no one is assigned, **In progress** when an eligible account is assigned, **Needs reassignment** when that account is unavailable, and **Completed** after a pass or fail result is recorded. Clearing an assignment returns an unfinished audit to **Not started**. Assignment changes do not send email or change results. Bulk changes save together: an invalid account, missing audit, or stale form rejects the whole batch. Each changed audit records the authenticated actor in the activity log.


## Change selection settings

Open **Admin → Selection settings** to set a percentage from 0% to 100%, with up to two decimal places. The saved value applies to new listings in future runs without restarting the app. It does not reselect listings already processed, change earlier audit records, or reopen an ended test window. The setting is stored in the audit database; `AUDIT_RATE` is the starting value until a manager saves a percentage.

The **Brokerage cooldown** control sets how many whole days must pass before another listing from the same brokerage office can be selected. Choose 0–365 days; 0 removes the wait between runs, while the existing one-selection-per-office-per-run limit still applies. The saved value applies to future selections and does not change earlier audits. `BROKERAGE_COOLDOWN_DAYS` supplies the starting value until a manager saves a cooldown. The broker cooldown remains a separate configuration setting. Audit managers and IT administrators can change these settings.


## Review daily audit volume

Open **Admin → Daily audit report** for the past 90 local calendar days. It shows the number of unique new Active listings first processed by the app each day, how many were selected for audit, and the audited percentage. Manual test runs are included. Days with no completed run show unavailable values rather than a misleading zero; a completed run that found no new listings shows zero. The report is calculated from the persistent listing, audit, and run records, so it survives dashboard restarts. It does not represent all listings in the MLS if the app missed a daily intake.


## Review brokerage statistics

Open **Admin → Brokerage statistics** to compare brokerages for a rolling 3-month, 6-month, or 1-year period. Each row shows unique new Active listings first processed by the app, how many received an audit selection, the audited percentage, and passed and failed counts and rates. Pass and fail rates use completed audits only; pending audits are excluded, and a rate is unavailable until the brokerage has a completed audit. Branches with the same brokerage name (ignoring capitalization and extra spaces) are combined into one row, with their counts and rates calculated together. Select **View branches** to see each office's Bridge profile address and its own counts and rates; brokerage rows remain grouped while sorting. Listings without a brokerage name retain their Bridge office ID as a separate group. Manual test runs are included. Use **Export branded PDF** to download a management report for the selected period, including all branch addresses. Both views show when app history begins; an earlier portion of a selected period cannot be filled from missing intake history. PDF support is included in the locked runtime dependencies. If export is unavailable, ask IT to verify the deployed environment.
