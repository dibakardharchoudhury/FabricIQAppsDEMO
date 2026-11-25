"""Loads only the approved proxy certificate inside Azure, never into deployer output."""

import os
from pathlib import Path
import re
import subprocess
import sys
import time


def run(*arguments: str) -> str:
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=180)
    if result.returncode:
        sys.exit(f"TLS loader {arguments[0]} step failed with exit {result.returncode}; no private material logged.")
    return result.stdout.strip()


os.umask(0o077)
directory = Path("/certs")
certificate = directory / "server.crt"
private_key = directory / "server.key"
ready = directory / "ready"
vault = os.environ["VAULT_NAME"]
expected = os.environ["CERTIFICATE_FINGERPRINT"].replace(":", "").upper()
if not re.fullmatch(r"[a-zA-Z0-9-]{3,24}", vault) or not re.fullmatch(r"[A-F0-9]{64}", expected):
    sys.exit("Invalid approved vault or public certificate fingerprint.")

if not ready.exists():
    pfx = directory / "enrollment.pfx"
    pfx.unlink(missing_ok=True)
    run(
        "az", "login", "--identity", "--client-id", os.environ["IDENTITY_CLIENT_ID"],
        "--allow-no-subscriptions", "--output", "none", "--only-show-errors",
    )
    try:
        run(
            "az", "keyvault", "secret", "download",
            "--id", f"https://{vault}.vault.azure.net/secrets/ai-espen-io",
            "--file", str(pfx), "--encoding", "base64",
            "--output", "none", "--only-show-errors",
        )
        run(
            "openssl", "pkcs12", "-in", str(pfx), "-clcerts", "-nokeys",
            "-passin", "pass:", "-out", str(certificate),
        )
        run(
            "openssl", "pkcs12", "-in", str(pfx), "-nocerts", "-nodes",
            "-passin", "pass:", "-out", str(private_key),
        )
        private_key.chmod(0o600)
        certificate.chmod(0o600)
    finally:
        pfx.unlink(missing_ok=True)

fingerprint = run("openssl", "x509", "-in", str(certificate), "-noout", "-fingerprint", "-sha256")
if fingerprint.rsplit("=", 1)[-1].replace(":", "").upper() != expected:
    sys.exit("Proxy certificate fingerprint did not match the approved public certificate.")
run("openssl", "x509", "-in", str(certificate), "-noout", "-checkhost", "ai.espen.io")
run("openssl", "x509", "-in", str(certificate), "-noout", "-checkend", "86400")
ready.write_text("approved-proxy-certificate-loaded\n")
print("Approved TLS certificate loaded inside Azure; private material is not logged.", flush=True)

while True:
    time.sleep(3600)
