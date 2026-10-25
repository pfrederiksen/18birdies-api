"""Invented protocol responses only; no live accounts or network access."""

import contextlib
import getpass
import http.client
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import warnings

from eighteenbirdies import client
from eighteenbirdies import direct as d
from eighteenbirdies.retrieval import RetrievalError

SAMPLE = Path(__file__).resolve().parents[1] / "examples/synthetic-export.json"
ACCOUNT, PASSWORD, TOKEN = "invented@example.invalid", "INVENTED-SECRET", "INVENTED-TOKEN"


class DirectTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "new.json"
        self.guard = contextlib.ExitStack()
        self.addCleanup(self.guard.close)
        self.guard.enter_context(patch.dict(os.environ, {"DEBUG": "", "PWDEBUG": ""}))

    def test_default_cli_protocol_and_parse_same_file(self):
        with patch.object(d, "_credentials", return_value=(ACCOUNT, PASSWORD)):
            with patch.object(
                d, "_post", side_effect=[json.dumps({"token": TOKEN}).encode(), SAMPLE.read_bytes()]
            ) as post:
                output, errors = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    self.assertEqual(client.main(["retrieve", "--output", str(self.output)]), 0)
        first, second = post.call_args_list
        self.assertEqual(
            first.args[:3], (d.LOGIN_PATH, {"account": ACCOUNT, "password": PASSWORD}, None)
        )
        self.assertEqual(first.args[4], d.AUTH_MAX_BYTES)
        self.assertEqual(second.args[:3], (d.EXPORT_PATH, {}, TOKEN))
        self.assertEqual(first.args[3], second.args[3])
        self.assertEqual(second.args[4], client.MAX_BYTES)
        for secret in (ACCOUNT, PASSWORD, TOKEN):
            self.assertNotIn(secret, output.getvalue() + errors.getvalue())
        self.assertEqual(self.output.read_bytes(), SAMPLE.read_bytes())
        self.assertEqual(
            client.EighteenBirdiesClient.from_export(self.output).summary()["rounds"], 1
        )
        if os.name != "nt":
            self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)

    def test_secure_input_and_no_echo_fallback(self):
        with patch.object(d.sys.stdin, "isatty", return_value=True):
            with patch.object(d.sys.stderr, "isatty", return_value=True):
                with patch.object(d.getpass, "getpass", side_effect=[ACCOUNT, PASSWORD]) as prompt:
                    self.assertEqual(d._credentials(), (ACCOUNT, PASSWORD))
                    self.assertEqual(prompt.call_count, 2)

                def insecure(*args, **kwargs):
                    warnings.warn("SECRET", getpass.GetPassWarning)

                with patch.object(d.getpass, "getpass", side_effect=insecure):
                    with self.assertRaisesRegex(RetrievalError, "Secure hidden"):
                        d._credentials()
        with patch.object(d.sys.stdin, "isatty", return_value=False):
            with patch.object(d.getpass, "getpass") as prompt:
                with self.assertRaisesRegex(RetrievalError, "interactive terminal"):
                    d._credentials()
                prompt.assert_not_called()

    def test_invalid_credentials_are_not_sent(self):
        for account, password in (("+15555555555", "secret"), (ACCOUNT, ""), ("a b@c", PASSWORD)):
            with patch.object(d.sys.stdin, "isatty", return_value=True):
                with patch.object(d.sys.stderr, "isatty", return_value=True):
                    with patch.object(d.getpass, "getpass", side_effect=[account, password]):
                        with patch.object(d, "_post") as post:
                            with self.assertRaises(RetrievalError):
                                d.retrieve_direct(self.output)
                        post.assert_not_called()

    def test_unknown_or_challenge_authentication_never_requests_export(self):
        responses = [
            b"not JSON SECRET",
            b"[]",
            b"{}",
            b'{"result":"INVALID_ACCOUNT_OR_PASSWORD"}',
            b'{"result":"MFA_REQUIRED"}',
            b'{"token":"x","captchaRequired":true}',
            b'{"token":"header\\r\\ninjection"}',
            b'{"token":17}',
        ]
        for response in responses:
            with self.subTest(response=response):
                with patch.object(d, "_credentials", return_value=(ACCOUNT, PASSWORD)):
                    with patch.object(d, "_post", return_value=response) as post:
                        with self.assertRaises(RetrievalError) as caught:
                            d.retrieve_direct(self.output)
                self.assertNotIn("SECRET", str(caught.exception))
                self.assertEqual(post.call_count, 1)
                self.assertFalse(self.output.exists())

    def test_failures_redacted_no_retries_or_files(self):
        for error in (
            OSError(PASSWORD),
            TimeoutError(TOKEN),
            http.client.HTTPException(ACCOUNT),
            KeyboardInterrupt(),
        ):
            with self.subTest(error=type(error)):
                with patch.object(d, "_credentials", return_value=(ACCOUNT, PASSWORD)):
                    with patch.object(d, "_post", side_effect=error) as post:
                        output, errors = io.StringIO(), io.StringIO()
                        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                            self.assertEqual(
                                client.main(["retrieve", "--output", str(self.output)]), 2
                            )
                self.assertEqual(post.call_count, 1)
                self.assertNotIn("Traceback", errors.getvalue())
                for secret in (ACCOUNT, PASSWORD, TOKEN):
                    self.assertNotIn(secret, output.getvalue() + errors.getvalue())
                self.assertEqual(list(self.output.parent.iterdir()), [])

    def test_bad_archive_is_not_published(self):
        with patch.object(d, "_credentials", return_value=(ACCOUNT, PASSWORD)):
            with patch.object(d, "_post", side_effect=[b'{"token":"invented"}', b'{"myData":']):
                with self.assertRaisesRegex(RetrievalError, "validation"):
                    d.retrieve_direct(self.output)
        self.assertEqual(list(self.output.parent.iterdir()), [])

    def test_preflight_refuses_overwrite_before_prompt_or_network(self):
        self.output.write_bytes(b"keep")
        with patch.object(d, "_credentials") as prompt, patch.object(d, "_post") as post:
            with self.assertRaises(RetrievalError):
                d.retrieve_direct(self.output)
        prompt.assert_not_called()
        post.assert_not_called()
        self.assertEqual(self.output.read_bytes(), b"keep")

    def connection(self, *, status=200, data=b'{"token":"invented"}', headers=None):
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = status
        headers = {"Content-Type": "application/json", **(headers or {})}
        response.getheader.side_effect = lambda key, default=None: headers.get(key, default)
        response.read1.side_effect = [data, b""]
        return connection

    def test_https_wire_contract_and_close(self):
        connection = self.connection()
        with patch.object(d.http.client, "HTTPSConnection", return_value=connection) as constructor:
            with patch.object(d.time, "monotonic", return_value=0):
                d._post(d.EXPORT_PATH, {}, TOKEN, 300, 1024)
        constructor.assert_called_once_with("18birdies.com", timeout=30.0)
        args, kwargs = connection.request.call_args
        self.assertEqual(args, ("POST", d.EXPORT_PATH))
        self.assertEqual(json.loads(kwargs["body"]), {})
        self.assertEqual(kwargs["headers"]["Token"], TOKEN)
        self.assertNotIn("Cookie", kwargs["headers"])
        connection.close.assert_called_once()
        connection.set_debuglevel.assert_called_once_with(0)
        connection.sock.settimeout.assert_called_with(30.0)

    def test_http_denial_redirect_and_response_limits(self):
        cases = [
            (401, {}, b""),
            (403, {}, b""),
            (429, {}, b""),
            (302, {}, b""),
            (500, {}, b""),
            (200, {"Content-Type": "text/html"}, b"CAPTCHA"),
            (200, {"Content-Encoding": "gzip"}, b"compressed"),
            (200, {"Content-Length": "9999"}, b"x"),
            (200, {"Content-Length": "bad"}, b"x"),
            (200, {"Content-Length": "-1"}, b"x"),
            (200, {"Content-Length": "2"}, b"x"),
            (200, {}, b""),
            (200, {}, b"x" * 101),
        ]
        for status, headers, data in cases:
            with self.subTest(status=status, headers=headers):
                connection = self.connection(status=status, headers=headers, data=data)
                with patch.object(d.http.client, "HTTPSConnection", return_value=connection):
                    with patch.object(d.time, "monotonic", return_value=0):
                        with self.assertRaises(RetrievalError):
                            d._post(d.EXPORT_PATH, {}, TOKEN, 300, 100)
                connection.close.assert_called_once()
                self.assertEqual(connection.request.call_count, 1)

    def test_network_deadline_covers_both_requests_and_reads(self):
        with patch.object(d.time, "monotonic", return_value=301):
            with patch.object(d.http.client, "HTTPSConnection") as constructor:
                with self.assertRaises(TimeoutError):
                    d._post(d.LOGIN_PATH, {}, None, 300, 100)
            constructor.assert_not_called()
        connection = self.connection()
        with patch.object(d.http.client, "HTTPSConnection", return_value=connection):
            with patch.object(d.time, "monotonic", side_effect=[0, 1, 2, 301]):
                with self.assertRaises(TimeoutError):
                    d._post(d.EXPORT_PATH, {}, TOKEN, 300, 100)
        connection.close.assert_called_once()

    def test_connection_close_response_retains_body_socket_deadline(self):
        connection = self.connection()
        transport = connection.sock
        response = connection.getresponse.return_value

        def detach():
            connection.sock = None
            return response

        connection.getresponse.side_effect = detach
        with patch.object(d.http.client, "HTTPSConnection", return_value=connection):
            with patch.object(d.time, "monotonic", return_value=0):
                d._post(d.EXPORT_PATH, {}, TOKEN, 300, 100)
        transport.settimeout.assert_called_with(30.0)
        response.close.assert_called_once()
        connection.close.assert_called_once()

    def test_final_read_cannot_succeed_after_network_deadline(self):
        connection = self.connection()
        with patch.object(d.http.client, "HTTPSConnection", return_value=connection):
            with patch.object(d.time, "monotonic", side_effect=[0, 1, 2, 3, 301]):
                with self.assertRaises(TimeoutError):
                    d._post(d.EXPORT_PATH, {}, TOKEN, 300, 100)
        connection.getresponse.return_value.close.assert_called_once()
        connection.close.assert_called_once()
