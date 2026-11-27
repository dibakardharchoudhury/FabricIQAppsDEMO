"""Deploy the approved private Logic App and remove its temporary publisher."""
import base64
import json
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent
SUBSCRIPTION = "d4d67f11-3766-4c12-8966-599f08e759da"
GROUP = "dbfabricvnet"
BASE = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}"
HELPER = "aci-logic-private-deploy"
IDENTITY = "id-logic-private-deploy"
STORAGE = "stlogicsk5xyotnt4w2k"
STORAGE_BLOCKER = (
    "Storage shared-key authentication is disabled. WS1 content storage cannot "
    "operate in this configuration. Do not bypass governance policy; see README.md "
    "for the documented ASE v3 alternative."
)


def az(*args):
    result = subprocess.run(
        ["az", *args, "--subscription", SUBSCRIPTION, "--only-show-errors", "-o", "json"],
        text=True, capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    if args[:2] == ("container", "logs"):
        return result.stdout
    return json.loads(result.stdout) if result.stdout.strip() else None


def cleanup():
    containers = az("container", "list", "-g", GROUP)
    if any(item["name"] == HELPER for item in containers):
        az("container", "delete", "-g", GROUP, "-n", HELPER, "--yes")
    identities = az("identity", "list", "-g", GROUP)
    identity = next((item for item in identities if item["name"] == IDENTITY), None)
    if identity:
        for role in az("role", "assignment", "list", "--assignee", identity["principalId"], "--all"):
            if not role["scope"].lower().startswith(BASE.lower() + "/"):
                raise RuntimeError("Unexpected temporary identity scope; manual review required")
            az("role", "assignment", "delete", "--ids", role["id"])
        az("identity", "delete", "-g", GROUP, "-n", IDENTITY)
    # ACI can retain a subnet association briefly after deletion.
    for attempt in range(18):
        subnets = az("network", "vnet", "subnet", "list", "-g", GROUP, "--vnet-name", "vnet_dbfabric")
        if not any(item["name"] == "snet-logic-deploy" for item in subnets):
            break
        try:
            az("network", "vnet", "subnet", "delete", "-g", GROUP,
               "--vnet-name", "vnet_dbfabric", "-n", "snet-logic-deploy")
            break
        except RuntimeError:
            if attempt == 17:
                raise
            time.sleep(10)
    print("TEMPORARY_HELPER_CLEANUP completed", flush=True)


def verify(outputs):
    app_name = outputs["appId"].rsplit("/", 1)[1]
    storage_name = outputs["storageId"].rsplit("/", 1)[1]
    app = az("webapp", "show", "-g", GROUP, "-n", app_name)
    storage = az("storage", "account", "show", "-g", GROUP, "-n", storage_name)
    if app["publicNetworkAccess"] != "Disabled" or not app["httpsOnly"]:
        raise RuntimeError("Application public access/TLS verification failed")
    if storage["publicNetworkAccess"] != "Disabled" or storage["allowBlobPublicAccess"]:
        raise RuntimeError("Storage public access verification failed")
    if storage["networkRuleSet"]["defaultAction"] != "Deny":
        raise RuntimeError("Storage firewall verification failed")
    if not storage["allowSharedKeyAccess"]:
        raise RuntimeError("Azure Files runtime shared-key authentication is disabled")
    expected_endpoints = {f"pe-{app_name}"} | {
        f"pe-{storage_name}-{service}" for service in ("blob", "file", "queue", "table")
    }
    found = set()
    for endpoint in az("network", "private-endpoint", "list", "-g", GROUP):
        if endpoint["name"] not in expected_endpoints:
            continue
        if endpoint["provisioningState"] != "Succeeded" or any(
            connection["privateLinkServiceConnectionState"]["status"] != "Approved"
            for connection in endpoint["privateLinkServiceConnections"]
        ):
            raise RuntimeError("Private endpoint approval verification failed")
        found.add(endpoint["name"])
    if found != expected_endpoints:
        raise RuntimeError("Private endpoint inventory verification failed")
    if any(item["name"] == HELPER for item in az("container", "list", "-g", GROUP)):
        raise RuntimeError("Temporary container still exists")
    if any(item["name"] == IDENTITY for item in az("identity", "list", "-g", GROUP)):
        raise RuntimeError("Temporary identity still exists")
    for scope in (outputs["appId"], outputs["storageId"]):
        if az("role", "assignment", "list", "--scope", scope):
            raise RuntimeError("Unexpected remaining direct app/storage role assignment")
    print("PRIVATE_ACCESS_AND_CLEANUP_READBACK passed", flush=True)


def main():
    for account in az("storage", "account", "list", "-g", GROUP):
        if account["name"] == STORAGE and account.get("allowSharedKeyAccess") is False:
            raise RuntimeError(STORAGE_BLOCKER + " No deployment changes were made.")
    if any(item["name"] == HELPER for item in az("container", "list", "-g", GROUP)):
        raise RuntimeError("Temporary helper name already exists; inspect before proceeding")
    if any(item["name"] == IDENTITY for item in az("identity", "list", "-g", GROUP)):
        raise RuntimeError("Temporary identity name already exists; inspect before proceeding")
    result = az(
        "deployment", "group", "create", "-g", GROUP, "-n", "private-logicapp",
        "--mode", "Incremental", "--template-file", str(ROOT / "main.bicep"),
    )
    outputs = {key: value["value"] for key, value in result["properties"]["outputs"].items()}
    print(json.dumps(outputs, indent=2), flush=True)
    account = az("storage", "account", "show", "-g", GROUP,
                 "-n", outputs["storageId"].rsplit("/", 1)[1])
    if account.get("allowSharedKeyAccess") is not True:
        cleanup()
        raise RuntimeError(STORAGE_BLOCKER + " No publishing identity was created.")
    package = {
        str(path.relative_to(ROOT / "workflow")): path.read_text(encoding="utf-8")
        for path in (ROOT / "workflow").rglob("*.json")
    }
    values = {
        "appName": outputs["appId"].rsplit("/", 1)[1],
        "storageName": outputs["storageId"].rsplit("/", 1)[1],
        "helperSubnetId": outputs["helperSubnetId"],
        "scriptBase64": base64.b64encode((ROOT / "private-helper.py").read_bytes()).decode(),
        "packageBase64": base64.b64encode(json.dumps(package).encode()).decode(),
    }
    with tempfile.TemporaryDirectory(prefix="logic-private-") as directory:
        parameters = Path(directory) / "helper.parameters.json"
        parameters.write_text(json.dumps({"parameters": {
            key: {"value": value} for key, value in values.items()
        }}), encoding="utf-8")
        arguments = (
            "-g", GROUP, "-n", "private-logicapp-helper",
            "--template-file", str(ROOT / "helper.bicep"), "--parameters", f"@{parameters}",
        )
        try:
            az("deployment", "group", "validate", *arguments)
            preview = az("deployment", "group", "what-if", *arguments,
                         "--result-format", "ResourceIdOnly", "--no-pretty-print")
            if preview.get("status") != "Succeeded" or any(
                item["changeType"] not in ("Create", "Ignore", "NoChange")
                for item in preview.get("changes", [])
            ):
                raise RuntimeError("Unexpected helper what-if changes")
            az("deployment", "group", "create", *arguments)
            for attempt in range(132):
                group = az("container", "show", "-g", GROUP, "-n", HELPER)
                state = group["containers"][0].get("instanceView", {}).get("currentState", {})
                if state.get("state") == "Terminated":
                    logs = az("container", "logs", "-g", GROUP, "-n", HELPER)
                    print(logs, flush=True)
                    if state.get("exitCode") != 0 or "PRIVATE_WORKFLOW_TEST HTTP 200" not in logs:
                        raise RuntimeError("Private workflow verification failed")
                    break
                if attempt == 131:
                    raise RuntimeError("Private helper exceeded bounded execution time")
                time.sleep(10)
        finally:
            cleanup()
    verify(outputs)
    print("PRIVATE_LOGIC_APP runtime and private-access controls verified", flush=True)


if __name__ == "__main__":
    main()
