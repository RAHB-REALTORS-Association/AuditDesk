# Changelog

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
