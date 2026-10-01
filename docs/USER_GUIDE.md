# Roles and workflows

Open the AuditDesk HTTPS address and sign in through Cloudflare Access. If identity succeeds but AuditDesk denies access, IT must grant your email an active application role. Assignment to the reviewer roster alone does not grant login access.

| Role | Capabilities |
| --- | --- |
| Reviewer | View shared audits/listings/runs, record pass/fail results, retry failed emails, read reports and export brokerage PDFs |
| Audit manager | Reviewer capabilities plus assignments, roster, selection percentage and email templates |
| IT administrator | Manager capabilities plus access management and activity-log export |

No one receives a role automatically merely by signing in. IT can change or disable access under **Access management**. Changes take effect on the next request. Bootstrap administrators are protected from demotion. Names in the audit roster describe responsibility for work; the activity log separately records the authenticated person making each change.

## Development previews

A PR preview opens without login as Demo Developer. The DEVELOPMENT SANDBOX banner identifies synthetic data. Delivery records are simulated and no email can be sent, including failed-audit notices or retries. Edits, assignments and results are available for UI testing and reset on restart. Real keys and enable flags cannot activate integrations.

## Everyday audit work

1. Review Audit history. Selected listings, intended/actual recipients, delivery state, reviewer, and outcome are shown together.
2. A manager assigns a reviewer. Unassigned unfinished audits are Not started; assigned unfinished audits are In progress. Removed roster entries preserve historical names and flag unfinished work for reassignment.
3. Once the original request is accepted by SendGrid, choose Record result. A pass records the result without another email. For a failure, describe the issues, preview the notice, then record and send. Each audit receives one result.
4. Retry an explicitly failed email from the queue. An unknown delivery means SendGrid may have accepted it: contact IT to inspect provider delivery records before any intervention.
5. Use daily and brokerage reports for the app's intake history. Missing intake is not a zero day, and rates exclude pending results where appropriate. PDF export reflects the selected period.

Test mode redirects all messages to the configured administrator, and the banner shows that mode. EMAIL_ENABLED=false blocks all sends even in test mode. A recorded result may therefore have a pending notice until email is enabled and a permitted person resumes it. Simulation uses synthetic data without real messages or intake.

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
