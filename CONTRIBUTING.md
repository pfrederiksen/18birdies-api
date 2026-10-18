# Contributing

Keep the core local-first and dependency-free. Network access, authentication,
telemetry, or publication behavior must never be added implicitly.

```sh
python -m venv .venv
# Activate the environment using your shell's usual command.
python -m pip install -e '.[dev,timezones]'
python -m unittest discover -s tests -v
python -m pytest
ruff check .
ruff format --check .
python -m build
python -m twine check dist/*
```

Use only synthetic fixtures. Preserve unknown/null values, the distinction
between scoring strokes and recorded shots, source identifiers, and exact
timestamps. Add focused tests for schema changes, corrected exports, imports,
and platform-dependent storage behavior. Preserve all approved raw golf fields
even when the normalized interface does not yet understand them.

Before opening a pull request, inspect its complete diff for account data,
private paths, credentials, real course history, and coordinates. Do not copy a
real export and redact selected fields: construct the fixture from scratch.
Limit changes to one clear behavior. Describe its effect and the checks run.

CI builds distributions for validation only. Releases and package-index uploads
are manual maintainer operations; the workflow contains no publishing step.
