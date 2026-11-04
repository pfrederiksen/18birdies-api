"""Explicit proxy-backed cloud input modes with no implicit fallback.

Raw environment input requires explicit cloud-env selection. Network-secret
JSON-body substitution/escaping remains unverified and that mode stays blocked.
"""

import http.client
from collections.abc import Callable, MutableMapping
from functools import partial
import os
from pathlib import Path
import ssl
from typing import Any, NoReturn
from urllib.parse import urlsplit

from . import direct
from .retrieval import RetrievalError, check_destination

EMAIL_KEY = "EIGHTEENBIRDIES_EMAIL_NETWORK_SECRET"
PASSWORD_KEY = "EIGHTEENBIRDIES_PASSWORD_NETWORK_SECRET"  # noqa: S105
ENV_EMAIL_KEY = "EIGHTEENBIRDIES_EMAIL_ENV"
ENV_PASSWORD_KEY = "EIGHTEENBIRDIES_PASSWORD_ENV"  # noqa: S105


def _proxy_factory(
    proxy_url: object,
    *,
    connection_factory: Callable[..., Any] | None = None,
    context: ssl.SSLContext | None = None,
) -> Callable[..., Any]:
    """Create verified HTTPS CONNECT connections, never a direct fallback.

    The caller supplies the configured HTTPS_PROXY (or https_proxy). NO_PROXY
    is deliberately not consulted: this adapter always requires the proxy.
    Default TLS trust includes the standard SSL_CERT_FILE/SSL_CERT_DIR settings.
    """
    hostname: str | None = None
    try:
        if not isinstance(proxy_url, str):
            raise TypeError
        proxy = urlsplit(proxy_url)
        hostname = proxy.hostname
        port = 80 if proxy.port is None else proxy.port
        valid = (
            proxy.scheme == "http"
            and isinstance(hostname, str)
            and hostname != ""
            and proxy.username is None
            and proxy.password is None
            and proxy.path in ("", "/")
            and not proxy.query
            and not proxy.fragment
            and 1 <= port <= 65535
        )
    except (TypeError, ValueError):
        valid = False
    if not valid or not isinstance(hostname, str):
        raise RetrievalError("A supported configured HTTP proxy is required") from None
    context = context or ssl.create_default_context()
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RetrievalError("Verified TLS is required")

    def connect(host: str, *, timeout: float) -> Any:
        if host != direct.HOST:
            raise RetrievalError("Unsupported retrieval host")
        factory = connection_factory or http.client.HTTPSConnection
        connection = factory(hostname, port, timeout=timeout, context=context)
        try:
            connection.set_tunnel(direct.HOST, 443)
        except BaseException:
            connection.close()
            raise
        return connection

    return connect


def _placeholder_credentials(values: MutableMapping[str, Any]) -> tuple[str, str]:
    # Placeholders are opaque, so email-format validation is inappropriate.
    # Never include mapping values in errors, reprs, logs or result objects.
    try:
        account, password = values[EMAIL_KEY], values[PASSWORD_KEY]
    except KeyError:
        raise RetrievalError("Required cloud placeholder keys are missing") from None
    if any(
        not isinstance(value, str) or not 1 <= len(value) <= 4096 for value in (account, password)
    ):
        raise RetrievalError("Invalid cloud placeholder input")
    return account, password


def _retrieve_synthetic(
    destination: Path,
    values: MutableMapping[str, Any],
    *,
    timeout: int = 300,
    connection_factory: Callable[..., Any],
) -> dict[str, Any]:
    """Offline integration seam: caller must inject a non-network test connection.

    This tests application serialization and transport, not proxy substitution.
    It never discovers or reads runtime credentials or falls back to raw input.
    """
    check_destination(destination, timeout)
    proxy_url = values.get("HTTPS_PROXY") or values.get("https_proxy")
    factory = _proxy_factory(proxy_url, connection_factory=connection_factory)
    return direct.retrieve_direct(
        destination,
        timeout=timeout,
        _credential_provider=partial(_placeholder_credentials, values),
        _post_request=partial(direct._post, _connection_factory=factory),
        # Placeholders are opaque tokens, not email addresses. This seam is offline.
        _validate_login=False,
    )


def retrieve_cloud(destination: Path, *, timeout: int = 300) -> NoReturn:
    """Fail before credential reads or network until JSON substitution is verified.

    No environment toggle or CLI bypass enables unverified live secret input.
    """
    check_destination(destination, timeout)
    raise RetrievalError(
        "Cloud network-secret JSON substitution is unverified; no request was sent"
    )


def retrieve_environment(
    destination: Path,
    *,
    timeout: int = 300,
    _environment: Any = None,
    _connection_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Explicit raw-value mode; never selected as a fallback from another mode.

    Only the two fixed input keys are consumed, then removed from this process's
    environment. No subprocess is started. Python and the OS cannot guarantee
    erasure of process memory or removal from a parent process's environment.
    """
    environment: Any = os.environ if _environment is None else _environment
    account = password = ""
    try:
        check_destination(destination, timeout)
        account = environment.pop(ENV_EMAIL_KEY, None)
        password = environment.pop(ENV_PASSWORD_KEY, None)
        try:
            account, password = direct.validate_login(account, password)
        except RetrievalError:
            raise RetrievalError(
                "Required cloud environment credential keys are missing or invalid"
            ) from None
        proxy_url = environment.get("HTTPS_PROXY") or environment.get("https_proxy")
        factory = _proxy_factory(proxy_url, connection_factory=_connection_factory)
        return direct.retrieve_direct(
            destination,
            timeout=timeout,
            _credential_provider=lambda: (account, password),
            _post_request=partial(direct._post, _connection_factory=factory),
        )
    except RetrievalError:
        raise
    except KeyboardInterrupt:
        raise RetrievalError("Retrieval cancelled; no authentication retry was attempted") from None
    except (OSError, http.client.HTTPException, ValueError, TypeError, UnicodeError):
        raise RetrievalError("Cloud environment retrieval failed; no retry was attempted") from None
    finally:
        environment.pop(ENV_EMAIL_KEY, None)
        environment.pop(ENV_PASSWORD_KEY, None)
        account = password = ""
