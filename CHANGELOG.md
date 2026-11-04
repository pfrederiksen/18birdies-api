# Changelog

## Unreleased

- Documented that global `--store` comes before `import --new-store`. The
  shortened-archive error now shows `--store PATH import EXPORT --new-store`.
- Published exported scoring counts and fairway/green miss directions, with the
  existing `noStats` and denominator rules. Putts per hole stays null.
- Added score-to-par averages inside each hole-count bucket, course hole-stroke
  averages, and a source handicap series that remains labeled as not GHIN.
- `trends` now returns `months`, `last_10`, and `last_20`.
- Added round filters (`--since`, `--until`, `--course`, `--holes`, `--last`),
  club distance quartiles, and opt-in `--minimum-yards`.
- Added `coverage` and `handicap` commands, `--redact-identity`, and distinct
  CLI errors for a missing round, invalid JSON, and an unwritable store.
- Type-checked the client, report coverage from pytest, and check Python 3.14.
  Ruff now includes bugbear and pyupgrade. A weekly job checks that the public
  export page still names the login and archive paths.

## 0.2.0

- Added browser-free email/password retrieval, an explicit visible-browser
  fallback, and an explicit `cloud-env` proxy mode. `retrieve --method cloud`
  still stops before secrets or network access.
- Refused nonempty `DEBUG`, `PWDEBUG`, and `SSLKEYLOGFILE` before retrieval.
  The browser process does not inherit those variables, and a download is saved
  only while the page remains on `https://18birdies.com/`.
- Applied one email and password check to terminal input and `cloud-env`.
- Reported `"golf_only": false` for a retrieved archive and stated on stderr
  that the file is the full account export.
- Added `--redact-coordinates` and `import --new-store` for a shortened archive
  in a separate store.
- Added CodeQL, pip Dependabot, Ruff security checks, a mypy check of the
  retrieval modules, and an optional Chromium data-URL job.

## 0.1.0

- Introduced an offline SDK and JSON CLI for official account exports.
- Added rounds, hole scores, shot entries, measured club distances, course
  history, summaries, trends, and explicit capability/coverage diagnostics.
- Preserved unknown values, partial scorecards, source namespaces, and raw golf
  subtrees while excluding unrelated account categories from the local store.
- Added atomic local imports, portable advisory locks, provenance, unchanged
  export detection, and protection against accidentally reducing round history.
- Added synthetic examples and tests, packaging, and a cross-platform CI matrix.
