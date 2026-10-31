"""Stable Foundry resource identities for independently bound workspace deployments."""
import re


def foundry_resource_suffix(workspace: str) -> str:
    if not re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", workspace,
    ):
        raise ValueError("Foundry resource names require a verified workspace GUID.")
    return "-" + workspace.replace("-", "").lower()
