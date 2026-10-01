# Changelog

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
