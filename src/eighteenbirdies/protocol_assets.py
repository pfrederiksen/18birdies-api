"""Match the public export page against the known login and archive paths.

This module does not open a network connection. A scheduled job fetches the
official page, the same-host scripts it references, and the chunks those
scripts import, then asks these helpers whether the paths are still named.
"""

from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit

OFFICIAL_ORIGIN = "https://18birdies.com"
OFFICIAL_HOST = urlsplit(OFFICIAL_ORIGIN).hostname
OFFICIAL_EXPORT_PAGE = "https://18birdies.com/download-account-data/"
LOGIN_PATH = "/usercentral/api/user/userLogin"
EXPORT_PATH = "/usercentral/api/user/getMyData"
REQUIRED_PATHS = (LOGIN_PATH, EXPORT_PATH)


class _ScriptLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for _name, candidate in attrs:
            if isinstance(candidate, str) and candidate.split("?", 1)[0].endswith(".js"):
                self.links.append(candidate)


_JS_REFERENCE = re.compile(r"""["']([^"']+\.js)["']""")


def _same_host(url: str) -> str | None:
    parts = urlsplit(url)
    if (
        parts.scheme == "https"
        and parts.hostname == OFFICIAL_HOST
        and parts.username is None
        and parts.password is None
    ):
        return url
    return None


def linked_scripts(page_html: str, page_url: str = OFFICIAL_EXPORT_PAGE) -> list[str]:
    """Return same-host HTTPS script URLs referenced by the public export page."""
    parser = _ScriptLinks()
    parser.feed(page_html)
    scripts: list[str] = []
    for candidate in parser.links:
        absolute = _same_host(urljoin(page_url, candidate))
        if absolute is not None:
            scripts.append(absolute)
    return scripts


def imported_scripts(javascript: str, script_url: str) -> list[str]:
    """Return same-host scripts named by a public chunk.

    Only `./file.js` and `/_astro/file.js` references are followed. A bare
    `_astro/file.js` string is also present in some bundles and would resolve
    to `/_astro/_astro/file.js` from a chunk that already lives in `/_astro`.
    """
    scripts: list[str] = []
    for candidate in _JS_REFERENCE.findall(javascript):
        if candidate.startswith("./") and "/" not in candidate[2:]:
            absolute = _same_host(urljoin(script_url, candidate))
        elif candidate.startswith("/_astro/") and "/" not in candidate.removeprefix("/_astro/"):
            absolute = _same_host(urljoin(OFFICIAL_ORIGIN, candidate))
        elif candidate.startswith(f"{OFFICIAL_ORIGIN}/_astro/"):
            name = candidate.removeprefix(f"{OFFICIAL_ORIGIN}/_astro/")
            absolute = _same_host(candidate) if "/" not in name else None
        else:
            absolute = None
        if absolute is not None:
            scripts.append(absolute)
    return scripts


def paths_present(*texts: str) -> dict[str, bool]:
    """Report which required export paths appear in the supplied public text."""
    combined = "\n".join(texts)
    return {path: path in combined for path in REQUIRED_PATHS}
