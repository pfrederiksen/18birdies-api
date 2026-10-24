# Explicit cloud environment retrieval

`retrieve --method cloud-env` is an opt-in alternative to hidden terminal input.
It reads raw credential values from exactly these environment-variable keys:

- `EIGHTEENBIRDIES_EMAIL_ENV`
- `EIGHTEENBIRDIES_PASSWORD_ENV`

Raw values are available to task code. Configure them through your environment's
secret-input controls, scoped only to the intended environment. Never place
values in shell commands, command arguments, committed files, logs, or CI.
Exclude them from unrelated build and test subprocess environments.

The runtime must permit HTTPS to `18birdies.com:443` and supply `HTTPS_PROXY`
(or `https_proxy`). Environment credential configuration does not grant network
access. In a saved environment, publish configuration changes before starting a
new task and check presence and permission metadata without printing values.
Obtain the account owner's authorization before a live retrieval.

```sh
18birdies retrieve --method cloud-env --output /private/new-archive.json --timeout 300
```

Use an existing private directory and a new filename. The adapter consumes both
credential keys before connecting and removes them from its own environment on
success or failure. It starts no subprocess. Credentials and returned tokens
remain in process memory; Python cannot guarantee erasure or remove them from a
parent process's environment. Normal JSON serialization preserves quotes,
backslashes, and non-ASCII characters.

## Required transport

The adapter uses HTTPS CONNECT through the configured plain HTTP proxy, with a
fixed official host and port. Proxy URLs containing credentials, custom paths,
or query strings are rejected. It never consults `NO_PROXY` or falls back to a
direct connection. TLS certificate and hostname verification are required;
standard system trust and `SSL_CERT_FILE`/`SSL_CERT_DIR` apply.

Login and export each get one attempt under a shared deadline. Redirects, access
denials, known security challenges, unsupported responses, and invalid archives
stop retrieval. No credentials, provider bodies, or tokens appear in diagnostics.
Saved archives contain private account data; keep them outside the repository.
See [protocol evidence and limits](RETRIEVAL.md).

## Disabled network-secret mode

`retrieve --method cloud` fails before credential lookup or network access.
The proposed keys `EIGHTEENBIRDIES_EMAIL_NETWORK_SECRET` and
`EIGHTEENBIRDIES_PASSWORD_NETWORK_SECRET` are opaque placeholders, not raw-value
inputs. Proxy JSON-body substitution and escaping semantics are unverified.
There is no toggle or fallback that enables this mode. Do not configure raw
credentials under those keys.

Offline synthetic tests inject fake connections and invented placeholders to
verify serialization and the transport contract. They do not establish that a
platform proxy safely substitutes secrets into JSON. Enabling that route requires
separately validated substitution semantics and a reviewed implementation change.
