import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch


WORKER = Path(__file__).with_name("certificate-worker.py")
CERTIFICATE = {
    "id": "https://example.vault.azure.net/certificates/ai-espen-io/version",
    "sid": "https://example.vault.azure.net/secrets/ai-espen-io/version",
    "cer": "public-certificate-only",
    "attributes": {"enabled": True, "expires": "2027-11-25"},
    "policy": {
        "issuerParameters": {"name": "Self"},
        "x509CertificateProperties": {
            "subject": "CN=ai.espen.io",
            "subjectAlternativeNames": {"dnsNames": ["ai.espen.io"]},
        },
    },
}


def response(value=None, *, error=""):
    return subprocess.CompletedProcess(
        [], 1 if error else 0, json.dumps(value), error,
    )


class CertificateWorkerTests(unittest.TestCase):
    def run_worker(self, replies):
        output = io.StringIO()
        with (
            patch.dict(os.environ, {
                "IDENTITY_CLIENT_ID": "worker",
                "VAULT_NAME": "example",
                "CERTIFICATE_POLICY": "{}",
            }),
            patch("subprocess.run", side_effect=replies) as run,
            patch("time.sleep"),
            contextlib.redirect_stdout(output),
        ):
            runpy.run_path(str(WORKER))
        return run, json.loads(output.getvalue().splitlines()[-1])

    def test_creation_uses_authoritative_readback_not_creation_response(self):
        run, result = self.run_worker([
            response({}), response([]), response({}), response(CERTIFICATE),
        ])
        self.assertIn("create", run.call_args_list[2].args[0])
        self.assertIn("show", run.call_args_list[3].args[0])
        self.assertEqual(result["publicCertificate"], CERTIFICATE["cer"])
        self.assertNotIn("policy", result)

    def test_existing_certificate_is_not_rotated(self):
        run, result = self.run_worker([
            response({}), response(["ai-espen-io"]), response(CERTIFICATE),
        ])
        self.assertFalse(any("create" in call.args[0] for call in run.call_args_list))
        self.assertEqual(result["certificateId"], CERTIFICATE["id"])

    def test_creation_failure_is_not_replayed(self):
        with self.assertRaisesRegex(SystemExit, "creation failed without retry"):
            self.run_worker([
                response({}), response([]), response(error="Ambiguous transport failure"),
            ])

    def test_other_hostname_is_rejected(self):
        certificate = json.loads(json.dumps(CERTIFICATE))
        certificate["policy"]["x509CertificateProperties"]["subjectAlternativeNames"]["dnsNames"] = ["other.example"]
        with self.assertRaisesRegex(SystemExit, "did not match"):
            self.run_worker([
                response({}), response(["ai-espen-io"]), response(certificate),
            ])

    def test_permission_propagation_retries_reads_only(self):
        run, _ = self.run_worker([
            response({}), response(error="ForbiddenByRbac"),
            response([]), response({}), response(CERTIFICATE),
        ])
        self.assertEqual(sum("create" in call.args[0] for call in run.call_args_list), 1)
        self.assertEqual(sum("list" in call.args[0] for call in run.call_args_list), 2)


class PrivateProxyVerificationTests(unittest.TestCase):
    def run_check(self):
        runpy.run_path(str(WORKER.with_name("verify-nginx-proxy.py")))

    def test_unexpected_dns_blocks_network_request(self):
        with (
            patch.dict(os.environ, {"EXPECTED_PRIVATE_IP": "10.0.3.12"}),
            patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("203.0.113.1", 443))]),
            patch("http.client.HTTPSConnection") as connection,
            self.assertRaisesRegex(RuntimeError, "Private DNS"),
        ):
            self.run_check()
        connection.assert_not_called()

    def test_upstream_failure_is_not_replayed(self):
        healthy = MagicMock(status=200)
        healthy.read.return_value = b"proxy-ready; upstream status not checked\n"
        failed = MagicMock(status=503)
        failed.getheader.return_value = "503"
        with (
            patch.dict(os.environ, {
                "PUBLIC_CERTIFICATE_PEM": "public-trust",
                "EXPECTED_PRIVATE_IP": "10.0.3.12",
            }),
            patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("10.0.3.12", 443))]),
            patch("ssl.create_default_context") as context,
            patch("http.client.HTTPSConnection") as connection,
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(RuntimeError, "HTTP 503"),
        ):
            connection.return_value.getresponse.side_effect = [healthy, failed]
            self.run_check()
        context.assert_called_once_with(cadata="public-trust")
        self.assertEqual(
            connection.return_value.request.call_args_list,
            [call("GET", "/healthz"), call("GET", "/")],
        )
        connection.return_value.close.assert_called_once()

    def test_local_health_failure_prevents_forwarding(self):
        unhealthy = MagicMock(status=503)
        unhealthy.read.return_value = b"unavailable"
        with (
            patch.dict(os.environ, {
                "PUBLIC_CERTIFICATE_PEM": "public-trust",
                "EXPECTED_PRIVATE_IP": "10.0.3.12",
            }),
            patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("10.0.3.12", 443))]),
            patch("ssl.create_default_context"),
            patch("http.client.HTTPSConnection") as connection,
            self.assertRaisesRegex(RuntimeError, "no forwarded request attempted"),
        ):
            connection.return_value.getresponse.return_value = unhealthy
            self.run_check()
        connection.return_value.request.assert_called_once_with("GET", "/healthz")
        connection.return_value.close.assert_called_once()

    def test_known_forwarded_502_does_not_certify_runtime(self):
        healthy = MagicMock(status=200)
        healthy.read.return_value = b"proxy-ready; upstream status not checked\n"
        failed = MagicMock(status=502)
        failed.read.return_value = b'{"error":{"code":"BadGateway"}}'
        failed.getheader.side_effect = lambda name, default="": {
            "X-AI-Gateway-Proxy-Upstream-Status": "502",
            "Content-Type": "application/json",
        }.get(name, default)
        output = io.StringIO()
        with (
            patch.dict(os.environ, {
                "PUBLIC_CERTIFICATE_PEM": "public-trust",
                "EXPECTED_PRIVATE_IP": "10.0.3.12",
            }),
            patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("10.0.3.12", 443))]),
            patch("ssl.create_default_context"),
            patch("http.client.HTTPSConnection") as connection,
            contextlib.redirect_stdout(output),
        ):
            connection.return_value.getresponse.side_effect = [healthy, failed]
            self.run_check()
        result = json.loads(output.getvalue().splitlines()[-1])
        self.assertTrue(result["forwardingVerified"])
        self.assertEqual(result["forwardedRootStatus"], 502)
        self.assertFalse(result["upstreamRuntimeCertified"])
        self.assertFalse(result["modelOrMcpCalled"])
        self.assertEqual(
            connection.return_value.request.call_args_list,
            [call("GET", "/healthz"), call("GET", "/")],
        )
        connection.return_value.close.assert_called_once()


class CertificateLoaderTests(unittest.TestCase):
    def test_failed_private_download_is_removed_without_logging_its_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def execute(arguments, **kwargs):
                if "download" in arguments:
                    (root / "enrollment.pfx").write_bytes(b"private-placeholder")
                    return subprocess.CompletedProcess(arguments, 1, "", "sensitive-output")
                return subprocess.CompletedProcess(arguments, 0, "", "")

            with (
                patch.dict(os.environ, {
                    "VAULT_NAME": "test-vault",
                    "CERTIFICATE_FINGERPRINT": "A" * 64,
                    "IDENTITY_CLIENT_ID": "proxy-identity",
                }),
                patch("pathlib.Path", return_value=root),
                patch("subprocess.run", side_effect=execute) as run,
                self.assertRaisesRegex(SystemExit, "no private material logged") as error,
            ):
                runpy.run_path(str(WORKER.with_name("certificate-loader.py")))
            self.assertEqual(run.call_count, 2)
            self.assertFalse((root / "enrollment.pfx").exists())
            self.assertFalse((root / "ready").exists())
            self.assertNotIn("sensitive-output", str(error.exception))

    def test_loaded_certificate_is_revalidated_without_another_private_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "ready").write_text("already-loaded")

            def execute(arguments, **kwargs):
                output = "sha256 Fingerprint=" + "A" * 64 if "-fingerprint" in arguments else ""
                return subprocess.CompletedProcess(arguments, 0, output, "")

            with (
                patch.dict(os.environ, {
                    "VAULT_NAME": "test-vault",
                    "CERTIFICATE_FINGERPRINT": "A" * 64,
                }),
                patch("pathlib.Path", return_value=root),
                patch("subprocess.run", side_effect=execute) as run,
                patch("time.sleep", side_effect=SystemExit("stop-after-readiness")),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(SystemExit, "stop-after-readiness"),
            ):
                runpy.run_path(str(WORKER.with_name("certificate-loader.py")))
            self.assertEqual(run.call_count, 3)
            self.assertTrue(all(entry.args[0][0] == "openssl" for entry in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
