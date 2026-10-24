"""Acquisition tests use invented archives and a mocked browser only."""

import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import quote_from_bytes

from eighteenbirdies import client
from eighteenbirdies import retrieval as r

SAMPLE = Path(__file__).resolve().parents[1] / "examples/synthetic-export.json"


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "archive.json"
        self.url = r.PREFIX + quote_from_bytes(SAMPLE.read_bytes())

    def test_valid_json_preserved_and_parseable(self):
        result = r.save_download(self.url, self.output)
        self.assertEqual(self.output.read_bytes(), SAMPLE.read_bytes())
        self.assertEqual(result["bytes"], SAMPLE.stat().st_size)
        self.assertEqual(
            client.EighteenBirdiesClient.from_export(self.output).summary()["rounds"], 1
        )
        if os.name != "nt":
            self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.output.parent.iterdir()), [self.output])

    def test_refuses_existing_file_and_symlink(self):
        self.output.write_bytes(b"keep")
        with self.assertRaises(r.RetrievalError):
            r.save_download(self.url, self.output)
        self.assertEqual(self.output.read_bytes(), b"keep")
        self.output.unlink()
        if os.name != "nt":
            self.output.symlink_to(self.output.parent / "missing")
            with self.assertRaises(r.RetrievalError):
                r.save_download(self.url, self.output)
            self.assertTrue(self.output.is_symlink())

    def test_unsupported_invalid_and_compressed_are_not_published(self):
        for value in (
            "https://example.invalid/private",
            r.PREFIX + "%1f%8b",
            r.PREFIX + "{}",
            r.PREFIX + "<html>secret</html>",
            r.PREFIX,
        ):
            with self.subTest(value=value):
                with self.assertRaises(r.RetrievalError):
                    r.save_download(value, self.output)
                self.assertEqual(list(self.output.parent.iterdir()), [])

    def test_limits_before_and_after_decoding(self):
        with patch.object(r, "MAX_BYTES", 2):
            for url in (r.PREFIX + "a" * 7, r.PREFIX + "abc"):
                with self.assertRaises(r.RetrievalError):
                    r.save_download(url, self.output)
        self.assertFalse(self.output.exists())

    def test_link_failure_cleans_temporary_file(self):
        with patch.object(r.os, "link", side_effect=OSError("SECRET")):
            with self.assertRaises(r.RetrievalError) as caught:
                r.save_download(self.url, self.output)
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertEqual(list(self.output.parent.iterdir()), [])

    def browser(self):
        manager = MagicMock()
        browser = manager.return_value.__enter__.return_value.chromium.launch.return_value
        page = browser.new_context.return_value.new_page.return_value
        page.expect_download.return_value.__enter__.return_value.value.url = self.url
        module = SimpleNamespace(
            sync_playwright=manager, Error=RuntimeError, TimeoutError=TimeoutError
        )
        return manager, browser, page, module

    def test_browser_flow_and_cli(self):
        manager, browser, page, module = self.browser()
        with patch.dict(sys.modules, {"playwright.sync_api": module}):
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(
                    client.main(["retrieve", "--method", "browser", "--output", str(self.output)]),
                    0,
                )
        page.goto.assert_called_once_with(r.EXPORT_URL, wait_until="domcontentloaded")
        page.expect_download.assert_called_once_with(timeout=300000)
        browser.new_context.assert_called_once_with(accept_downloads=False)
        browser.close.assert_called_once()
        self.assertTrue(self.output.exists())

    def test_timeout_and_browser_failure_hide_payload_and_close(self):
        for error in (TimeoutError("SECRET"), RuntimeError("SECRET")):
            _, browser, page, module = self.browser()
            page.goto.side_effect = error
            with patch.dict(sys.modules, {"playwright.sync_api": module}):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(r.RetrievalError) as caught:
                        r.retrieve_browser(self.output)
            self.assertNotIn("SECRET", str(caught.exception))
            browser.close.assert_called_once()
            self.assertFalse(self.output.exists())

    def test_preflight_does_not_start_browser(self):
        manager, _, _, module = self.browser()
        with patch.dict(sys.modules, {"playwright.sync_api": module}):
            for timeout in (0, 29, 901):
                with self.assertRaises(r.RetrievalError):
                    r.retrieve_browser(self.output, timeout=timeout)
            self.output.write_bytes(b"keep")
            with self.assertRaises(r.RetrievalError):
                r.retrieve_browser(self.output)
        manager.assert_not_called()

    def test_long_sign_in_wait_and_saved_output_cli_parser(self):
        _, browser, page, module = self.browser()
        with patch.dict(sys.modules, {"playwright.sync_api": module}):
            with contextlib.redirect_stderr(io.StringIO()):
                r.retrieve_browser(self.output, timeout=600)
        page.expect_download.assert_called_once_with(timeout=600000)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(client.main(["--export", str(self.output), "summary"]), 0)
        self.assertIn('"rounds": 1', output.getvalue())
        browser.close.assert_called_once()

    def test_navigation_interruption_and_keyboard_cancel_are_safe_cli_errors(self):
        for error in (RuntimeError("net::ERR_ABORTED SECRET"), KeyboardInterrupt()):
            _, browser, page, module = self.browser()
            page.goto.side_effect = error
            output, errors = io.StringIO(), io.StringIO()
            with patch.dict(sys.modules, {"playwright.sync_api": module}):
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    self.assertEqual(
                        client.main(
                            ["retrieve", "--method", "browser", "--output", str(self.output)]
                        ),
                        2,
                    )
            self.assertEqual(output.getvalue(), "")
            self.assertNotIn("SECRET", errors.getvalue())
            self.assertNotIn("Traceback", errors.getvalue())
            self.assertEqual(list(self.output.parent.iterdir()), [])
            browser.close.assert_called_once()

    def test_download_event_timeout_closes_browser(self):
        _, browser, page, module = self.browser()
        page.expect_download.return_value.__exit__.side_effect = TimeoutError("SECRET")
        with patch.dict(sys.modules, {"playwright.sync_api": module}):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(r.RetrievalError):
                    r.retrieve_browser(self.output)
        browser.close.assert_called_once()
        self.assertFalse(self.output.exists())

    def test_flush_exception_cleans_temporary_file(self):
        with patch.object(r.os, "fsync", side_effect=OSError("SECRET")):
            with self.assertRaises(r.RetrievalError) as caught:
                r.save_download(self.url, self.output)
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertEqual(list(self.output.parent.iterdir()), [])

    def test_debug_environment_rejected_before_browser_launch(self):
        manager, _, _, module = self.browser()
        for name in ("DEBUG", "PWDEBUG"):
            with patch.dict(os.environ, {name: "1"}):
                with patch.dict(sys.modules, {"playwright.sync_api": module}):
                    with self.assertRaisesRegex(r.RetrievalError, "Unset DEBUG"):
                        r.retrieve_browser(self.output)
        manager.assert_not_called()
