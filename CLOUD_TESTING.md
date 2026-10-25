# Testing export retrieval

The suite uses invented archives, credentials, tokens, and mocked HTTP/browser
connections. Run tests without real credential environment variables. No live
account or browser is needed for the offline suite.

## Reproduce CI (Python 3.11+)

```sh
python -m venv .venv
# Activate the virtual environment for your operating system.
python -m pip install -e '.[dev,timezones]'
python -m unittest discover -s tests -v
python -m pytest -q
ruff check .
ruff format --check .
python -m build
python -m twine check dist/*
python -m pip install --no-deps --force-reinstall dist/*.whl
18birdies --export examples/synthetic-export.json summary --json
python -m eighteenbirdies --export examples/synthetic-export.json capabilities --json
```

The retrieval snapshot has 94 tests, covering offline parsing, private storage,
HTTP deadlines and limits, safe failures, explicit browser selection, proxy TLS,
and explicit environment credential consumption. CI checks Linux, macOS, and
Windows on Python 3.11, 3.12, and 3.13. No type checker is configured.

`retrieve` defaults to direct HTTPS with hidden terminal input. Explicit
`--method cloud-env` supports the fixed environment-variable keys documented in
[Cloud preparation](docs/CLOUD_PREPARATION.md), requiring a configured proxy and
verified TLS. The separate `--method cloud` remains disabled because proxy
JSON-body secret substitution is unverified.

A separately authorized browser-free cloud-env integration test retrieved and
parsed an official archive successfully. Account data and test credentials are
excluded from this repository. That result demonstrates one compatible execution;
it does not guarantee service availability or authentication for other accounts.
Do not run live retrieval in CI or retry authentication automatically.
