"""Runs inside the temporary private ACI; outputs public certificate material only."""

import json
import os
import subprocess
import sys
import time


def az(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["az", *arguments, "--only-show-errors", "--output", "json"],
        capture_output=True,
        text=True,
        timeout=180,
    )


login = az(
    "login", "--identity", "--client-id", os.environ["IDENTITY_CLIENT_ID"],
    "--allow-no-subscriptions",
)
if login.returncode:
    sys.exit("Certificate worker managed-identity login failed; no certificate request sent.")

vault = os.environ["VAULT_NAME"]
for attempt in range(20):
    listing = az("keyvault", "certificate", "list", "--vault-name", vault, "--query", "[].name")
    if listing.returncode == 0:
        break
    if "Forbidden" not in listing.stderr or attempt == 19:
        sys.exit("Certificate metadata read failed: " + listing.stderr)
    print("Waiting for private vault access and certificate-role propagation.", flush=True)
    time.sleep(15)

if "ai-espen-io" not in json.loads(listing.stdout):
    policy = json.loads(os.environ["CERTIFICATE_POLICY"])
    creation = az(
        "keyvault", "certificate", "create", "--vault-name", vault,
        "--name", "ai-espen-io", "--policy", json.dumps(policy),
    )
    if creation.returncode:
        sys.exit("Certificate creation failed without retry: " + creation.stderr)

result = az("keyvault", "certificate", "show", "--vault-name", vault, "--name", "ai-espen-io")
if result.returncode:
    sys.exit("Certificate readback failed: " + result.stderr)

certificate = json.loads(result.stdout)
policy = certificate.get("policy", {})
x509 = policy.get("x509CertificateProperties", {})
if (
    x509.get("subject") != "CN=ai.espen.io"
    or x509.get("subjectAlternativeNames", {}).get("dnsNames") != ["ai.espen.io"]
    or policy.get("issuerParameters", {}).get("name") != "Self"
    or certificate.get("attributes", {}).get("enabled") is not True
    or not certificate.get("cer")
    or not certificate.get("sid")
):
    print(json.dumps({"publicPolicy": policy, "attributes": certificate.get("attributes")}), flush=True)
    sys.exit("Existing or created certificate did not match the approved identity and issuer.")

print(json.dumps({
    "certificateId": certificate["id"],
    "secretReference": certificate["sid"],
    "publicCertificate": certificate["cer"],
    "expires": certificate["attributes"].get("expires"),
}), flush=True)
