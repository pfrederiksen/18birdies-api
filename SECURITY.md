# Security

Explicit `retrieve --method cloud-env` consumes the two fixed Personal Vault
environment-variable inputs documented in `docs/CLOUD_PREPARATION.md`. Actual
values are available to task code. It removes the input keys from its own
environment before connecting, starts no subprocess, and sends JSON only to the
fixed official host over a required proxy with verified TLS. Values and tokens
remain in memory and are excluded from diagnostics. Parent-process environments
and process memory cannot be reliably erased by this Python process. Other modes
never fall back to these values. The network-secret mode remains blocked.

This project processes golf exports locally. Offline commands make no network
requests or telemetry calls. Explicit `retrieve` uses the official HTTPS
email/password export flow; hidden interactive input and the returned token are
kept in process memory, never persisted or logged. No redirects, automatic
retries, cookies, credential stores, or existing browser sessions are used.
Security challenges and denials stop direct retrieval. An explicitly selected
`--method browser` fallback opens a fresh visible browser for user authentication.
No persistent browser profile is created. Retrieved archives contain the complete
account data. Files are not encrypted; Python cannot guarantee memory erasure.
Do not enable debugging/tracing or terminal recording while entering credentials.

An account export may contain profile details, messages, contacts, identifiers,
and precise shot locations. Keep the original export outside your repository.
The imported store includes only the complete `activityData` and `playedClubs`
subtrees; those still contain private golf data and coordinates. Raw future
fields inside these approved subtrees are deliberately preserved. Treat CLI
JSON output as private too. File permissions are not a substitute for disk
encryption or access controls.

On POSIX, new store files/locks use mode 0600 and newly created directories use
0700. Existing parent-directory permissions are left alone. Windows uses the
current account's inherited filesystem ACLs; use a private user data directory.
Advisory locking and atomic replacement assume a local filesystem, not a shared
network drive. Verify your operating system's backups and sync-folder behavior.

Never attach a real account export, store, coordinates, credentials, cookies,
screenshots containing account data, or private logs to a public issue or pull
request. Use a small, entirely invented reproducer.

Report vulnerabilities through the repository's private security reporting
feature when it is available. Otherwise open a public issue saying only that
you need a private reporting channel; do not include exploit details or data.
Do not send credentials or live exports. Supported security fixes currently
target the latest release.
