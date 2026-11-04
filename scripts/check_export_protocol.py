#!/usr/bin/env python3
"""Fail when the public export page no longer names the known API paths.

No credentials are sent. Responses are size-capped, and redirects that leave
https://18birdies.com are refused. Failure text names the missing path only.
"""

import sys
from http.client import HTTPMessage
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from eighteenbirdies.protocol_assets import (
    OFFICIAL_EXPORT_PAGE,
    REQUIRED_PATHS,
    imported_scripts,
    linked_scripts,
    paths_present,
)

MAX_SCRIPTS = 25

MAX_BYTES = 2_000_000
TIMEOUT = 20


class _OfficialRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        parts = urlsplit(newurl)
        if parts.scheme != "https" or parts.hostname != "18birdies.com":
            raise URLError("redirect left the official host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read(url: str) -> str:
    request = Request(url, headers={"Accept": "text/html,application/javascript,*/*"})  # noqa: S310
    with build_opener(_OfficialRedirect).open(request, timeout=TIMEOUT) as response:  # noqa: S310
        final = urlsplit(response.geturl())
        if final.scheme != "https" or final.hostname != "18birdies.com":
            raise URLError("response left the official host")
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise URLError("response exceeds the size limit")
    return data.decode("utf-8", errors="replace")


def main() -> int:
    try:
        page = _read(OFFICIAL_EXPORT_PAGE)
        pending = linked_scripts(page)
        seen: list[str] = []
        bodies: list[str] = []
        while pending and len(seen) < MAX_SCRIPTS:
            url = pending.pop(0)
            if url in seen:
                continue
            seen.append(url)
            body = _read(url)
            bodies.append(body)
            for child in imported_scripts(body, url):
                if child not in seen and child not in pending:
                    pending.append(child)
    except (HTTPError, URLError, TimeoutError, ValueError):
        print("Official export page could not be read.", file=sys.stderr)
        return 1
    if not bodies:
        print("Official export page did not link a same-host script.", file=sys.stderr)
        return 1
    found = paths_present(page, *bodies)
    missing = [path for path in REQUIRED_PATHS if not found[path]]
    if missing:
        print("Official export assets are missing: " + ", ".join(missing), file=sys.stderr)
        return 1
    print("Official export assets still name the login and archive paths.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
