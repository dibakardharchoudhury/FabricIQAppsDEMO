"""Stable Foundry resource identities for independently bound workspace deployments."""
import hashlib
import re


def foundry_resource_suffix(workspace: str) -> str:
    if not re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", workspace,
    ):
        raise ValueError("Foundry resource names require a verified workspace GUID.")
    return "-" + workspace.replace("-", "").lower()


def workspace_project_name(workspace: str) -> str:
    return "hydro-workspace" + foundry_resource_suffix(workspace)


def native_connection_name(project_id: str, workspace: str, kind: str) -> str:
    if kind not in ("iq", "ontology") or not re.fullmatch(
        r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/?#]+/providers/"
        r"Microsoft\.CognitiveServices/accounts/[^/?#]+/projects/[A-Za-z0-9_.-]+",
        project_id, re.IGNORECASE,
    ):
        raise ValueError("Native connection names require a verified project ARM identity and kind.")
    identity = project_id.casefold() + "/" + foundry_resource_suffix(workspace)
    return "hydro-fabric-" + kind + "-" + hashlib.sha256(identity.encode()).hexdigest()[:32]
