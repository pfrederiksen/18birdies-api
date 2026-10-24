# Official export flow: evidence and compatibility

Inspected 2026-10-24 without account credentials. The official entry point is
<https://18birdies.com/download-account-data/>. Public client assets linked by
that page establish the protocol, rather than guessed endpoints:

- [Sign-in component](https://18birdies.com/_astro/SignIn.9cfce424.js): for email
  input, sends `{"account": email, "password": password}` to
  `/usercentral/api/user/userLogin`. A successful JSON response has a `token`.
  `INVALID_ACCOUNT_OR_PASSWORD` is an observed rejection result. The component
  separately supports phone verification; this CLI does not automate that flow.
- [HTTP helper](https://18birdies.com/_astro/birdiesFetch.18a6879b.js): uses HTTPS
  on `18birdies.com`, POST, JSON content type, and the `Token` request header
  when a token is supplied.
- [Export component](https://18birdies.com/_astro/DownloadAccountData.41460882.js):
  sends `{}` to `/usercentral/api/user/getMyData` with that token. On receipt of
  a `myData` JSON object, the browser serializes the response into a UTF-8
  `data:text/plain;charset=utf-8,` URL named `18Birdies_archive.json`.

This is the official website's actual protocol, **not a published supported SDK
or API contract**. Assets, schema, authentication requirements, and availability
can change. Nothing here guarantees direct HTTP will be accepted for an account.
Only email/password is implemented for direct retrieval. The default terminal mode makes no credentialed requests until hidden input
succeeds in an interactive terminal. Explicit cloud-env input is documented in
[Cloud preparation](CLOUD_PREPARATION.md).
No terminal echo fallback is permitted. Tests use invented accounts and tokens.

Direct HTTP uses the standard library's verified TLS connection, a fixed host
and fixed paths, and disables HTTP debug output. Login and export each get one
attempt. The default terminal mode does not use proxies; cloud-env requires a verified
proxy transport. Redirects are never followed; there is no credential/token
refresh, cookie jar, or authentication cache. Status 401, 403, 429, redirects,
non-JSON or compressed responses, missing/invalid tokens, known challenge
indicators, and unknown sign-in results without a usable token stop the flow.
No challenge or access-denial response is followed by an alternate endpoint,
automatic browser fallback, or retry. Error messages never include provider
bodies, identifiers, passwords, tokens, URLs, or exception payloads.

A shared monotonic network deadline covers login and export reads, with each
socket operation capped at 30 seconds. DNS timeout remains controlled by the
operating system. Hidden terminal input precedes that deadline and can be
cancelled with Ctrl-C. No background work or ongoing synchronization is started.
Login JSON is limited to 64 KiB and archives to 64 MiB, regardless of declared
content length. Incomplete declared bodies, unsupported encodings, invalid JSON,
and parser-limit failures never publish an archive.

All acquisition methods share the validated atomic JSON writer. It writes a
same-directory private temporary file, flushes and fsyncs it, validates with the
existing offline parser, then uses a hard link to publish without overwriting an
existing file or symlink. It ignores browser-supplied filenames and never
extracts compressed paths. Temporary files are removed on handled exceptions.
Abrupt termination or filesystem cleanup failure may leave a private temporary
file; a filesystem that does not support hard links is rejected. A failure after
publication can leave a valid archive, so inspect the selected output before
retrying. Existing archives and golf stores are never replaced by retrieval.

A separately authorized cloud-env integration test successfully retrieved and
parsed an official archive through the configured proxy. No real account data
or credentials are included here. Tests use invented data and mocked HTTP/browser
responses; a real Chromium synthetic data-URL download also validated the
optional browser path. Service behavior can change independently of this package.
