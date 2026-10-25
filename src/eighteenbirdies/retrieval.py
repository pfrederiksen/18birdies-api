"""Explicit acquisition through the official account export flow."""

import hashlib
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import unquote_to_bytes

from .client import EighteenBirdiesClient, EighteenBirdiesError, MAX_BYTES

EXPORT_URL = "https://18birdies.com/download-account-data/"
PREFIX = "data:text/plain;charset=utf-8,"


class RetrievalError(EighteenBirdiesError):
    """An acquisition error with a fixed, safe user-facing message."""


def save_download(url: str, destination: Path) -> dict:
    """Validate the official JSON data URL and publish without replacing any file."""
    if not url.startswith(PREFIX) or len(url) > len(PREFIX) + MAX_BYTES * 3:
        raise RetrievalError("Unsupported download; expected the official JSON archive")
    data = unquote_to_bytes(url[len(PREFIX) :])
    if not data or len(data) > MAX_BYTES:
        raise RetrievalError("Archive is empty or exceeds the 64 MiB limit")
    return save_json(data, destination)


def save_json(data: bytes, destination: Path) -> dict:
    """Validate bounded JSON bytes and atomically publish without replacement."""
    if not data or len(data) > MAX_BYTES:
        raise RetrievalError("Archive is empty or exceeds the 64 MiB limit")
    temporary = None
    try:
        # Require an existing private directory; never trust the remote filename.
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            EighteenBirdiesClient.from_export(temporary)
        except EighteenBirdiesError:
            raise RetrievalError("Downloaded file failed official export validation") from None
        # A hard link publishes complete bytes atomically and fails on any existing
        # destination, including dangling symlinks. Both paths share a filesystem.
        os.link(temporary, destination)
    except FileExistsError:
        raise RetrievalError("Output already exists; choose a new output filename") from None
    except OSError:
        raise RetrievalError(
            "Cannot save archive; use an existing writable local directory"
        ) from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"source": "18birdies", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def check_destination(destination: Path, timeout: int) -> None:
    """Reject invalid options before credentials or network access."""
    if os.environ.get("DEBUG") or os.environ.get("PWDEBUG"):
        raise RetrievalError("Unset DEBUG and PWDEBUG before retrieving private account data")
    if not 30 <= timeout <= 900:
        raise RetrievalError("Timeout must be between 30 and 900 seconds")
    if not destination.parent.is_dir():
        raise RetrievalError("Output directory must already exist")
    if os.path.lexists(destination):
        raise RetrievalError("Output already exists; choose a new output filename")


def retrieve_browser(destination: Path, *, timeout: int = 300) -> dict:
    """Open a fresh browser. The user performs all authentication and requests."""
    check_destination(destination, timeout)
    try:
        from playwright.sync_api import sync_playwright, Error, TimeoutError
    except ImportError:
        raise RetrievalError(
            "Install the retrieval extra and browser: pip install '.[retrieve]'; "
            "python -m playwright install chromium"
        ) from None
    print(
        "Sign in directly in the opened browser and select Request My Data. "
        "Credentials are never entered in this terminal. Close the browser to cancel.",
        file=sys.stderr,
    )
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False, timeout=30000)
            try:
                context = browser.new_context(accept_downloads=False)
                page = context.new_page()
                page.set_default_navigation_timeout(30000)
                # Arm before navigation so even a fast user-triggered download is caught.
                with page.expect_download(timeout=timeout * 1000) as pending:
                    page.goto(EXPORT_URL, wait_until="domcontentloaded")
                return save_download(pending.value.url, destination)
            finally:
                browser.close()
    except KeyboardInterrupt:
        raise RetrievalError("Retrieval cancelled; no authentication retry was attempted") from None
    except TimeoutError:
        raise RetrievalError(
            "Export timed out; rerun and complete the official browser flow"
        ) from None
    except Error:
        raise RetrievalError(
            "Browser unavailable or closed; install Chromium or retry the official export flow"
        ) from None


def retrieve(destination: Path, *, timeout: int = 300, method: str = "direct") -> dict:
    """Direct HTTP is the default; browser use must be explicitly requested."""
    if method == "browser":
        return retrieve_browser(destination, timeout=timeout)
    if method == "cloud":
        from .cloud_transport import retrieve_cloud

        return retrieve_cloud(destination, timeout=timeout)
    if method == "cloud-env":
        from .cloud_transport import retrieve_environment

        return retrieve_environment(destination, timeout=timeout)
    if method != "direct":
        raise RetrievalError("Unknown retrieval method")
    from .direct import retrieve_direct

    return retrieve_direct(destination, timeout=timeout)
