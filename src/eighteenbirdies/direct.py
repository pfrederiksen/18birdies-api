"""Browser-free email/password export protocol observed in official public assets.

Default terminal mode uses no cookies, credential files, redirects, proxies,
retries, or endpoint discovery. Private injection seams support offline cloud
adapter tests. HTTP and provider errors never include response payloads.
"""

import getpass
import http.client
import json
from pathlib import Path
import sys
import time
import warnings

from .client import MAX_BYTES
from .retrieval import RetrievalError, check_destination, save_json

HOST = "18birdies.com"
LOGIN_PATH = "/usercentral/api/user/userLogin"
EXPORT_PATH = "/usercentral/api/user/getMyData"
AUTH_MAX_BYTES = 64 * 1024


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    return min(30.0, remaining)


def _set_timeout(connection, deadline, transport=None):
    timeout = _remaining(deadline)
    transport = transport if transport is not None else connection.sock
    if transport is not None:
        transport.settimeout(timeout)


def _post(
    path: str,
    payload: dict,
    token: str | None,
    deadline: float,
    limit: int,
    *,
    _connection_factory=None,
) -> bytes:
    """One TLS POST to the fixed official host. Never follow any redirect."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
    }
    if token is not None:
        headers["Token"] = token
    if path not in (LOGIN_PATH, EXPORT_PATH):
        raise RetrievalError("Unsupported retrieval path")
    factory = _connection_factory or http.client.HTTPSConnection
    connection = factory(HOST, timeout=_remaining(deadline))
    response = None
    try:
        connection.set_debuglevel(0)
        connection.connect()
        _set_timeout(connection, deadline)
        connection.request("POST", path, body=json.dumps(payload).encode("utf-8"), headers=headers)
        _set_timeout(connection, deadline)
        # Keep the transport reference even when getresponse detaches it for
        # a Connection: close response; body reads still need the deadline.
        transport = connection.sock
        response = connection.getresponse()
        if response.status == 401:
            raise RetrievalError("Authentication rejected or expired; no retry was attempted")
        if response.status in (403, 429):
            raise RetrievalError(
                "Access denied, rate limited, or security action required; direct retrieval stopped"
            )
        if 300 <= response.status < 400:
            raise RetrievalError(
                "Redirect refused; direct retrieval requires the official HTTPS flow"
            )
        if response.status != 200:
            raise RetrievalError("Official service returned an error; no retry was attempted")
        content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise RetrievalError(
                "Expected JSON; a sign-in challenge or unsupported response requires official action"
            )
        if response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise RetrievalError("Compressed HTTP responses are unsupported; no archive was saved")
        length = response.getheader("Content-Length")
        if length is not None:
            try:
                length = int(length)
            except ValueError:
                raise RetrievalError("Invalid response size; no archive was saved") from None
            if not 0 < length <= limit:
                raise RetrievalError("Official response is empty or exceeds its byte limit")
        chunks = bytearray()
        while True:
            _set_timeout(connection, deadline, transport)
            chunk = response.read1(min(65536, limit + 1 - len(chunks)))
            _remaining(deadline)
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > limit:
                raise RetrievalError("Official response exceeds its byte limit")
        if not chunks or (length is not None and len(chunks) != length):
            raise RetrievalError("Official response is empty or incomplete")
        return bytes(chunks)
    finally:
        try:
            if response is not None:
                response.close()
        finally:
            connection.close()


def _credentials() -> tuple[str, str]:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise RetrievalError("Direct retrieval requires an interactive terminal with hidden input")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            account = getpass.getpass("18Birdies email (hidden): ").strip()
            password = getpass.getpass("18Birdies password (hidden): ")
    except (getpass.GetPassWarning, EOFError, OSError):
        raise RetrievalError(
            "Secure hidden terminal input unavailable; no request was sent"
        ) from None
    if (
        not 3 <= len(account) <= 320
        or "@" not in account
        or any(character.isspace() for character in account)
        or not 1 <= len(password) <= 4096
    ):
        raise RetrievalError("An email address and nonempty password are required")
    return account, password


def retrieve_direct(
    destination: Path, *, timeout: int = 300, _credential_provider=None, _post_request=None
) -> dict:
    """Prompt securely, authenticate once, fetch once, and discard session state."""
    check_destination(destination, timeout)
    account = password = token = ""
    try:
        account, password = (_credential_provider or _credentials)()
        post = _post_request or _post
        # Human input is not counted against the network deadline. Never time out
        # a user halfway through typing a password or expose it to a fallback.
        deadline = time.monotonic() + timeout
        authentication = post(
            LOGIN_PATH, {"account": account, "password": password}, None, deadline, AUTH_MAX_BYTES
        )
        account = password = ""
        try:
            document = json.loads(authentication)
        except (ValueError, UnicodeError, RecursionError):
            raise RetrievalError(
                "Invalid sign-in response; direct retrieval is unsupported"
            ) from None
        authentication = b""
        if not isinstance(document, dict):
            raise RetrievalError("Unsupported sign-in response; use the official account flow")
        if document.get("result") == "INVALID_ACCOUNT_OR_PASSWORD":
            raise RetrievalError("Email/password rejected; no retry was attempted")
        result = document.get("result")
        challenge_fields = (
            "captcha",
            "captchaRequired",
            "mfaRequired",
            "twoFactorRequired",
            "challenge",
            "requiresVerification",
            "verificationRequired",
        )
        if any(document.get(field) for field in challenge_fields) or (
            isinstance(result, str)
            and any(
                marker in result.upper()
                for marker in ("CAPTCHA", "MFA", "CHALLENGE", "VERIFICATION", "TWO_FACTOR")
            )
        ):
            raise RetrievalError(
                "Security verification required; complete the official account flow"
            )
        token = document.get("token")
        document.clear()
        if (
            not isinstance(token, str)
            or not 1 <= len(token) <= 4096
            or any(not 33 <= ord(character) <= 126 for character in token)
        ):
            raise RetrievalError(
                "No usable token; MFA, CAPTCHA, verification, or another official action may be required"
            )
        archive = post(EXPORT_PATH, {}, token, deadline, MAX_BYTES)
        token = ""
        return save_json(archive, destination)
    except RetrievalError:
        raise
    except KeyboardInterrupt:
        raise RetrievalError("Retrieval cancelled; no authentication retry was attempted") from None
    except TimeoutError:
        raise RetrievalError("Network timeout; no authentication retry was attempted") from None
    except (OSError, http.client.HTTPException, ValueError, UnicodeError):
        raise RetrievalError("Secure HTTP request failed; no retry was attempted") from None
    finally:
        # References are discarded; Python cannot guarantee erasure of process memory.
        account = password = token = ""
