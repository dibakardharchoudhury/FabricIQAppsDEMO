import contextlib
import io
import json
import os
import ssl
import sys
import time
import unittest
from unittest.mock import Mock, call, patch

import requests
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.exceptions import MaxRetryError, SSLError as Urllib3SSLError

import http_retries
import run_pipeline
from delete_workspace_items import Fabric as DeleteFabric
from feature_workspace import FeatureFabric
from key_vault_preflight import CloudClient, FABRIC_SCOPE
from sync_workspace_from_git import Fabric

URL = (
    "https://api.fabric.microsoft.com/v1/workspaces/11111111-1111-1111-1111-111111111111"
    "/items/22222222-2222-2222-2222-222222222222/jobs/instances/"
    "33333333-3333-3333-3333-333333333333"
)


def response(status=200, body=None, **headers):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(body or {"status": "Completed"}).encode()
    result.headers.update(headers)
    return result


class SharedReadRetryTests(unittest.TestCase):
    def test_tls_eof_reads_retry_through_requests_and_urllib3_wrappers(self):
        eof = ssl.SSLEOFError(ssl.SSL_ERROR_EOF, "private TLS payload")
        for failure in (
            requests.exceptions.SSLError(eof),
            requests.exceptions.SSLError(MaxRetryError(
                HTTPSConnectionPool("api.fabric.microsoft.com"), URL, reason=Urllib3SSLError(eof),
            )),
        ):
            for method in ("GET", "HEAD"):
                with self.subTest(method=method, failure=type(failure.args[0]).__name__):
                    sender = Mock(side_effect=[failure, response()])
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output), patch.object(http_retries.time, "sleep") as sleep:
                        result = http_retries.request_with_read_retry(
                            method, URL + "?token=private-query", send=sender, timeout=60,
                        )
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(sender.call_count, 2)
                    self.assertEqual(sender.call_args_list[0], sender.call_args_list[1])
                    self.assertNotIn("verify", sender.call_args.kwargs)
                    sleep.assert_called_once_with(1)
                    self.assertIn("TLS EOF", output.getvalue())
                    self.assertNotIn("private", output.getvalue())

    def test_tls_eof_retry_budget_is_bounded_and_preserves_original_error(self):
        failure = requests.exceptions.SSLError(
            MaxRetryError(HTTPSConnectionPool("api.fabric.microsoft.com"), URL, reason=Urllib3SSLError(
                ssl.SSLEOFError(ssl.SSL_ERROR_EOF, "TLS stream closed"),
            )),
        )
        sender = Mock(side_effect=failure)
        with patch.object(http_retries.time, "sleep") as sleep, self.assertRaises(
            requests.exceptions.SSLError,
        ) as caught:
            http_retries.request_with_read_retry("GET", URL, send=sender)
        self.assertIs(caught.exception, failure)
        self.assertEqual(sender.call_count, http_retries.READ_ATTEMPTS)
        self.assertEqual(sleep.call_args_list, [call(1), call(2), call(4), call(8), call(15)])

    def test_tls_writes_certificate_and_unknown_ssl_errors_are_never_retried(self):
        eof = ssl.SSLEOFError(ssl.SSL_ERROR_EOF, "TLS stream closed")
        certificate = ssl.SSLCertVerificationError(1, "certificate verify failed")
        mixed = ssl.SSLEOFError(ssl.SSL_ERROR_EOF, "TLS stream closed")
        mixed.__cause__ = certificate
        mixed.__context__ = mixed
        failures = [
            (method, requests.exceptions.SSLError(eof))
            for method in ("POST", "PUT", "PATCH", "DELETE")
        ] + [
            ("GET", requests.exceptions.SSLError(MaxRetryError(
                HTTPSConnectionPool("api.fabric.microsoft.com"), URL, reason=Urllib3SSLError(certificate),
            ))),
            ("GET", requests.exceptions.SSLError(mixed)),
            ("GET", requests.exceptions.SSLError(ssl.SSLError(1, "wrong protocol"))),
            ("GET", requests.exceptions.SSLError("UNEXPECTED_EOF_WHILE_READING")),
        ]
        for method, failure in failures:
            with self.subTest(method=method, failure=str(failure)):
                sender = Mock(side_effect=failure)
                with patch.object(http_retries.time, "sleep") as sleep, self.assertRaises(
                    requests.exceptions.SSLError,
                ):
                    http_retries.request_with_read_retry(method, URL, send=sender)
                sender.assert_called_once()
                sleep.assert_not_called()

    def test_all_fabric_clients_recover_the_same_read_without_replaying_a_start(self):
        for client_type in (Fabric, run_pipeline.Fabric, FeatureFabric, DeleteFabric, CloudClient):
            with self.subTest(client=client_type.__name__):
                client = client_type.__new__(client_type)
                session = Mock()
                result = response()
                session.request.side_effect = [requests.ConnectionError("remote disconnected"), result]
                client._session = session
                client._token = Mock(return_value="test-token")
                client.session = session
                client.tokens = {FABRIC_SCOPE: ("test-token", time.time() + 3600)}
                request = (
                    lambda: client.request(FABRIC_SCOPE, "GET", URL) if client_type is CloudClient
                    else client._request("GET", URL) if client_type is DeleteFabric
                    else client.request("GET", URL)
                )
                with patch.object(http_retries.time, "sleep") as sleep:
                    self.assertIs(request(), result)
                self.assertEqual([entry.args[0] for entry in session.request.call_args_list], ["GET", "GET"])
                sleep.assert_called_once_with(1)

    def test_interrupted_pipeline_poll_recovers_without_submitting_a_second_run(self):
        client = run_pipeline.Fabric.__new__(run_pipeline.Fabric)
        client._session = Mock()
        client._token = Mock(return_value="test-token")
        client._session.request.side_effect = [
            response(202, Location=URL),
            requests.ConnectionError("Remote end closed connection without response"),
            response(body={"status": "InProgress"}),
            requests.ReadTimeout("status read timed out"),
            response(body={"status": "Completed"}),
        ]
        with patch.object(http_retries.time, "sleep"):
            location = client.start_pipeline("11111111-1111-1111-1111-111111111111",
                                             "22222222-2222-2222-2222-222222222222", {})
            self.assertEqual(client.poll_job(location, first_wait=1)["status"], "Completed")
        methods = [entry.args[0] for entry in client._session.request.call_args_list]
        self.assertEqual(methods.count("POST"), 1)
        self.assertEqual(methods.count("GET"), 4)
        self.assertTrue(all(entry.args[1] == URL for entry in client._session.request.call_args_list[1:]))

    def test_transient_http_read_errors_retry_but_terminal_and_permission_errors_do_not(self):
        sender = Mock(side_effect=[
            response(503), response(429, **{"Retry-After": "3"}), response(),
        ])
        with patch.object(http_retries.time, "sleep") as sleep:
            self.assertEqual(http_retries.request_with_read_retry("GET", URL, send=sender).status_code, 200)
        self.assertEqual(sleep.call_args_list, [call(1), call(3)])
        for status in (400, 401, 403, 404):
            with self.subTest(status=status), patch.object(http_retries.time, "sleep") as sleep:
                sender = Mock(return_value=response(status))
                self.assertEqual(http_retries.request_with_read_retry("GET", URL, send=sender).status_code, status)
                self.assertEqual(sender.call_count, 1)
                sleep.assert_not_called()

    def test_exhausted_transport_budget_raises_the_actual_failure(self):
        failure = requests.ConnectionError("read interrupted")
        sender = Mock(side_effect=failure)
        with patch.object(http_retries.time, "sleep") as sleep, self.assertRaises(requests.ConnectionError) as caught:
            http_retries.request_with_read_retry("GET", URL, send=sender)
        self.assertIs(caught.exception, failure)
        self.assertEqual(sender.call_count, 6)
        self.assertEqual(sleep.call_args_list, [call(1), call(2), call(4), call(8), call(15)])

    def test_mutations_and_bad_certificates_are_not_replayed(self):
        for method, failure in (
            ("POST", requests.ConnectionError("acceptance unknown")),
            ("PUT", requests.Timeout("acceptance unknown")),
            ("PATCH", requests.ConnectionError("acceptance unknown")),
            ("DELETE", requests.Timeout("acceptance unknown")),
            ("GET", requests.exceptions.SSLError("invalid certificate")),
        ):
            with self.subTest(method=method), patch.object(http_retries.time, "sleep") as sleep:
                sender = Mock(side_effect=failure)
                with self.assertRaises(type(failure)):
                    http_retries.request_with_read_retry(method, URL, send=sender)
                self.assertEqual(sender.call_count, 1)
                sleep.assert_not_called()
        sender = Mock(return_value=response(503))
        self.assertEqual(http_retries.request_with_read_retry("POST", URL, send=sender).status_code, 503)
        self.assertEqual(sender.call_count, 1)

    def test_retry_after_cannot_create_an_unbounded_wait(self):
        sender = Mock(return_value=response(429, **{"Retry-After": "3600"}))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(http_retries.time, "sleep") as sleep:
            self.assertEqual(http_retries.request_with_read_retry("GET", URL, send=sender).status_code, 429)
        self.assertIn("exceeds", output.getvalue())
        sender.assert_called_once()
        sleep.assert_not_called()

    def test_retry_logging_does_not_expose_exception_payloads_or_query_tokens(self):
        sender = Mock(side_effect=[requests.ConnectionError("private-payload"), response()])
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(http_retries.time, "sleep"):
            http_retries.request_with_read_retry("GET", URL + "?token=private-query", send=sender)
        self.assertNotIn("private", output.getvalue())


class PipelineRunIdentityTests(unittest.TestCase):
    def test_resume_never_restarts_preflights_or_submits_a_second_job(self):
        for state, expected in (("Completed", 0), ("Failed", 1), ("Cancelled", 1), ("Interrupted", 2)):
            with self.subTest(state=state):
                client = Mock()
                client.resolve_workspace_id.return_value = ("11111111-1111-1111-1111-111111111111", "Demo")
                client.find_pipeline.return_value = ("22222222-2222-2222-2222-222222222222", "01_Pipe_Setup")
                if state == "Interrupted":
                    client.poll_job.side_effect = requests.ConnectionError("remote disconnected")
                else:
                    client.poll_job.return_value = {"status": state}
                output = io.StringIO()
                with (
                    patch.object(sys, "argv", ["run_pipeline.py", "--tenant", "tenant.example",
                        "--workspace", "Demo", "--resume-job-url", URL, "--yes"]),
                    patch.dict(os.environ, {"FABRIC_PIPELINE_PARAMS": "{}"}),
                    patch.object(run_pipeline, "Fabric", return_value=client),
                    patch.object(run_pipeline, "ensure_spark_runtime") as runtime,
                    patch.object(run_pipeline, "verify_environment_runtimes") as environment,
                    patch.object(run_pipeline, "ensure_key_vault_access") as vault,
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(output),
                ):
                    self.assertEqual(run_pipeline.main(), expected)
                client.start_pipeline.assert_not_called()
                client.require_idle_pipeline.assert_not_called()
                client.poll_job.assert_called_once_with(URL, first_wait=10)
                runtime.assert_not_called()
                environment.assert_not_called()
                vault.assert_not_called()
                if state == "Interrupted":
                    self.assertIn("Fabric outcome is unverified", output.getvalue())
                    self.assertNotIn("PIPELINE_JOB_TERMINAL=", output.getvalue())
                else:
                    self.assertIn("PIPELINE_JOB_TERMINAL=" + state, output.getvalue())

    def test_url_requires_explicit_matching_workspace_pipeline_and_fabric_origin(self):
        self.assertEqual(run_pipeline.validate_pipeline_job_url(URL), URL)
        for invalid in (
            URL.replace("api.fabric.microsoft.com", "untrusted.example"),
            URL + "?token=value", URL + "#fragment", URL.replace("https:", "http:"),
        ):
            with self.subTest(url=invalid), self.assertRaises(ValueError):
                run_pipeline.validate_pipeline_job_url(invalid)
        with self.assertRaisesRegex(ValueError, "does not match"):
            run_pipeline.validate_pipeline_job_url(URL, "44444444-4444-4444-4444-444444444444")

    def test_active_run_inventory_blocks_duplicate_submission(self):
        client = run_pipeline.Fabric.__new__(run_pipeline.Fabric)
        client._session = Mock()
        client._token = Mock(return_value="test-token")
        client._session.request.return_value = response(body={"value": [
            {"id": "33333333-3333-3333-3333-333333333333", "status": "InProgress"},
        ]})
        with self.assertRaisesRegex(RuntimeError, "no duplicate was submitted"):
            client.require_idle_pipeline("11111111-1111-1111-1111-111111111111",
                                         "22222222-2222-2222-2222-222222222222")
        self.assertEqual([entry.args[0] for entry in client._session.request.call_args_list], ["GET"])


if __name__ == "__main__":
    unittest.main()
