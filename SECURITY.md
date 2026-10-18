# Security

This project processes golf exports locally. The core SDK and CLI do not log in,
make network requests, upload data, or emit telemetry. They do not encrypt files.

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
