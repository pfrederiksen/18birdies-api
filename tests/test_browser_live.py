"""Real Chromium coverage for the official-origin data-URL download.

Skipped unless EIGHTEENBIRDIES_BROWSER_TEST=1. The suite never contacts
18birdies.com; Playwright fulfills that host with a synthetic page.
"""

import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from urllib.parse import quote_from_bytes

from eighteenbirdies import retrieval


SAMPLE = Path(__file__).resolve().parents[1] / "examples/synthetic-export.json"


@unittest.skipUnless(
    os.environ.get("EIGHTEENBIRDIES_BROWSER_TEST") == "1",
    "set EIGHTEENBIRDIES_BROWSER_TEST=1 to launch Chromium",
)
class LiveBrowserDownloadTests(unittest.TestCase):
    def test_chromium_keeps_data_url_from_the_official_host(self):
        from playwright.sync_api import sync_playwright

        self.assertIsNotNone(sync_playwright)
        for name in retrieval.BLOCKED_ENV:
            os.environ.pop(name, None)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        output = Path(directory.name) / "archive.json"
        href = retrieval.PREFIX + quote_from_bytes(SAMPLE.read_bytes())
        html = (
            "<!DOCTYPE html><html><body>"
            f'<a id="save" download="archive.json" href="{href}">save</a>'
            "<script>document.getElementById('save').click();</script>"
            "</body></html>"
        )

        def prepare(context):
            def fulfill(route):
                route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)

            context.route("https://18birdies.com/**", fulfill)

        with contextlib.redirect_stderr(io.StringIO()):
            result = retrieval.retrieve_browser(
                output, timeout=60, _headless=True, _prepare_context=prepare
            )
        self.assertIs(result["golf_only"], False)
        self.assertEqual(output.read_bytes(), SAMPLE.read_bytes())
        if os.name != "nt":
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
