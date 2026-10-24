"""Cloud adapter tests use invented placeholders and fake connections only."""

import contextlib
import io
import json
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from eighteenbirdies import client, cloud_transport as cloud, direct
from eighteenbirdies.retrieval import RetrievalError

SAMPLE = Path(__file__).resolve().parents[1] / "examples/synthetic-export.json"


class CloudTransportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "archive.json"
        self.values = {
            cloud.EMAIL_KEY: 'opaque-placeholder-"-\\-not-an-email',
            cloud.PASSWORD_KEY: 'opaque-password-"-\\-placeholder',
            "HTTPS_PROXY": "http://proxy.invalid:8080",
            "NO_PROXY": "*",
        }
        self.connections = []

    def factory(self, host, port, **options):
        connection = MagicMock()
        connection.endpoint = (host, port)
        connection.options = options
        response = connection.getresponse.return_value
        response.status = 200
        response.getheader.side_effect = lambda key, default=None: {
            "Content-Type": "application/json"
        }.get(key, default)
        data = b'{"token":"SYNTHETIC_TOKEN"}' if not self.connections else SAMPLE.read_bytes()
        response.read1.side_effect = [data, b""]
        self.connections.append(connection)
        return connection

    def run_synthetic(self, factory=None):
        return cloud._retrieve_synthetic(
            self.output, self.values, connection_factory=factory or self.factory
        )

    def test_end_to_end_proxy_placeholders_token_archive_and_safe_output(self):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = self.run_synthetic()
        self.assertEqual(output.getvalue() + errors.getvalue(), "")
        self.assertEqual(set(result), {"source", "bytes", "sha256"})
        self.assertEqual(self.output.read_bytes(), SAMPLE.read_bytes())
        first, second = self.connections
        for connection in self.connections:
            self.assertEqual(connection.endpoint, ("proxy.invalid", 8080))
            connection.set_tunnel.assert_called_once_with("18birdies.com", 443)
            connection.set_debuglevel.assert_called_once_with(0)
            connection.connect.assert_called_once()
            connection.close.assert_called_once()
            self.assertEqual(connection.options["context"].verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(connection.options["context"].check_hostname)
        self.assertEqual(first.request.call_args.args, ("POST", direct.LOGIN_PATH))
        self.assertEqual(
            json.loads(first.request.call_args.kwargs["body"]),
            {
                "account": self.values[cloud.EMAIL_KEY],
                "password": self.values[cloud.PASSWORD_KEY],
            },
        )
        self.assertEqual(second.request.call_args.args, ("POST", direct.EXPORT_PATH))
        self.assertEqual(second.request.call_args.kwargs["headers"]["Token"], "SYNTHETIC_TOKEN")
        self.assertNotIn("Cookie", second.request.call_args.kwargs["headers"])
        self.assertEqual(first.options["timeout"], 30)

    def test_lowercase_configured_proxy_supported(self):
        self.values["https_proxy"] = self.values.pop("HTTPS_PROXY")
        self.run_synthetic()
        self.assertEqual(self.connections[0].endpoint, ("proxy.invalid", 8080))

    def test_cli_cloud_blocks_before_placeholder_read_or_connection(self):
        with patch.object(cloud, "_placeholder_credentials") as credentials:
            with patch.object(cloud, "_proxy_factory") as proxy:
                output, errors = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    code = client.main(
                        ["retrieve", "--method", "cloud", "--output", str(self.output)]
                    )
        self.assertEqual(code, 2)
        credentials.assert_not_called()
        proxy.assert_not_called()
        self.assertEqual(output.getvalue(), "")
        self.assertIn("JSON substitution is unverified", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())
        self.assertEqual(list(self.output.parent.iterdir()), [])

    def test_missing_or_invalid_placeholders_never_connect(self):
        for value in [None, "", 42, "x" * 4097]:
            self.values[cloud.EMAIL_KEY] = value
            with self.assertRaises(RetrievalError):
                self.run_synthetic()
        del self.values[cloud.EMAIL_KEY]
        with self.assertRaises(RetrievalError):
            self.run_synthetic()
        self.assertEqual(self.connections, [])

    def test_missing_or_unsupported_proxy_has_no_direct_fallback(self):
        for proxy in [
            None,
            "",
            "https://proxy.invalid",
            "http://u:p@proxy.invalid",
            "http://proxy.invalid/path",
            "http://proxy.invalid?secret=value",
            "http://proxy.invalid:0",
            "http://proxy.invalid:99999",
        ]:
            self.values["HTTPS_PROXY"] = proxy
            with self.assertRaises(RetrievalError):
                self.run_synthetic()
        self.assertEqual(self.connections, [])

    def test_unverified_tls_context_rejected(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        with self.assertRaises(RetrievalError):
            cloud._proxy_factory("http://proxy.invalid", context=context)

    def test_host_and_path_are_fixed(self):
        factory = cloud._proxy_factory("http://proxy.invalid", connection_factory=self.factory)
        with self.assertRaises(RetrievalError):
            factory("unreviewed.invalid", timeout=30)
        with self.assertRaises(RetrievalError):
            direct._post(
                "https://unreviewed.invalid/", {}, None, 300, 100, _connection_factory=factory
            )
        self.assertEqual(self.connections, [])

    def test_shared_deadline_and_read_bounds_are_preserved(self):
        deadlines, limits = [], []
        original = direct._post

        def checked(path, payload, token, deadline, limit, **options):
            deadlines.append(deadline)
            limits.append(limit)
            return original(path, payload, token, deadline, limit, **options)

        with patch.object(direct, "_post", side_effect=checked):
            self.run_synthetic()
        self.assertEqual(deadlines[0], deadlines[1])
        self.assertEqual(limits, [direct.AUTH_MAX_BYTES, client.MAX_BYTES])

    def test_cancel_and_timeout_close_without_retry_or_files(self):
        for failure in [KeyboardInterrupt(), TimeoutError("SYNTHETIC_SECRET")]:
            self.connections.clear()

            def factory(*args, **kwargs):
                connection = self.factory(*args, **kwargs)
                connection.request.side_effect = failure
                return connection

            with self.assertRaises(RetrievalError) as caught:
                self.run_synthetic(factory)
            self.assertNotIn("SYNTHETIC_SECRET", str(caught.exception))
            self.assertEqual(len(self.connections), 1)
            self.connections[0].close.assert_called_once()
            self.assertEqual(list(self.output.parent.iterdir()), [])

    def test_denial_redirect_and_invalid_response_stop_before_export(self):
        for status, content_type in [
            (403, "application/json"),
            (302, "application/json"),
            (200, "text/html"),
        ]:
            self.connections.clear()

            def factory(*args, **kwargs):
                connection = self.factory(*args, **kwargs)
                response = connection.getresponse.return_value
                response.status = status
                response.getheader.side_effect = lambda key, default=None: {
                    "Content-Type": content_type
                }.get(key, default)
                return connection

            with self.assertRaises(RetrievalError):
                self.run_synthetic(factory)
            self.assertEqual(len(self.connections), 1)
            self.connections[0].close.assert_called_once()
            self.assertFalse(self.output.exists())

    def test_factory_failure_is_redacted_and_tunnel_failure_closes(self):
        def broken(*args, **kwargs):
            raise OSError(self.values[cloud.PASSWORD_KEY])

        with self.assertRaises(RetrievalError) as caught:
            self.run_synthetic(broken)
        self.assertNotIn(self.values[cloud.PASSWORD_KEY], str(caught.exception))
        connection = MagicMock()
        connection.set_tunnel.side_effect = OSError("SYNTHETIC_SECRET")
        with self.assertRaises(RetrievalError):
            self.run_synthetic(lambda *args, **kwargs: connection)
        connection.close.assert_called_once()


class EnvironmentCredentialTests(unittest.TestCase):
    factory = CloudTransportTests.factory

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "archive.json"
        self.connections = []
        self.values = {
            cloud.ENV_EMAIL_KEY: 'invented"\\é@example.invalid',
            cloud.ENV_PASSWORD_KEY: 'INVENTED-"-\\-é-雪-\n-PASSWORD',
            "HTTPS_PROXY": "http://proxy.invalid:8080",
            "UNRELATED": "leave-this-alone",
            cloud.EMAIL_KEY: "NETWORK-PLACEHOLDER-MUST-NOT-BE-USED",
            cloud.PASSWORD_KEY: "NETWORK-PASSWORD-MUST-NOT-BE-USED",
        }

    def run_environment(self, factory=None):
        return cloud.retrieve_environment(
            self.output,
            _environment=self.values,
            _connection_factory=factory or self.factory,
        )

    def assert_consumed(self):
        self.assertNotIn(cloud.ENV_EMAIL_KEY, self.values)
        self.assertNotIn(cloud.ENV_PASSWORD_KEY, self.values)
        self.assertEqual(self.values["UNRELATED"], "leave-this-alone")

    def test_json_sensitive_raw_values_serialized_and_consumed_before_connect(self):
        expected = (self.values[cloud.ENV_EMAIL_KEY], self.values[cloud.ENV_PASSWORD_KEY])

        def factory(*args, **kwargs):
            self.assert_consumed()
            return self.factory(*args, **kwargs)

        with patch("subprocess.Popen", side_effect=AssertionError("No child process allowed")):
            result = self.run_environment(factory)
        payload = json.loads(self.connections[0].request.call_args.kwargs["body"])
        self.assertEqual(payload, {"account": expected[0], "password": expected[1]})
        self.assertEqual(self.output.read_bytes(), SAMPLE.read_bytes())
        self.assertEqual(set(result), {"source", "bytes", "sha256"})
        for value in expected:
            self.assertNotIn(value, json.dumps(result))
            self.assertNotIn(value.encode(), self.output.read_bytes())
        self.assert_consumed()
        self.assertEqual(self.values[cloud.EMAIL_KEY], "NETWORK-PLACEHOLDER-MUST-NOT-BE-USED")
        self.connections[0].set_tunnel.assert_called_once_with("18birdies.com", 443)

    def test_missing_inputs_do_not_use_network_secrets_or_terminal_fallback(self):
        del self.values[cloud.ENV_PASSWORD_KEY]
        with patch.object(direct, "_credentials") as terminal:
            with self.assertRaises(RetrievalError):
                self.run_environment()
        terminal.assert_not_called()
        self.assertEqual(self.connections, [])
        self.assert_consumed()

    def test_invalid_input_bounds_consumed_without_values_in_errors(self):
        for value in ("", None, 42, "INVENTED" * 600):
            self.values[cloud.ENV_EMAIL_KEY] = "invented@example.invalid"
            self.values[cloud.ENV_PASSWORD_KEY] = value
            with self.assertRaises(RetrievalError) as caught:
                self.run_environment()
            self.assertNotIn("INVENTED", str(caught.exception))
            self.assert_consumed()
        self.assertEqual(self.connections, [])

    def test_proxy_tls_errors_are_redacted_and_inputs_consumed(self):
        secret = self.values[cloud.ENV_PASSWORD_KEY]
        with patch.object(cloud.ssl, "create_default_context", side_effect=OSError(secret)):
            with self.assertRaises(RetrievalError) as caught:
                self.run_environment()
        self.assertNotIn(secret, str(caught.exception))
        self.assertEqual(self.connections, [])
        self.assert_consumed()

    def test_cli_explicit_opt_in_only(self):
        from eighteenbirdies import retrieval

        with patch.object(
            cloud, "retrieve_environment", return_value={"source": "synthetic"}
        ) as raw:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    client.main(
                        ["retrieve", "--method", "cloud-env", "--output", str(self.output)]
                    ),
                    0,
                )
            raw.assert_called_once_with(self.output, timeout=300)
        with patch.object(cloud, "retrieve_environment") as raw:
            with self.assertRaises(RetrievalError):
                retrieval.retrieve(self.output, method="cloud")
            raw.assert_not_called()

    def test_cli_all_transport_failures_redacted_without_subprocesses(self):
        original = cloud.retrieve_environment
        for kind in (OSError, TimeoutError, KeyboardInterrupt, direct.http.client.HTTPException):
            account, password = 'invented"é@example.invalid', 'INVENTED-\\-"-雪'
            self.values[cloud.ENV_EMAIL_KEY] = account
            self.values[cloud.ENV_PASSWORD_KEY] = password
            self.connections.clear()

            def factory(*args, **kwargs):
                connection = self.factory(*args, **kwargs)
                connection.request.side_effect = kind(password)
                return connection

            def invoke(destination, **options):
                return original(
                    destination, _environment=self.values, _connection_factory=factory, **options
                )

            output, errors = io.StringIO(), io.StringIO()
            with patch.object(cloud, "retrieve_environment", side_effect=invoke):
                with patch("subprocess.Popen", side_effect=AssertionError("No child allowed")):
                    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                        self.assertEqual(
                            client.main(
                                ["retrieve", "--method", "cloud-env", "--output", str(self.output)]
                            ),
                            2,
                        )
            for value in (account, password, "Traceback"):
                self.assertNotIn(value, output.getvalue() + errors.getvalue())
            self.assertEqual(len(self.connections), 1)
            self.connections[0].close.assert_called_once()
            self.assertEqual(list(self.output.parent.iterdir()), [])
            self.assert_consumed()

    def test_preflight_failure_consumes_inputs_without_connection(self):
        self.output.write_bytes(b"keep")
        with self.assertRaises(RetrievalError):
            self.run_environment()
        self.assertEqual(self.output.read_bytes(), b"keep")
        self.assertEqual(self.connections, [])
        self.assert_consumed()

    def test_authentication_rejection_and_payload_errors_redacted(self):
        for data in (
            b"not JSON INVENTED-SECRET",
            b'{"result":"MFA_REQUIRED"}',
            b'{"token":"x","captchaRequired":true}',
        ):
            self.values[cloud.ENV_EMAIL_KEY] = "invented@example.invalid"
            self.values[cloud.ENV_PASSWORD_KEY] = "INVENTED-SECRET"
            self.connections.clear()

            def factory(*args, **kwargs):
                connection = self.factory(*args, **kwargs)
                connection.getresponse.return_value.read1.side_effect = [data, b""]
                return connection

            with self.assertRaises(RetrievalError) as caught:
                self.run_environment(factory)
            self.assertNotIn("INVENTED-SECRET", str(caught.exception))
            self.assertEqual(len(self.connections), 1)
            self.assertEqual(list(self.output.parent.iterdir()), [])
            self.assert_consumed()


if __name__ == "__main__":
    unittest.main()
