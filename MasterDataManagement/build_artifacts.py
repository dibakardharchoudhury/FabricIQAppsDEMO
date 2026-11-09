"""Build deployable Fabric notebook/pipeline payloads without mutating a workspace."""

import argparse
import json
import hashlib
import re
import uuid
import zipfile
from pathlib import Path

from .contract import load_approved


def build_notebook(workspace_id, lakehouse_id, lakehouse_name, digest):
    for value in (workspace_id, lakehouse_id):
        if str(uuid.UUID(value)) != value.lower():
            raise ValueError("Workspace and Lakehouse must be canonical GUIDs.")
    if not lakehouse_name.strip():
        raise ValueError("The live Lakehouse display name is required.")
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("A separately approved SHA-256 digest is required.")

    def cell(kind, source, tags=None):
        result = {
            "cell_type": kind, "metadata": {"tags": tags or []},
            "id": hashlib.sha256(source.encode("utf-8")).hexdigest()[:16],
            "source": source.splitlines(keepends=True),
        }
        if kind == "code":
            result.update(execution_count=None, outputs=[])
        return result

    parameters = (
        f"expected_sha256 = {digest!r}\n"
        f"target_workspace_id = {workspace_id!r}\n"
        f"target_lakehouse_id = {lakehouse_id!r}\n"
    )
    run = """import json
import sys
import notebookutils

runtime_zip = "/lakehouse/default/Files/master-data/mdm-runtime.zip"
if runtime_zip not in sys.path:
    sys.path.insert(0, runtime_zip)
from MasterDataManagement.fabric_runner import run_release

contract_path = f"Files/master-data/contracts/{expected_sha256}.json"
raw_contract = notebookutils.fs.head(contract_path, 2000000)
receipt = run_release(
    spark, raw_contract, expected_sha256,
    target_workspace_id, target_lakehouse_id, notebookutils.runtime.context,
    notebookutils.credentials.getToken("https://api.fabric.microsoft.com"),
)
notebookutils.notebook.exit(json.dumps({
    key: receipt[key] for key in ("status", "commit_table", "release_hash", "semantic_model_status")
}))
"""
    return {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Synapse PySpark", "language": "python", "name": "synapse_pyspark"},
            "language_info": {"name": "python"},
            "dependencies": {"lakehouse": {
                "default_lakehouse": lakehouse_id, "default_lakehouse_name": lakehouse_name,
                "default_lakehouse_workspace_id": workspace_id,
            }},
        },
        "cells": [
            cell("markdown", "# Governed Silver-to-Gold release\n\nRun only after Silver succeeds. "
                 "The pinned digest is approved separately from the browser draft. Failures do not publish a release marker.\n"),
            cell("code", parameters, ["parameters"]),
            cell("code", run),
        ],
    }


def build_pipeline(workspace_id, notebook_id):
    for value in (workspace_id, notebook_id):
        if str(uuid.UUID(value)) != value.lower():
            raise ValueError("Workspace and notebook must be canonical GUIDs.")
    return {"properties": {
        "concurrency": 1,
        "activities": [{
            "name": "Validate_and_publish_golden_release", "type": "TridentNotebook", "dependsOn": [],
            "policy": {"timeout": "0.02:00:00", "retry": 0, "retryIntervalInSeconds": 60,
                       "secureInput": True, "secureOutput": True},
            "typeProperties": {"notebookId": notebook_id, "workspaceId": workspace_id, "parameters": {}},
        }],
    }}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--approved-sha256", required=True)
    parser.add_argument("--target-workspace", required=True)
    parser.add_argument("--target-lakehouse", required=True)
    parser.add_argument("--target-lakehouse-name", required=True)
    parser.add_argument("--notebook-id", help="Actual deployed notebook item ID; omit to build only the notebook/runtime.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_approved(args.contract.read_bytes(), args.approved_sha256)
    notebook = build_notebook(args.target_workspace, args.target_lakehouse, args.target_lakehouse_name, args.approved_sha256)
    args.output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output / "mdm-runtime.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        root = Path(__file__).parent
        for name in ("__init__.py", "contract.py", "fabric_runner.py"):
            archive.write(root / name, f"MasterDataManagement/{name}")
    notebook_path = args.output / "MDM_001_publish_gold.ipynb"
    notebook_path.write_text(json.dumps(notebook, indent=2), encoding="utf-8")
    if args.notebook_id:
        (args.output / "pipeline-content.json").write_text(json.dumps(
            build_pipeline(args.target_workspace, args.notebook_id), indent=2,
        ), encoding="utf-8")
    print(f"Built {notebook_path}. No Fabric resources were created, run or published.")


if __name__ == "__main__":
    main()
