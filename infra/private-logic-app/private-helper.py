"""Runs only inside the temporary private ACI; never logs secrets."""
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request


def az(*args):
    result = subprocess.run(
        ["az", *args, "--only-show-errors", "-o", "json"],
        text=True, capture_output=True, check=False,
    )
    if result.returncode:
        # CLI errors can echo signed URLs or arguments; keep them out of ACI logs.
        details = next((message for message in (
            "No subscriptions found", "unrecognized arguments",
            "Failed to connect to MSI", "AuthorizationFailed",
            "AuthorizationPermissionMismatch", "InvalidAuthenticationToken",
        ) if message.lower() in result.stderr.lower()), "details withheld")
        if args[0] == "storage":
            details = result.stderr
            for variable in ("AZURE_STORAGE_KEY", "AZURE_STORAGE_CONNECTION_STRING", "AZURE_STORAGE_SAS_TOKEN"):
                if os.environ.get(variable):
                    details = details.replace(os.environ[variable], "[REDACTED]")
            details = re.sub(r"(?i)(sig|access_token|accountkey)=([^&\s]+)", r"\1=[REDACTED]", details)
            details = details[-1800:]
        raise RuntimeError(f"Azure operation failed: {' '.join(args[:3])}; {details}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def main():
    for attempt in range(18):
        try:
            az("login", "--identity", "--allow-no-subscriptions",
               "--client-id", os.environ["IDENTITY_CLIENT_ID"])
            break
        except RuntimeError as error:
            if attempt == 17:
                raise
            print(f"IDENTITY_NOT_READY {error}; retrying", flush=True)
            time.sleep(10)
    app_id = os.environ["APP_ID"]
    storage_id = os.environ["STORAGE_ID"]
    app = app_id.rsplit("/", 1)[1]
    account = storage_id.rsplit("/", 1)[1]
    for host in [f"{app}.azurewebsites.net", f"{app}.scm.azurewebsites.net"] + [
        f"{account}.{service}.core.windows.net"
        for service in ("blob", "file", "queue", "table")
    ]:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443)}
        if not addresses or not all(ipaddress.ip_address(ip).is_private for ip in addresses):
            raise RuntimeError(f"Non-private DNS resolution: {host}")
        print(f"PRIVATE_DNS {host} {','.join(sorted(addresses))}", flush=True)

    key = None
    for attempt in range(30):
        try:
            key = az(
                "rest", "--method", "post", "--url",
                f"https://management.azure.com{storage_id}/listKeys?api-version=2023-05-01",
            )["keys"][0]["value"]
            break
        except RuntimeError:
            if attempt == 29:
                raise
            time.sleep(10)

    # Credentials stay inside this short-lived Azure container.
    os.environ["AZURE_STORAGE_ACCOUNT"] = account
    os.environ["AZURE_STORAGE_KEY"] = key
    for directory in ("site", "site/wwwroot", "site/wwwroot/starter"):
        az("storage", "directory", "create", "--auth-mode", "key",
           "--share-name", "content", "--name", directory)
    package = json.loads(base64.b64decode(os.environ["PACKAGE_B64"]))
    for name, contents in package.items():
        path = Path("/tmp") / Path(name).name
        path.write_text(contents, encoding="utf-8")
        az("storage", "file", "upload", "--auth-mode", "key", "--share-name", "content",
           "--source", str(path), "--path", f"site/wwwroot/{name}")
        path.unlink()
    print("PRIVATE_CONTENT_UPLOAD succeeded", flush=True)

    endpoint = (
        f"https://management.azure.com{app_id}/hostruntime/runtime/webhooks/workflow"
        "/api/management/workflows/starter/triggers/manual/listCallbackUrl?api-version=2018-11-01"
    )
    for attempt in range(90):
        try:
            callback = az("rest", "--method", "post", "--url", endpoint)["value"]
            request = urllib.request.Request(
                callback, data=b"{}", headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=90) as response:
                body = json.load(response)
                if response.status != 200 or body != {"status": "ok", "workflow": "starter"}:
                    raise RuntimeError("Unexpected private workflow response")
            print("PRIVATE_WORKFLOW_TEST HTTP 200; exact starter response verified", flush=True)
            return
        except (RuntimeError, urllib.error.URLError, KeyError):
            if attempt == 89:
                raise RuntimeError("Private workflow did not become ready; callback withheld") from None
            time.sleep(10)


if __name__ == "__main__":
    main()
