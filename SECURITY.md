# Security reporting

Report suspected vulnerabilities privately to Cornerstone IT through your established internal support channel. External reporters should contact repository maintainers to arrange a private report before sharing technical details. Do not put exploit details, listing/contact data, database exports, credentials, cookies, or tokens in public issues or pull requests.

Include the affected commit or image, a minimal reproduction using synthetic data, expected and observed behavior, and potential impact. Maintainers can arrange a secure channel for additional evidence.

Operators own Cloudflare Access policy, runtime secrets, HTTPS/origin routing, backups, and deployment updates. AuditDesk validates signed Access identity and enforces application permissions. Development is an open synthetic sandbox; live test/production environments require Cloudflare authentication.

Consult the [deployment guide](docs/DEPLOYMENT.md) for the single-instance runtime and recovery procedure. There is no published version support schedule; report the deployed commit so maintainers can assess the issue accurately.
