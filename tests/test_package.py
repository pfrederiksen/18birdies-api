"""Public package behavior and portability checks using synthetic inputs only."""

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from eighteenbirdies import EighteenBirdiesClient, EighteenBirdiesError, default_store_path
from eighteenbirdies import client as api
from eighteenbirdies.locking import file_lock


SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "synthetic-export.json"


class PackageTests(unittest.TestCase):
    def test_version(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as stopped:
                api.main(["--version"])
        self.assertEqual(stopped.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), "18birdies 0.1.0")

    def test_all_core_processing_works_with_network_calls_forbidden(self):
        import http.client
        import socket
        import urllib.request

        with contextlib.ExitStack() as guard:
            for target, name in (
                (socket, "socket"),
                (socket, "create_connection"),
                (urllib.request, "urlopen"),
                (http.client.HTTPConnection, "connect"),
                (http.client.HTTPSConnection, "connect"),
            ):
                guard.enter_context(
                    patch.object(
                        target, name, side_effect=AssertionError("Network access forbidden")
                    )
                )
            with tempfile.TemporaryDirectory() as directory:
                client = EighteenBirdiesClient.import_export(
                    SAMPLE, store_path=Path(directory) / "golf.json"
                )
                self.assertEqual(client.snapshot()["total_rounds"], 1)
                self.assertEqual(len(client.get_round("demo-round-01")["shots"]), 2)
                self.assertEqual(client.course_history()[0]["name"], "Example Golf Course")
                self.assertEqual(len(client.club_distances()), 2)
                self.assertFalse(client.capabilities()["live_api"])
                self.assertEqual(EighteenBirdiesClient.from_export(SAMPLE).summary()["rounds"], 1)

    def test_default_store_is_user_data_not_installed_code(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"XDG_DATA_HOME": directory}):
                self.assertEqual(
                    default_store_path(), Path(directory) / "eighteenbirdies" / "export.json"
                )
            with patch.dict(os.environ, {"XDG_DATA_HOME": "relative-is-invalid"}):
                self.assertEqual(
                    default_store_path(),
                    Path.home() / ".local" / "share" / "eighteenbirdies" / "export.json",
                )

    def test_default_timezone_is_utc_and_can_be_selected(self):
        client = EighteenBirdiesClient.from_export(SAMPLE)
        self.assertEqual(client.snapshot()["date_timezone"], "UTC")
        self.assertEqual(client.list_rounds()[0]["date"], client.list_rounds()[0]["date_utc"])
        selected = EighteenBirdiesClient.from_export(SAMPLE, timezone_name="Europe/London")
        self.assertEqual(selected.snapshot()["date_timezone"], "Europe/London")

    def test_invalid_timezone_does_not_write_a_store(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "golf.json"
            with self.assertRaises(EighteenBirdiesError):
                EighteenBirdiesClient.import_export(
                    SAMPLE, store_path=destination, timezone_name="Invalid/Zone"
                )
            self.assertFalse(destination.exists())

    def test_module_cli_is_packaged_and_offline(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "eighteenbirdies",
                "--export",
                str(SAMPLE),
                "capabilities",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        self.assertIn('"live_api": false', result.stdout)
        self.assertEqual(result.stderr, "")

    def test_cli_uses_configured_store_and_real_acquisition_only(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "local.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    api.main(["--store", str(destination), "import", str(SAMPLE), "--json"]), 0
                )
            self.assertIsNone(
                EighteenBirdiesClient(store_path=destination).provenance["acquired_at"]
            )

    def test_regular_lock_can_be_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.lock"
            with file_lock(path):
                self.assertTrue(path.is_file())
            with file_lock(path):
                self.assertTrue(path.is_file())

    @unittest.skipIf(os.name == "nt", "POSIX contention branch; Windows runs native lock tests")
    def test_posix_lock_contention_has_bounded_wait(self):
        import errno
        from eighteenbirdies import locking

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.lock"
            with patch(
                "fcntl.flock", side_effect=BlockingIOError(errno.EAGAIN, "synthetic contention")
            ):
                with patch.object(locking.time, "monotonic", side_effect=[0, 31]):
                    with self.assertRaisesRegex(OSError, "Timed out"):
                        with file_lock(path):
                            self.fail("contended lock must time out")
            with file_lock(path):
                self.assertTrue(path.is_file())

    @unittest.skipIf(
        os.name == "nt", "Windows symlink creation requires platform-specific privileges"
    )
    def test_symlink_lock_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            target.write_text("unchanged")
            link = Path(directory) / "local.lock"
            link.symlink_to(target)
            with self.assertRaises(OSError):
                with file_lock(link):
                    self.fail("symlink lock must not be acquired")
            self.assertEqual(target.read_text(), "unchanged")
