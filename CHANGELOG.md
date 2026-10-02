# Changelog

## Unreleased — account deletion and listing recovery

- Add confirmed person deletion under Manage → Access, preserving audit history and clearing assignments; protect bootstrap administrators, your own account, and the last active administrator.
- Refresh up to 20 selected failed or pending audit requests from Bridge after upstream listing/contact corrections, rebuilding recipients without sending email.
- Preserve selection and delivery history, recheck Cornerstone/member eligibility, skip sent or completed audits, and reject conflicting or incomplete refresh batches.

## Unreleased — Manage, list controls, and delivery visibility

- Group selection settings, request/failure email wording, and account access under one role-aware Manage workspace.
- Persist brokerage office cooldown, broker cooldown, and initial listing window; apply validated business settings to future runs and record changes in activity history.
- Upgrade to schema version 4 additively, preserving saved selection percentages, office cooldowns, and audit data from version 3.
- Combine search, status filters, and column visibility in one compact list control.
- Add row counts and First/Previous/Next/Last controls to all six table views; search and sort complete matching histories beyond the former recent-record limits.
- Keep brokerage branches together and retain bulk selections across pages, filters, sorting, and browser Back navigation; exports keep their existing scope.
- Remove the sidebar's daily-selection timezone footer.
- Include recipient-validation failures in scheduled run email error summaries; surface unresolved errors on older completed runs and distinguish pending mail.
- Keep completed intake from being repeated merely because a notification failed.

## Unreleased — PDF pagination fix

- Allow large brokerage groups to span PDF pages instead of failing with a layout error.
- Keep brokerage headings with the first branch, label continuation branches with their brokerage name, and repeat table headers.
- Cover multi-page groups and authenticated PDF downloads for all reporting periods.

## Unreleased — email editor fix

- Replace the message-body backing textarea with a native hidden input so only the rich editor is visible.

## Unreleased — mobile navigation icons

- Replace mobile menu text with hamburger and close icons, retaining accessible labels and 44px touch targets.

## Unreleased — list column controls

- Add a compact Columns button in each list header, opening a floating chooser with browser-saved preferences and a Show all columns reset.
- Keep identifying columns, audit selection, and actions visible; retain sorting, branch expansion, bulk operations, and complete exports.

## Unreleased — remove the staff simulation page

- Remove Simulation from staff navigation, tab routing, and presentation styles.
- Keep isolated fixtures and the command-line simulation for developer verification.

## Unreleased — account assignments and bulk operations

- Remove the Audit team page, roster routes, and roster-only controls; active account roles determine assignment eligibility.
- Schema version 2 replaces development roster fields and data with account assignment references.
- Add row selection, select-all, selection counts, and atomic bulk assignment/clearing in Audit history.
- Reject unavailable accounts, missing audits, duplicate selections, and stale batches without partial updates; log each affected audit.

## Unreleased — interface polish

- Give history columns room to read, limit sticky actions to audit history, and unify report/simulation card spacing.
- Normalize editor, outcome, and access-form padding; align checkbox controls and stack forms on narrow screens.
- Align the application shell with Cornerstone Signatures: shared color tokens, system typography, compact navigation, and a separate identity bar.

## Unreleased — repository organization

- Add the MIT license and contributor licensing guidance.
- Separate CLI commands from the compatibility launcher; add the equivalent `python -m audit_app` entry point.
- Split HTML components into helpers, workflow, management, email, and report views; move local HTTP startup into runtime and the email editor script into static assets.
- Rewrite the README with a product overview, quick start, environment comparison, module map, and documentation links.
- Expand the staff guide and add architecture, configuration, contribution, and private security-reporting docs, plus a pull request template.
- Preserve the original handoff as historical project context and focus contributor instructions on current development rules.

## Unreleased — deployment foundation

- Cloudflare Access JWT authentication and application-owned Reviewer, Audit manager, and IT administrator roles.
- Flask/Gunicorn HTTP runtime, CSRF/origin checks, bounded forms, identity-based rate limits, and transactional stale-form protection.
- Authenticated mutation history and CSV export; protected bootstrap administrator access.
- Non-root container, persistent SQLite storage, health endpoint, reproducible dependencies, CI and versioned image releases.
- Portable scheduling and locks; removed macOS launchd services and platform-specific data paths.
- Failed-intake retry and downtime catch-up; test-to-production email protection and disabled-send switch.
- Online backup, validated offline restore, responsive navigation, and operator/user documentation.

- Removed Basic Auth and shared username/password configuration entirely; Cloudflare Access authenticates test/production; development is an open synthetic sandbox. Email delivery now defaults to disabled in configuration as well as containers.

- Disposable development/PR previews: seeded synthetic audits, fixed demo identity, ignored integration credentials, hard Bridge/email guards, no scheduler or volume, and retained form protection.

## Unreleased — administrator recovery controls

- Add Manage → Recovery for IT administrators to download consistent database snapshots and validate/stage restore uploads.
- Check uploaded database structure, integrity, supported schema, migration, and administrator access without changing live state; preserve original backup schema and show counts, SHA-256, and restore instructions.
- Keep replacement offline, preserve pre-restore snapshots, restrict recovery to administrators in test/production, and record export requests and restore staging in activity history.

- Consolidate selection percentage, office and individual broker cooldowns, and intake window into one consistently styled form with an atomic Save action.

- Add a manual Asana follow-up handoff for recorded failed audits: prefilled task draft, complete copyable details, and optional saved task link. No API credentials or synchronization. Schema 5 adds the optional task URL while preserving existing data.

- Make Asana task creation a visible action on failed audit rows, with a separate link for saving task URLs/copying details. Recording a failure opens its follow-up page immediately.

- Style access-denied, validation, missing-page, and server errors with a consistent standalone AuditDesk page, return action, and request ID. Styling works before authentication without exposing application assets.

- Enforce Cornerstone-only intake using Bridge `OriginatingSystemName`, with local checks before selection and every audit action/delivery. Exclude BRREA, other boards, and unverified records from listings, audit history, and reports. Schema 6 stores board provenance; a no-mail backfill verifies historical records without deleting data.

- Exclude Cornerstone-originated interboard listings identified by `ListAgentMlsId = NONMEM` before contact lookup and audit selection. Apply the same restriction to existing audit actions, delivery retries, lists, counts, and reports. Schema 7 stores the agent MLS identifier; rerun `backfill-listing-boards` after deployment to verify existing data. Missing identifiers remain blocked; missing broker emails alone retain the contact-error workflow.
