# 18birdies-api

[![Test And Build](https://github.com/pfrederiksen/18birdies-api/actions/workflows/ci.yml/badge.svg)](https://github.com/pfrederiksen/18birdies-api/actions/workflows/ci.yml)

![Original illustration of golf scorecards and shot paths processed locally](assets/18birdies-banner.jpg)

**Your golf export. Your machine.**

An unofficial Python SDK and JSON CLI for **official 18Birdies account exports**.
Explore rounds, hole scores, recorded shots, measured club distances, course
history, and trends without uploading an export or handing over credentials.

This is an export-backed client, **not a live API**. Offline commands do not
contact the network. An explicitly invoked retrieval command downloads an export
through the official flow using direct HTTP or an optional browser fallback. It does not sync in the background.
It is not affiliated with, endorsed by, or supported by
18Birdies. The name identifies the compatible export format.

## Quick Start

Requires Python 3.11 or newer. Install from a checkout:

```sh
git clone https://github.com/pfrederiksen/18birdies-api.git
cd 18birdies-api
python -m pip install .
18birdies --export examples/synthetic-export.json summary --json
18birdies --version
```

The included export is entirely invented. No account or network access is
required to try it. The core has **zero third-party runtime dependencies**.
Windows, macOS, and Linux have a Python 3.11/3.12/3.13 CI matrix.

To work with your own data, obtain the JSON through the official 18Birdies
account-data download flow and keep the original outside this repository. A
read-only command does not create a store:

```sh
18birdies --export /private/account-export.json list --limit 5 --json
```

To create a local golf-only store:

```sh
18birdies import /private/account-export.json --json
18birdies list --limit 5 --json
18birdies round '18birdies:round:ROUND_ID' --json
18birdies summary --json
18birdies trends --json
18birdies courses --json
18birdies clubs --json
18birdies capabilities --json
18birdies snapshot --json
```

On Windows, use a normal quoted Windows path for your export. The default store
is `$XDG_DATA_HOME/eighteenbirdies/export.json` when that variable is absolute,
otherwise `~/.local/share/eighteenbirdies/export.json`. It never writes into the
installed package. Global `--store PATH` chooses another location. Global
`--export PATH` reads an export directly. Output is always JSON; `--json` is
accepted for clarity. Errors exit 2 without echoing private input.

## Retrieve Your Archive

The default retrieval command is **browser-free** and requires no extra packages:

```sh
18birdies retrieve --output /private/18Birdies_archive.json --timeout 300
18birdies --export /private/18Birdies_archive.json summary --json
18birdies import /private/18Birdies_archive.json --json
```

Run it in your own interactive terminal. It prompts for your email and password
with both inputs hidden. In this default terminal mode, credentials are never
accepted as command-line options
or read from environment variables, files, credential stores, or existing browser
sessions. They and the authentication token remain in process memory only. No
cookies or credentials are saved, and no automatic authentication retry occurs.
Python cannot guarantee erasure of process memory; files are not encrypted.

Use an existing private local directory and a new filename. Retrieval validates
the complete JSON account archive with the existing parser before atomically
publishing it without replacing any file or symlink. POSIX output uses mode
0600; Windows inherits account ACLs. The local filesystem must support hard
links. Retrieved archives include private account categories beyond golf.
Successful output reports byte count and SHA256 without account data or paths.
Use `import` explicitly to update a golf store.

The direct flow reproduces the email/password login and export POSTs observed
in the official website's public client assets. It is an unofficial integration,
not a published or guaranteed API. Phone verification, social sign-in, MFA,
CAPTCHA, redirects, access denial, and bot challenges are not automated. The
command stops with a safe error when a response requires unsupported action;
use the official account flow to resolve it. No browser launches automatically.

`--timeout` (30–900 seconds, default 300) sets a shared network deadline after
hidden credential entry. Individual socket operations have at most 30 seconds;
operating-system DNS resolution can have its own timeout. Human terminal input
has no deadline; Ctrl-C cancels cleanly. Responses are bounded (64 KiB for login,
64 MiB for the archive), must be uncompressed JSON, and are not redirected or
retried. Existing parser schema/depth/node/round/shot limits apply before saving.
Interrupted or invalid downloads are not published. Handled failures clean the
temporary file; abrupt process termination can leave a private temporary file.
Existing directory permissions are unchanged. Do not use a shared/synced folder.
Retrieval refuses to start when `DEBUG` or `PWDEBUG` is nonempty.

An optional **explicit browser fallback** is also available:

```sh
python -m pip install '.[retrieve]'
python -m playwright install chromium
18birdies retrieve --method browser --output /private/18Birdies_archive.json --timeout 900
```

This opens a fresh visible Chromium session at the
[official export page](https://18birdies.com/download-account-data/).
Sign in directly there, complete security checks yourself, then select
**Request My Data**. No browser profile is saved; closing the window cancels.
Its timeout bounds the interactive download wait; startup and initial navigation
each have a separate 30-second limit. It accepts the official UTF-8 JSON data-URL
download only, ignoring the remote filename. Encoded and decoded sizes are
bounded; compressed archives and other download URLs are rejected. Neither
method extracts archive members or accepts server-supplied local paths. Do not
enable Playwright tracing, debug logging, or video recording with private data.
The official website may load its own third-party resources; the package adds
no analytics.

See [protocol evidence and limits](docs/RETRIEVAL.md). Tests use invented data and
mock HTTP/browser responses; a real Chromium synthetic data-URL download was
also verified. A separately authorized cloud-env integration test retrieved and parsed an
official archive; no account data or credentials are included.

Saved cloud environment preparation is documented in
[Cloud preparation](docs/CLOUD_PREPARATION.md). The proposed proxy adapter has
offline synthetic coverage; `retrieve --method cloud` currently stops before
reading secrets or sending requests because proxy JSON substitution is unverified.
The separately selected `retrieve --method cloud-env` uses actual Personal Vault
environment-variable values through the required verified proxy transport. See
the cloud preparation guide for exact keys and scope; never put values in commands.

## Python SDK

```python
from eighteenbirdies import EighteenBirdiesClient

client = EighteenBirdiesClient.from_export("examples/synthetic-export.json")

for round_ in client.rounds.list(limit=5):
    print(round_["date"], round_["course"], round_["strokes"])

round_ = client.rounds.get("18birdies:round:demo-round-01")
shots = round_["holes"][0]["shots"]
summary = client.stats.summary()
trends = client.stats.trends()
courses = client.courses.list()
distances = client.club_distances()
coverage = client.coverage()
```

Import explicitly when persistence is useful:

```python
client = EighteenBirdiesClient.import_export(
    "/private/account-export.json",
    store_path="/private/golf.json",
    acquired_at=None,  # Set only when the actual download time is known.
)
client = EighteenBirdiesClient(store_path="/private/golf.json")
snapshot = client.snapshot()
```

Round dates default to UTC. Set `timezone_name="Europe/London"` in the SDK or
global `--timezone Europe/London` in the CLI for local calendar dates; exact
`timestamp_ms` and `date_utc` remain available. On platforms without IANA time
zone data, install `python -m pip install '.[timezones]'` for named zones. UTC
works without this optional extra.

See [the API reference](docs/API.md) for fields, source IDs, resource methods,
storage behavior, and validation limits. `python -m eighteenbirdies` runs the
same CLI as `18birdies`.

## What The Data Can Tell You

| Available when present in an export | Deliberately not inferred |
| --- | --- |
| Round totals and per-hole scores | A completed 18-hole round from a partial card |
| Recorded shots, clubs, distances, coordinates | All scoring strokes from the shot-entry count |
| Round putting, fairway and GIR statistics | Per-hole stats absent from the source |
| Measured club distance means and medians | Smart, carry, or full-swing distances |
| Course history and scoring by hole count | Nine- and eighteen-hole averages mixed together |
| Valid non-sentinel strokes-gained values | A genuine result from the unvalidated SG=100 marker |
| Source handicap values | An official GHIN handicap |

`null` means unknown. A verified zero remains zero. Invalid/zero denominators
do not become rates. The inspected schema's putt-hole denominators have not
been validated, so putts per hole stays unknown even when round putts exist.

Tracking coverage is explicit: scored holes without shots, missing club or
distance fields, unmatched shot-hole assignments, unscored slots, and score
disagreements are visible. Every hole having a shot still does not prove that
every putt, penalty, or shot was recorded. `shot_tracking_complete` remains null.
Future shot tracking cannot fill historical gaps.

## Local Data Handling

Import preserves the complete `myData.activityData` and
`myData.clubData.playedClubs` subtrees, including future fields. It excludes
other account categories. The retained golf data can still include sensitive
identifiers and precise coordinates. **The original export, imported store,
and CLI output are private data.** They are not encrypted by this package.

Imports use a local advisory lock, temporary file, flush/fsync, and atomic
replacement. POSIX files use mode 0600; new directories use 0700. Windows
inherits the current account's filesystem ACLs. Use a private local directory,
not a shared/network/synced drive. Existing directory permissions are untouched.

Provenance records the source SHA256/size, import time, and actual download time
only when explicitly supplied with `--acquired-at ISO8601`. Import time never
pretends to be download time. Identical exports keep existing metadata and bytes
unless a new, explicit actual acquisition time is supplied. Imports that omit
previously imported round IDs are rejected, preserving the prior store. Normal
edits to existing rounds and shot entries are accepted. Intentional reduced
archives should use a separate store.

No scheduled acquisition or ongoing-refresh promise is made. Re-export and
import when you need newer data. See [security notes](SECURITY.md) before sharing
logs or reports; bug reports must use invented examples only.

## Development

```sh
python -m pip install -e '.[dev,timezones]'
python -m unittest discover -s tests -v
python -m pytest -q
ruff check .
ruff format --check .
python -m build
python -m twine check dist/*
```

Tests use synthetic data only. CI tests three operating systems and three
Python versions, then validates the wheel/source archive and CLI. It does not
publish packages. See [contributing](CONTRIBUTING.md), [changelog](CHANGELOG.md),
and the [MIT license](LICENSE).
