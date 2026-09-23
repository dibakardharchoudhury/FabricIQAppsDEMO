"""Opt-in, Git-free feature workspace setup for deploy_fabric_app.py."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from uuid import UUID

import requests

from sync_workspace_from_git import (
    FABRIC_BASE,
    Fabric,
    configure_weather_assets,
    fabric_operation_url,
    bind_notebook_definition,
    notebook_definition,
)
from key_vault_preflight import PreflightError, ensure_key_vault_access


class FeatureWorkspaceError(RuntimeError):
    pass


ENERGY_NOTEBOOK = "Geo_001_ingest_energy_context"
ENERGY_PIPELINE = "04_Pipe_EnergyMap"
ENERGY_ITEMS = {ENERGY_NOTEBOOK, ENERGY_PIPELINE}


@dataclass(frozen=True)
class FeatureConfig:
    tenant_id: str
    workspace_id: str
    subscription_id: str
    resource_group: str
    vault_name: str
    location: str
    allow_public_api_group: bool
    state_path: Path
    env_suffix: str = "V6"
    enable_energy_map: bool = False

    @classmethod
    def load(cls, path: Path, tenant_id: str, workspace_id: str) -> FeatureConfig:
        raw = json.loads(path.read_text(encoding="utf-8"))
        required = {
            "tenant_id", "workspace_id", "subscription_id", "resource_group",
            "vault_name", "location", "allow_public_api_group",
        }
        if not isinstance(raw, dict) or not required.issubset(raw):
            raise FeatureWorkspaceError("Feature bootstrap config is missing required fields.")
        if set(raw) - required - {"env_suffix", "enable_energy_map"}:
            raise FeatureWorkspaceError("Feature bootstrap config contains unsupported fields.")
        for key in ("tenant_id", "workspace_id", "subscription_id"):
            try:
                raw[key] = str(UUID(raw[key]))
            except (ValueError, TypeError, AttributeError) as exc:
                raise FeatureWorkspaceError(f"Invalid feature bootstrap {key}.") from exc
        if raw["tenant_id"] != tenant_id.lower() or raw["workspace_id"] != workspace_id.lower():
            raise FeatureWorkspaceError("Feature bootstrap config does not match the deployment target.")
        for key, pattern in (
            ("resource_group", r"[A-Za-z0-9_-]{1,90}"),
            ("vault_name", r"[a-z][a-z0-9-]{1,22}[a-z0-9]"),
            ("location", r"[a-z0-9]+"),
            ("env_suffix", r"[A-Za-z0-9_]{1,24}"),
        ):
            value = raw.get(key, "V6" if key == "env_suffix" else "")
            if not isinstance(value, str) or not re.fullmatch(pattern, value):
                raise FeatureWorkspaceError(f"Invalid feature bootstrap {key}.")
        if type(raw["allow_public_api_group"]) is not bool:
            raise FeatureWorkspaceError("allow_public_api_group must be a JSON boolean.")
        if type(raw.get("enable_energy_map", False)) is not bool:
            raise FeatureWorkspaceError("enable_energy_map must be a JSON boolean.")
        return cls(**raw, state_path=path.with_suffix(".state.json"))


def load_feature_config(tenant_id: str, workspace_id: str) -> FeatureConfig | None:
    path = os.environ.get("FABRIC_FEATURE_CONFIG", "").strip()
    return FeatureConfig.load(Path(path), tenant_id, workspace_id) if path else None


def clean_notebook_dependencies(source: str) -> str:
    pattern = r"(?m)^# METADATA \*+\n\n((?:# META[^\n]*\n)+)"
    match = re.search(pattern, source)
    if not match:
        raise FeatureWorkspaceError("Notebook has no Fabric metadata header.")
    metadata = json.loads("\n".join(line.removeprefix("# META ") for line in match[1].splitlines()))
    metadata["dependencies"] = {}
    replacement = "\n".join(f"# META {line}" for line in json.dumps(metadata, indent=2).splitlines()) + "\n"
    return source[:match.start(1)] + replacement + source[match.end(1):]


def rebind_pipeline(content: dict[str, Any], notebook_ids: dict[str, str],
                    workspace_id: str, defaults: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(content))

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for entry in value:
                visit(entry)
        elif isinstance(value, dict):
            if value.get("type") == "TridentNotebook":
                props = value["typeProperties"]
                source_id = props.get("notebookId", "")
                if source_id not in notebook_ids:
                    raise FeatureWorkspaceError(f"Unresolved notebook reference in {value.get('name')}.")
                props["notebookId"] = notebook_ids[source_id]
                props["workspaceId"] = workspace_id
            for entry in value.values():
                visit(entry)

    visit(result)
    for name, spec in result.get("properties", {}).get("parameters", {}).items():
        if name in defaults:
            spec["defaultValue"] = defaults[name]
    return result


def definition_part(path: str, content: bytes) -> dict[str, str]:
    return {"path": path, "payloadType": "InlineBase64", "payload": base64.b64encode(content).decode("ascii")}


class FeatureFabric(Fabric):
    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.hostname != "api.fabric.microsoft.com"
                or parsed.port not in (None, 443) or parsed.username or parsed.password):
            raise FeatureWorkspaceError("Refusing a non-Fabric API URL.")
        headers = dict(kwargs.pop("headers", {}))
        headers["x-ms-fabric-skill"] = "git-integration-operations-cli"
        kwargs["allow_redirects"] = False
        return super().request(method, url, headers=headers, **kwargs)

    def poll_lro(self, response: requests.Response) -> requests.Response:
        if response.status_code != 202:
            return response
        try:
            location = fabric_operation_url(response)
        except ValueError as exc:
            raise FeatureWorkspaceError(str(exc)) from exc
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            time.sleep(max(1, int(response.headers.get("Retry-After", "5"))))
            response = self.request("GET", location)
            check_response(response, "Read Fabric operation", {200, 202})
            state = response.json().get("status")
            if state == "Succeeded":
                return response
            if state in {"Failed", "Cancelled"}:
                error = response.json().get("error", {})
                raise FeatureWorkspaceError(f"Fabric operation {state}: {error.get('errorCode', 'unknown error')}.")
        raise FeatureWorkspaceError(f"Fabric operation timed out; inspect {location} before retrying.")


def check_response(response: requests.Response, action: str, expected: set[int]) -> None:
    if response.status_code not in expected:
        raise FeatureWorkspaceError(
            f"{action} failed: HTTP {response.status_code}; "
            f"request {response.headers.get('x-ms-request-id', '(not supplied)')}."
        )


class FeatureWorkspace:
    def __init__(self, config: FeatureConfig, repo_root: Path):
        self.config = config
        self.repo_root = repo_root
        self.fabric = FeatureFabric(config.tenant_id)
        self.base = f"{FABRIC_BASE}/workspaces/{config.workspace_id}"
        self.marker = f"Feature bootstrap {config.workspace_id}"
        self.state: dict[str, Any] = {"workspace_id": config.workspace_id, "items": {}, "jobs": {}}
        if config.state_path.exists():
            self.state = json.loads(config.state_path.read_text(encoding="utf-8"))
            if self.state.get("workspace_id") != config.workspace_id:
                raise FeatureWorkspaceError("Bootstrap checkpoint belongs to another workspace.")
        self.specs = self._read_specs()
        self.source_digest = hashlib.sha256(b"".join(
            path.encode() + data
            for spec in self.specs if spec["name"] not in ENERGY_ITEMS
            for path, data in sorted(spec["files"].items())
        )).hexdigest()
        self.energy_digest = hashlib.sha256(b"".join(
            path.encode() + data
            for spec in self.specs if spec["name"] in ENERGY_ITEMS
            for path, data in sorted(spec["files"].items())
        )).hexdigest()

    def _save(self) -> None:
        temporary = self.config.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.config.state_path)

    def _read_specs(self) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        logical_ids: set[str] = set()
        names: set[tuple[str, str]] = set()
        for folder, item_type in (
            ("Environments", "Environment"), ("Notebooks", "Notebook"),
            ("Orchestrator_Pipelines", "DataPipeline"),
        ):
            for directory in sorted((self.repo_root / folder).glob(f"*.{item_type}")):
                platform = json.loads((directory / ".platform").read_text(encoding="utf-8"))
                metadata = platform["metadata"]
                name = metadata["displayName"]
                if name in ENERGY_ITEMS and not self.config.enable_energy_map:
                    continue
                logical_id = platform["config"]["logicalId"]
                if metadata["type"] != item_type or logical_id in logical_ids or (item_type, name) in names:
                    raise FeatureWorkspaceError(f"Invalid or duplicate source item {directory.name}.")
                logical_ids.add(logical_id)
                names.add((item_type, name))
                files = {}
                for source in sorted(directory.rglob("*")):
                    if source.is_file():
                        source.resolve().relative_to(self.repo_root.resolve())
                        files[source.relative_to(directory).as_posix()] = source.read_bytes()
                specs.append({
                    "folder": folder, "type": item_type, "name": name, "logical_id": logical_id,
                    "platform": platform, "files": files,
                })
        required = {
            ("Notebook", "RTI_001_create_lakehouse_SelfContained"),
            ("Notebook", "RTI_Orchestrator_Setup"),
            ("Notebook", "RTI_011_seed_sql_wire_graphql_agent"),
            ("DataPipeline", "01_Pipe_Setup"), ("DataPipeline", "02_Pipe_Stream"),
        }
        if not required.issubset(names):
            raise FeatureWorkspaceError("The checkout is missing required feature workspace items.")
        if self.config.enable_energy_map and not {
            ("Notebook", ENERGY_NOTEBOOK), ("DataPipeline", ENERGY_PIPELINE),
        }.issubset(names):
            raise FeatureWorkspaceError("The checkout is missing the enabled energy map ingestion items.")
        # Check every pipeline reference before provisioning any items.
        notebook_ids = {s["logical_id"]: s["logical_id"] for s in specs if s["type"] == "Notebook"}
        for spec in specs:
            if spec["type"] == "DataPipeline":
                rebind_pipeline(json.loads(spec["files"]["pipeline-content.json"]),
                                notebook_ids, self.config.workspace_id, {})
        return specs

    def _folder(self, name: str) -> str:
        matches = [f for f in self.fabric.list_workspace_folders(self.config.workspace_id)
                   if f.get("displayName") == name and not f.get("parentFolderId")]
        if len(matches) > 1:
            raise FeatureWorkspaceError(f"Ambiguous workspace folder {name}.")
        if matches:
            return matches[0]["id"]
        response = self.fabric.request("POST", f"{self.base}/folders", json={"displayName": name})
        check_response(response, f"Create folder {name}", {200, 201})
        return response.json()["id"]

    def _upsert(self, spec: dict[str, Any], folder_id: str,
                notebook_ids: dict[str, str], defaults: dict[str, Any]) -> str:
        name, item_type = spec["name"], spec["type"]
        files = dict(spec["files"])
        platform = json.loads(json.dumps(spec["platform"]))
        platform["metadata"]["description"] = self.marker
        files[".platform"] = json.dumps(platform).encode()
        if item_type == "Notebook":
            files["notebook-content.py"] = clean_notebook_dependencies(files["notebook-content.py"].decode()).encode()
        elif item_type == "DataPipeline":
            files["pipeline-content.json"] = json.dumps(rebind_pipeline(
                json.loads(files["pipeline-content.json"]), notebook_ids,
                self.config.workspace_id, defaults,
            )).encode()
        definition: dict[str, Any] = {"parts": [definition_part(path, data) for path, data in files.items()]}
        if item_type == "Notebook":
            definition["format"] = "fabricGitSource"
        digest = hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()
        matches = [i for i in self.fabric.list_workspace_items(self.config.workspace_id)
                   if i.get("type") == item_type and i.get("displayName") == name]
        if len(matches) > 1:
            raise FeatureWorkspaceError(f"Ambiguous target item {name}.")
        if matches:
            item = matches[0]
            if item.get("description") != self.marker:
                raise FeatureWorkspaceError(f"Refusing to overwrite unowned target item {name}.")
            item_id = item["id"]
            checkpoint = self.state["items"].get(spec["logical_id"], {})
            if checkpoint.get("id") == item_id and checkpoint.get("digest") == digest:
                print(f"  Reusing {name}.", flush=True)
                return item_id
            response = self.fabric.request(
                "POST", f"{self.base}/items/{item_id}/updateDefinition?updateMetadata=true",
                json={"definition": definition},
            )
        else:
            response = self.fabric.request("POST", f"{self.base}/items", json={
                "displayName": name, "type": item_type, "folderId": folder_id,
                "description": self.marker, "definition": definition,
            })
        check_response(response, f"Provision {name}", {200, 201, 202})
        self.fabric.poll_lro(response)
        for attempt in range(12):
            matches = [i for i in self.fabric.list_workspace_items(self.config.workspace_id)
                       if i.get("type") == item_type and i.get("displayName") == name]
            if len(matches) == 1:
                break
            time.sleep(5)
        if len(matches) != 1:
            raise FeatureWorkspaceError(f"Cannot resolve provisioned item {name}.")
        item_id = matches[0]["id"]
        self.state["items"][spec["logical_id"]] = {"id": item_id, "digest": digest}
        self._save()
        print(f"  Provisioned {name}.", flush=True)
        return item_id

    def _item(self, name: str, item_type: str) -> dict[str, Any]:
        matches = [i for i in self.fabric.list_workspace_items(self.config.workspace_id)
                   if i.get("displayName") == name and i.get("type") == item_type]
        if len(matches) != 1:
            raise FeatureWorkspaceError(f"Expected one {item_type} named {name}, found {len(matches)}.")
        return matches[0]

    def _run(self, name: str, item_type: str, body: dict[str, Any] | None = None) -> None:
        item = self._item(name, item_type)
        request_digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        digest = self.energy_digest if name in ENERGY_ITEMS else self.source_digest
        key = f"{item['id']}:{digest}:{request_digest}"
        status_url = self.state["jobs"].get(key)
        if status_url:
            response = self.fabric.request("GET", status_url)
            check_response(response, f"Resume {name}", {200})
            state = response.json().get("status")
            if state == "Completed":
                print(f"  {name} already completed for this bootstrap.", flush=True)
                return
            if state in {"Failed", "Cancelled", "Deduped"}:
                print(f"  Retrying the previously {state} job {name}.", flush=True)
                status_url = None
        if not status_url:
            if item_type == "Notebook":
                url = f"{self.base}/notebooks/{item['id']}/jobs/execute/instances?beta=false"
            else:
                url = f"{self.base}/items/{item['id']}/jobs/Pipeline/instances"
            response = self.fabric.request("POST", url, json=body or {})
            check_response(response, f"Start {name}", {202})
            status_url = response.headers.get("Location")
            if not status_url:
                raise FeatureWorkspaceError(f"{name} returned no job status URL.")
            self.state["jobs"][key] = status_url
            self._save()
        print(f"  Waiting for {name}: {status_url}", flush=True)
        deadline = time.monotonic() + 7800
        previous = ""
        while time.monotonic() < deadline:
            time.sleep(max(10, int(response.headers.get("Retry-After", "15"))))
            response = self.fabric.request("GET", status_url)
            check_response(response, f"Poll {name}", {200})
            state = response.json().get("status", "")
            if state != previous:
                print(f"  {name}: {state}", flush=True)
                previous = state
            if state == "Completed":
                return
            if state in {"Failed", "Cancelled", "Deduped"}:
                reason = response.json().get("failureReason") or {}
                raise FeatureWorkspaceError(f"{name} {state}: {reason.get('message', 'see Fabric job details')}")
        raise FeatureWorkspaceError(f"{name} timed out; the job remains recorded at {status_url}.")

    def prepare(self) -> None:
        from feature_prerequisites import FeaturePrerequisiteError, ensure_feature_prerequisites

        connection = self.fabric.request("GET", f"{self.base}/git/connection")
        check_response(connection, "Check Git isolation", {200})
        if connection.json().get("gitConnectionState") != "NotConnected":
            raise FeatureWorkspaceError("Git-free bootstrap requires a Git-disconnected target workspace.")
        workspace = self.fabric.request("GET", self.base)
        check_response(workspace, "Read target capacity assignment", {200})
        capacity_id = workspace.json().get("capacityId")
        capacities = []
        url = f"{FABRIC_BASE}/capacities"
        seen = set()
        while url:
            if url in seen:
                raise FeatureWorkspaceError("Capacity pagination returned a repeated continuation.")
            seen.add(url)
            response = self.fabric.request("GET", url)
            check_response(response, "Read target capacity status", {200})
            page = response.json()
            capacities.extend(page.get("value") or [])
            url = page.get("continuationUri")
            if not url and page.get("continuationToken"):
                url = f"{FABRIC_BASE}/capacities?continuationToken={quote(page['continuationToken'], safe='')}"
        matches = [capacity for capacity in capacities if capacity.get("id") == capacity_id]
        if len(matches) != 1 or matches[0].get("state") != "Active":
            raise FeatureWorkspaceError("The target Fabric capacity must be active before feature provisioning.")
        print("Preparing dedicated feature workspace prerequisites.", flush=True)
        try:
            prerequisites = ensure_feature_prerequisites(
                tenant_id=self.config.tenant_id, workspace_id=self.config.workspace_id,
                subscription_id=self.config.subscription_id, resource_group=self.config.resource_group,
                vault_name=self.config.vault_name, location=self.config.location,
                allow_public_api_group=self.config.allow_public_api_group,
            )
        except FeaturePrerequisiteError as exc:
            raise FeatureWorkspaceError(str(exc)) from exc
        self.state["prerequisites"] = prerequisites
        self._save()
        try:
            ensure_key_vault_access(
                self.config.tenant_id, self.config.workspace_id, prerequisites["key_vault_uri"],
            )
        except PreflightError as exc:
            raise FeatureWorkspaceError(f"Feature Key Vault private connectivity is not ready: {exc}") from exc
        self.state["private_connectivity_ready"] = True
        self._save()
        defaults = {
            "env_suffix": self.config.env_suffix, "workspace_id": self.config.workspace_id,
            "key_vault_uri": prerequisites["key_vault_uri"],
            "key_vault_tenant_id_secret_name": "tenantid",
            "key_vault_client_id_secret_name": "clientid",
            "key_vault_client_secret_name": "clientsecret",
            "ops_agent_teams_team_id": "", "ops_agent_teams_channel_id": "",
            "ops_agent_run_as_user": "", "enable_weather_schedule": False,
            "per_notebook_timeout_secs": 3600,
        }
        print("Provisioning Fabric items from this checkout without Git.", flush=True)
        notebook_ids = {}
        folders = {name: self._folder(name) for name in {s["folder"] for s in self.specs}}
        for spec in self.specs:
            item_id = self._upsert(spec, folders[spec["folder"]], notebook_ids, defaults)
            if spec["type"] == "Notebook":
                notebook_ids[spec["logical_id"]] = item_id
        configure_weather_assets(self.fabric, self.config.workspace_id, git_updated=False)
        self._run("01_Pipe_Setup", "DataPipeline", {"executionData": {"parameters": defaults}})
        configure_weather_assets(self.fabric, self.config.workspace_id, git_updated=False)
        self._verify_schedules_disabled()
        for name, item_type in (
            (f"Energy_IQ_LakehouseRTI_{self.config.env_suffix}", "Lakehouse"),
            (f"RTI_Demo_Eventhouse_{self.config.env_suffix}", "Eventhouse"),
            (f"RTI_Demo_Ontology_{self.config.env_suffix}", "Ontology"),
            (f"RTI_Demo_OPCUA_TelemetryStats_{self.config.env_suffix}", "KQLDashboard"),
            (f"RTI_Demo_Agent_{self.config.env_suffix}", "DataAgent"),
            (f"RTI_Demo_OpsAgent_{self.config.env_suffix}", "OperationsAgent"),
            ("Pipe_SendEmailAlert", "DataPipeline"),
        ):
            self._item(name, item_type)
        if self.config.enable_energy_map:
            self._prepare_energy_map()

    def _prepare_energy_map(self) -> None:
        name = f"Hydro_GeoContext_{self.config.env_suffix}"
        matches = [item for item in self.fabric.list_workspace_items(self.config.workspace_id)
                   if item.get("type") == "Lakehouse" and item.get("displayName") == name]
        if len(matches) > 1:
            raise FeatureWorkspaceError("Energy context Lakehouse discovery is ambiguous.")
        marker = f"{self.marker}: energy context"
        if matches and matches[0].get("description") != marker:
            raise FeatureWorkspaceError("Refusing to bind to an unowned energy context Lakehouse.")
        if not matches:
            response = self.fabric.request("POST", f"{self.base}/lakehouses", json={
                "displayName": name, "description": marker,
                "creationPayload": {"enableSchemas": False},
            })
            check_response(response, "Create energy context Lakehouse", {200, 201, 202})
            self.fabric.poll_lro(response)
        for attempt in range(12):
            matches = [item for item in self.fabric.list_workspace_items(self.config.workspace_id)
                       if item.get("type") == "Lakehouse" and item.get("displayName") == name]
            if len(matches) == 1:
                break
            time.sleep(5)
        if len(matches) != 1:
            raise FeatureWorkspaceError("The energy context Lakehouse did not become visible.")
        lakehouse = matches[0]
        self.state["energy_lakehouse_id"] = lakehouse["id"]
        self._save()
        notebook = self._item(ENERGY_NOTEBOOK, "Notebook")
        definition = notebook_definition(self.fabric, self.config.workspace_id, notebook["id"])
        target_lakehouse = {
            "default_lakehouse": lakehouse["id"], "default_lakehouse_name": name,
            "default_lakehouse_workspace_id": self.config.workspace_id,
            "known_lakehouses": [{"id": lakehouse["id"]}],
        }
        if bind_notebook_definition(definition, target_lakehouse, {}):
            response = self.fabric.request("POST",
                f"{self.base}/notebooks/{notebook['id']}/updateDefinition?updateMetadata=true",
                json={"definition": definition},
            )
            check_response(response, "Bind energy map notebook", {200, 202})
            self.fabric.poll_lro(response)
        self._run(ENERGY_PIPELINE, "DataPipeline", {"executionData": {"parameters": {
            "workspace_id": self.config.workspace_id, "env_suffix": self.config.env_suffix,
            "refresh_mode": "all", "force_refresh": False,
        }}})
        self._publish_energy_read_models(lakehouse["id"])

    def _publish_energy_read_models(self, lakehouse_id: str) -> None:
        database = f"RTI_Demo_Eventhouse_{self.config.env_suffix}"
        eventhouse = self._item(database, "Eventhouse")
        response = self.fabric.request("GET", f"{self.base}/eventhouses/{eventhouse['id']}")
        check_response(response, "Read energy serving Eventhouse", {200})
        cluster = response.json().get("properties", {}).get("queryServiceUri", "").rstrip("/")
        if not cluster.startswith("https://") or not (urlparse(cluster).hostname or "").endswith(".kusto.fabric.microsoft.com"):
            raise FeatureWorkspaceError("Unexpected energy serving endpoint.")
        token = self.fabric._credential.get_token(f"{cluster}/.default").token
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url = f"{self.base}/lakehouses/{lakehouse_id}/tables"
        discovered = []
        seen = set()
        while url:
            if url in seen:
                raise FeatureWorkspaceError("Energy table discovery repeated a continuation.")
            seen.add(url)
            response = self.fabric.request("GET", url)
            check_response(response, "Discover energy Delta table locations", {200})
            payload = response.json()
            discovered.extend(payload.get("data") or [])
            url = payload.get("continuationUri")
            if not url and payload.get("continuationToken"):
                url = f"{self.base}/lakehouses/{lakehouse_id}/tables?continuationToken={quote(payload['continuationToken'], safe='')}"
        for name, table in (("HydroGeoFeatures", "geo_map_features"), ("HydroGeoStatus", "geo_source_status")):
            matches = [item for item in discovered if item.get("name", "").split(".")[-1] == table]
            if len(matches) != 1 or matches[0].get("format", "").lower() != "delta":
                raise FeatureWorkspaceError(f"Cannot resolve the energy Delta table {table}.")
            location = matches[0].get("location", "")
            parts = urlparse(location)
            if (parts.scheme != "abfss" or parts.username != self.config.workspace_id
                    or parts.password is not None or parts.port is not None
                    or not (parts.hostname or "").endswith("onelake.dfs.fabric.microsoft.com")
                    or not parts.path.startswith(f"/{lakehouse_id}/Tables/")
                    or parts.query or parts.fragment or "'" in location or ";" in location):
                raise FeatureWorkspaceError("Energy table location is outside the selected GeoContext Lakehouse.")
            query = f".create-or-alter external table {name} kind=delta (h@'{location};impersonate')"
            response = requests.post(f"{cluster}/v1/rest/mgmt", headers=headers,
                                     json={"db": database, "csl": query}, timeout=90)
            check_response(response, f"Publish {name} map read model", {200})
            if response.json().get("error") or response.json().get("Exceptions"):
                raise FeatureWorkspaceError(f"Eventhouse rejected the {name} map read model.")
        response = requests.post(f"{cluster}/v1/rest/query", headers=headers, json={
            "db": database,
            "csl": "external_table('HydroGeoStatus') | project layer_id, state, row_count",
        }, timeout=90)
        check_response(response, "Verify energy map source status", {200})
        payload = response.json()
        if payload.get("error") or payload.get("Exceptions"):
            raise FeatureWorkspaceError("Eventhouse returned a partial or failed energy source-status query.")
        tables = payload.get("Tables") or []
        rows = tables[0].get("Rows", []) if tables else []
        required = {
            "transmission", "regional", "distribution", "sea-cables", "masts", "transformers",
            "hydro-plants", "reservoirs", "power-balance", "power-flows", "grid-frequency", "umm",
        }
        if len(rows) != len(required) or any(len(row) != 3 or row[0] not in required for row in rows):
            raise FeatureWorkspaceError("Energy map source status does not match the twelve-layer contract.")
        ready = {row[0] for row in rows if row[1] == "ready"
                 and type(row[2]) is int and (row[2] > 0 or (row[0] == "umm" and row[2] == 0))}
        if required != ready:
            raise FeatureWorkspaceError(f"Energy map import is incomplete: {sorted(required - ready)}.")
        self.state["energy_map_verification"] = {row[0]: row[2] for row in rows}
        self._save()
        print(f"Energy map read models are ready: {len(ready)} layers.", flush=True)

    def configure_app(self, env_path: Path) -> None:
        values = {
            "RAYFIN_PUBLIC_EVENTHOUSE_NAME": f"RTI_Demo_Eventhouse_{self.config.env_suffix}",
            "RAYFIN_PUBLIC_KQL_DATABASE": f"RTI_Demo_Eventhouse_{self.config.env_suffix}",
            "RAYFIN_PUBLIC_LAKEHOUSE_NAME": f"Energy_IQ_LakehouseRTI_{self.config.env_suffix}",
            "RAYFIN_PUBLIC_KQL_DASHBOARD_NAME": f"RTI_Demo_OPCUA_TelemetryStats_{self.config.env_suffix}",
        }
        content = env_path.read_text(encoding="utf-8")
        for key, value in values.items():
            pattern = rf"(?m)^{re.escape(key)}=.*$"
            if re.search(pattern, content):
                content = re.sub(pattern, f"{key}={value}", content)
            else:
                content += f"\n{key}={value}\n"
        env_path.write_text(content, encoding="utf-8")

    def _verify_schedules_disabled(self) -> None:
        names = ["03_Pipe_Weather"] + ([ENERGY_PIPELINE] if self.config.enable_energy_map else [])
        for name in names:
            pipeline = self._item(name, "DataPipeline")
            response = self.fabric.request("GET", f"{self.base}/items/{pipeline['id']}/jobs/Pipeline/schedules")
            check_response(response, f"Check feature {name} schedule", {200})
            if any(schedule.get("enabled") for schedule in response.json().get("value", [])):
                raise FeatureWorkspaceError(f"Feature {name} schedule must remain disabled.")

    def finish(self) -> None:
        self._run("RTI_011_seed_sql_wire_graphql_agent", "Notebook", {
            "parameters": [
                {"name": "sql_db_item_name", "value": "hydro-operations-ui", "type": "Text"},
                {"name": "strict_setup", "value": True, "type": "Boolean"},
            ],
        })
        self._run("02_Pipe_Stream", "DataPipeline")
        self._verify_schedules_disabled()
        self._verify_data()
        self.state["setup_completed"] = True
        self._save()
        print("Feature workspace setup, SQL seed, GraphQL provisioning and demo stream completed.", flush=True)

    def _verify_data(self) -> None:
        graphql = self._item("Hydro_STID_API", "GraphQLApi")
        query = """query FeatureReadiness {
            facilities: silver_facilities(first: 20) { items { facility_id } }
            equipment: silver_equipments(first: 100) { items { equipment_id } }
            instruments: silver_instruments(first: 500) { items { instrument_id } }
        }"""
        graphql_counts = {}
        for attempt in range(12):
            response = self.fabric.request(
                "POST", f"{self.base}/graphqlapis/{graphql['id']}/graphql", json={"query": query},
            )
            check_response(response, "Read feature STID data", {200})
            payload = response.json()
            graphql_counts = {key: len((payload.get("data") or {}).get(key, {}).get("items") or [])
                              for key in ("facilities", "equipment", "instruments")}
            if not payload.get("errors") and all(graphql_counts.values()):
                break
            print("  STID GraphQL publication is not ready; waiting.", flush=True)
            time.sleep(10)
        else:
            raise FeatureWorkspaceError("STID GraphQL did not return the seeded facility/asset/signal data.")
        name = f"RTI_Demo_Eventhouse_{self.config.env_suffix}"
        eventhouse = self._item(name, "Eventhouse")
        response = self.fabric.request("GET", f"{self.base}/eventhouses/{eventhouse['id']}")
        check_response(response, "Read feature Eventhouse endpoint", {200})
        cluster = response.json().get("properties", {}).get("queryServiceUri", "").rstrip("/")
        parsed = urlparse(cluster)
        if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".kusto.fabric.microsoft.com"):
            raise FeatureWorkspaceError("Unexpected feature Eventhouse query endpoint.")
        token = self.fabric._credential.get_token(f"{cluster}/.default").token
        rows = []
        for attempt in range(12):
            response = requests.post(f"{cluster}/v1/rest/query", headers={
                "Authorization": f"Bearer {token}", "Content-Type": "application/json",
            }, json={"db": name, "csl": "OPCUAEvents | summarize count(), max(event_time)"}, timeout=60)
            check_response(response, "Read feature telemetry", {200})
            tables = response.json().get("Tables") or []
            rows = tables[0].get("Rows", []) if tables else []
            if rows and rows[0][0] > 0:
                break
            print("  Waiting for the feature telemetry stream to become queryable.", flush=True)
            time.sleep(10)
        else:
            raise FeatureWorkspaceError("The feature Eventhouse has no queryable demo telemetry.")
        self.state["verification"] = {"stid_counts": graphql_counts, "telemetry_count": rows[0][0],
                                      "latest_telemetry_time": rows[0][1]}
        print(f"  Verified STID {graphql_counts}; telemetry rows: {rows[0][0]}.", flush=True)
