"""Deterministic Agent Framework orchestration of bounded Foundry specialists."""

import asyncio
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable, Iterable
from hashlib import sha256
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, Protocol, cast
from uuid import UUID

import httpx
from agent_framework import (
    Agent, AgentFrameworkException, AgentResponse, ChatOptions, Executor, FunctionInvocationContext, FunctionTool,
    MiddlewareTermination, WorkflowBuilder, WorkflowContext, WorkflowViz, function_middleware, handler,
)
from agent_framework.foundry import FoundryAgent, FoundryChatClient
from azure.ai.projects.aio import AIProjectClient
from azure.core.credentials_async import AsyncTokenCredential
from filelock import FileLock
from jsonschema import Draft202012Validator
from openai import APIStatusError, APITimeoutError
from pydantic import AwareDatetime, Field, model_validator

from .contracts import Contract, EvidenceReference, ResolvedScope, SourceIdentity, WorkOrderDraft, utc_now
from .live_sources import Bridge, SourceFailure
from .foundry_rca import verified_project_endpoint
from .presentation import AnswerColumn, AnswerRow, AnswerTable, ClientPresentation
from .workflow import RequiredCheckpointStorage

logger = logging.getLogger(__name__)
Role = Literal["supervisor", "qa", "rca", "work-order", "fabric-iq"]
NativeSource = Literal["data-agent"]
FOUNDRY_SERVICE_CALL_TIMEOUT_SECONDS = 120.0
FOUNDRY_NATIVE_CALL_TIMEOUT_SECONDS = 300.0
# The Supervisor is this hosted orchestrator's deterministic control plane, not a Foundry prompt agent. Its pinned
# identity is the control-plane contract revision; bump it when the orchestration contract changes incompatibly.
CONTROL_PLANE_AGENT = "hydro-orchestrator"
CONTROL_PLANE_VERSION = "1"
MAX_CHAT_RUN_DURATION = timedelta(minutes=10)
ROLE_SKILLS: dict[Role, tuple[str, ...]] = {
    "supervisor": ("source-reconciliation", "grounded-presentation"),
    "qa": ("condition-triage", "source-reconciliation", "maintenance-planning"),
    "rca": ("root-cause-evidence", "maintenance-planning"),
    "work-order": ("maintenance-planning", "work-order-review"),
    "fabric-iq": ("source-reconciliation",),
}


class BoundedAIProjectClient(AIProjectClient):
    def __init__(
        self,
        endpoint: str,
        credential: AsyncTokenCredential,
        *,
        request_timeout: float = FOUNDRY_SERVICE_CALL_TIMEOUT_SECONDS,
        allow_preview: bool = False,
        **kwargs: Any,
    ):
        self.request_timeout = request_timeout
        super().__init__(endpoint, credential, allow_preview=allow_preview, **kwargs)

    def get_openai_client(self, *, agent_name: str | None = None, **kwargs: object):  # type: ignore[no-untyped-def]
        kwargs.setdefault("max_retries", 0)
        kwargs.setdefault("timeout", self.request_timeout)
        return super().get_openai_client(agent_name=agent_name, **kwargs)


def _is_native_refusal_text(value: str) -> bool:
    normalized = " ".join(value.casefold().split())
    return any(marker in normalized for marker in (
        "cannot assist with that request",
        "can't assist with that request",
        "unable to assist with that request",
        "request was blocked by content moderation",
        "response was filtered due to the prompt triggering",
    ))


def _has_native_domain_evidence(value: object) -> bool:
    if isinstance(value, dict):
        if value and set(value).issubset({"id", "content", "title", "url"}):
            content = str(value.get("content", "")).strip()
            return bool(content) and not _is_native_refusal_text(content)
        metadata_keys = {"id", "title", "url", "type", "status", "name", "server_label"}
        return any(
            _has_native_domain_evidence(item)
            for key, item in value.items()
            if key not in metadata_keys and not key.startswith("@")
        )
    if isinstance(value, list):
        return any(_has_native_domain_evidence(item) for item in value)
    return value is not None and (
        not isinstance(value, str)
        or bool(value.strip()) and not _is_native_refusal_text(value)
    )


def _validated_native_result(output: object) -> dict[str, object]:
    if not isinstance(output, str) or not output.strip():
        raise SourceFailure("Native execution returned no inspectable source output.")
    try:
        payload: object = json.loads(output)
    except json.JSONDecodeError:
        payload = output

    def contains_refusal(value: object) -> bool:
        if isinstance(value, str):
            return _is_native_refusal_text(value)
        if isinstance(value, dict):
            return any(contains_refusal(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_refusal(item) for item in value)
        return False

    if contains_refusal(payload) and not _has_native_domain_evidence(payload):
        raise SourceFailure(
            "Native source refusal: the selected source returned moderation/refusal text instead of domain evidence."
        )
    native_result: dict[str, object] = (
        dict(payload) if isinstance(payload, dict)
        else {"native_output": payload}
    )
    native_result.update(_native_markdown_tables_from_payload(native_result))
    if not _has_native_domain_evidence(native_result):
        raise SourceFailure("Native execution returned only connection metadata without domain evidence.")
    return native_result


def _native_equipment_ids(value: object) -> set[str]:
    resolved: set[str] = set()

    def add(candidate: object) -> None:
        if isinstance(candidate, str):
            identifier = candidate.strip().strip("`")
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", identifier):
                resolved.add(identifier)

    def visit(item: object) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if re.sub(r"[^a-z]", "", key.lower()) == "equipmentid":
                    add(child)
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str):
            lines = item.splitlines()
            for line_index, line in enumerate(lines[:-2]):
                if not line.strip().startswith("|") or not line.strip().endswith("|"):
                    continue
                headers = [cell.strip() for cell in line.strip().strip("|").split("|")]
                equipment_columns = [
                    index for index, header in enumerate(headers)
                    if re.sub(r"[^a-z]", "", header.lower()) in {
                        "equipmentid", "equipmentcanonicalid", "canonicalequipmentid",
                    }
                ]
                separator = lines[line_index + 1].strip()
                if not equipment_columns or not re.fullmatch(r"\|?[\s:|-]+\|?", separator):
                    continue
                for row in lines[line_index + 2:]:
                    if not row.strip().startswith("|") or not row.strip().endswith("|"):
                        break
                    cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
                    for column in equipment_columns:
                        if column < len(cells):
                            add(cells[column].strip("* "))
            for match in re.finditer(
                r"(?im)^\|\s*equipment[\s_-]*id\s*\|\s*`?([^`|\s]+)`?\s*\|",
                item,
            ):
                add(match.group(1))
            for line in item.splitlines():
                field = re.match(r"^\s*[-*]\s*(.+?):\s*(.+?)\s*$", line)
                if field is None:
                    continue
                label = re.sub(r"[^a-z]", "", field.group(1).lower())
                if label not in {"equipmentid", "resolvedequipmentidentity"}:
                    continue
                code_value = re.search(r"`([^`]+)`", field.group(2))
                add(code_value.group(1) if code_value else field.group(2).strip("* "))

    visit(value)
    return resolved


def _native_markdown_tables(text: str) -> dict[str, list[dict[str, str]]]:
    lines = text.splitlines()
    tables: dict[str, list[dict[str, str]]] = {}
    index = 0
    while index + 1 < len(lines) and len(tables) < 20:
        header_line = lines[index].strip()
        separator_line = lines[index + 1].strip()
        if "|" not in header_line or "|" not in separator_line:
            index += 1
            continue
        headers = [cell.strip() for cell in re.split(r"(?<!\\)\|", header_line.strip("|"))]
        separators = [cell.strip() for cell in re.split(r"(?<!\\)\|", separator_line.strip("|"))]
        if (not 1 <= len(headers) <= 40 or len(headers) != len(separators)
                or any(not header for header in headers) or len(set(headers)) != len(headers)
                or any(re.fullmatch(r":?-{3,}:?", cell) is None for cell in separators)):
            index += 1
            continue
        rows: list[dict[str, str]] = []
        row_index = index + 2
        while row_index < len(lines) and len(rows) < 500:
            row_line = lines[row_index].strip()
            if "|" not in row_line:
                break
            cells = [
                cell.strip().replace(r"\|", "|")
                for cell in re.split(r"(?<!\\)\|", row_line.strip("|"))
            ]
            if len(cells) != len(headers):
                break
            rows.append(dict(zip(headers, cells, strict=True)))
            row_index += 1
        if rows:
            title = next((
                re.sub(r"^[#\s]+", "", candidate.strip())
                for candidate in reversed(lines[:index])
                if candidate.strip()
            ), f"table_{len(tables) + 1}")
            slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:60] or "table"
            key = f"native_{slug}"
            suffix = 2
            while key in tables:
                key = f"native_{slug}_{suffix}"
                suffix += 1
            tables[key] = rows
        index = max(index + 1, row_index)
    return tables


def _native_count_chart_metadata(
    result: dict[str, object],
) -> tuple[dict[str, str], dict[str, object] | None]:
    identifier_keys = {
        "equipment", "equipmentid", "facility", "facilityid", "facilityname", "station", "stationid",
    }
    count_keys = {
        "count", "opencount", "openorders", "openworkorders", "ordercount", "orders",
        "workordercount", "workorders",
    }
    for table_field, value in result.items():
        if not table_field.startswith("native_") or not isinstance(value, list) or not value:
            continue
        rows = [row for row in value if isinstance(row, dict)]
        if len(rows) != len(value) or not rows:
            continue
        keys = list(rows[0])
        if any(list(row) != keys for row in rows):
            continue
        x_keys = [key for key in keys if re.sub(r"[^a-z]", "", key.lower()) in identifier_keys]
        y_keys = [key for key in keys if re.sub(r"[^a-z]", "", key.lower()) in count_keys]
        if len(x_keys) != 1 or len(y_keys) != 1:
            continue
        if not all(
            isinstance(row[y_keys[0]], (int, float))
            or isinstance(row[y_keys[0]], str) and re.fullmatch(r"\d+", row[y_keys[0]].strip())
            for row in rows
        ):
            continue
        return (
            {y_keys[0]: "count"},
            {
                "table_field": table_field,
                "chart": {"kind": "bar", "x_key": x_keys[0], "y_keys": [y_keys[0]]},
            },
        )
    return {}, None


def updated_equipment_selection(
    current: set[str], grounded: set[str], *, requires_selection: bool,
) -> set[str]:
    if requires_selection:
        return current.union(grounded)
    return set(grounded) if grounded else set(current)


def initial_equipment_selection(request: "ChatRequest") -> set[str]:
    if request.resolved_scope is None:
        return set()
    return {
        match.canonical_id
        for match in request.resolved_scope.matches
        if match.kind == "equipment"
    }


def requests_unsent_message(question: str) -> bool:
    normalized = re.sub(r"\s+", " ", question.lower())
    return "unsent" in normalized and any(
        term in normalized for term in ("notification", "email", "message", "draft")
    )


def requests_new_work_order_proposal(question: str) -> bool:
    normalized = re.sub(r"\s+", " ", question.lower())
    for clause in re.split(r"[.!?;]+", normalized):
        if not any(term in clause for term in ("work order", "work-order", " wo ", "draft", "propose")):
            continue
        if re.search(r"(?:^|please\s+)draft\b", clause.strip()):
            return True
        for match in re.finditer(
            r"\b(?:prepare|create|stage|propose|open|raise|generate|make|need|want|request)\b",
            clause,
        ):
            prefix = clause[max(0, match.start() - 32):match.start()]
            if not re.search(r"\b(?:do not|don't|never|no)\b(?:\W+\w+){0,3}\W*$", prefix):
                return True
    return False


def safe_exception_signature(error: BaseException) -> str:
    signatures: list[str] = []
    current: BaseException | None = error
    while current is not None and len(signatures) < 4:
        fields = [type(current).__name__]
        for name in ("status_code", "code", "type", "param"):
            value = getattr(current, name, None)
            if isinstance(value, int) or (
                isinstance(value, str) and 0 < len(value) <= 100
                and re.fullmatch(r"[A-Za-z0-9_.:/ -]+", value)
            ):
                fields.append(f"{name}={value}")
        signatures.append(",".join(fields))
        cause = current.__cause__ or current.__context__
        current = cause if isinstance(cause, BaseException) and cause is not current else None
    return " <- ".join(signatures)


def exception_chain_contains(error: BaseException, expected: type[BaseException]) -> bool:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, expected):
            return True
        seen.add(id(current))
        cause = current.__cause__ or current.__context__
        current = cause if isinstance(cause, BaseException) and cause is not current else None
    return False


def transient_native_failure(error: BaseException) -> str | None:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, APIStatusError) and 500 <= current.status_code <= 599:
            return f"http_{current.status_code}"
        if (
            isinstance(current, APIStatusError)
            and current.status_code == 400
            and getattr(current, "code", None) == "tool_user_error"
        ):
            detail = str(current)
            if (
                "Remote MCP server error while getting LRO task" in detail
                and "completed without a reply to request with ID" in detail
            ):
                return "missing_mcp_reply"
        seen.add(id(current))
        cause = current.__cause__ or current.__context__
        current = cause if isinstance(cause, BaseException) and cause is not current else None
    return None


def operation_skill_guidance(role: Role) -> tuple[dict[str, str], ...]:
    root = Path(__file__).resolve().parents[2] / "skills"
    paths = [root / name for name in ROLE_SKILLS[role]]
    if any(not (path / "SKILL.md").is_file() for path in paths):
        raise SourceFailure("Required trusted operation skills are missing from the runtime package.")
    guidance = tuple({
        "name": path.name,
        "instructions": (path / "SKILL.md").read_text(encoding="utf-8"),
    } for path in paths)
    if any(not item["instructions"].strip() or len(item["instructions"]) > 30000 for item in guidance):
        raise SourceFailure("Trusted operation skill guidance is empty or exceeds its bounded size.")
    return guidance


class ToolInputError(ValueError):
    """Invalid model arguments rejected before any backend source execution."""


class ToolSpecification(Contract):
    name: str
    parameters: dict[str, object]


class SourceCatalog(Contract):
    tools: tuple[ToolSpecification, ...]
    context: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def distinct_tools(self) -> "SourceCatalog":
        if len({tool.name for tool in self.tools}) != len(self.tools):
            raise ValueError("Source tool catalog must contain distinct names.")
        for tool in self.tools:
            Draft202012Validator.check_schema(tool.parameters)
            if tool.parameters.get("type") != "object" or tool.parameters.get("additionalProperties") is not False:
                raise ValueError("Backend tool schemas must explicitly reject unknown argument fields.")
        return self


class DomainDatasetDefinition(Contract):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    source: Literal["lakehouse", "sql", "eventhouse", "derived", "native"]
    entity: str = Field(min_length=1, max_length=120)
    identity_fields: tuple[str, ...] = Field(alias="identityFields", min_length=1, max_length=4)
    presentations: tuple[str, ...] = Field(min_length=1, max_length=16)
    evidence_scopes: tuple[Literal["population", "focus"], ...] = Field(
        alias="evidenceScopes", min_length=1, max_length=2,
    )
    evidence_authorities: tuple[Literal["direct", "native"], ...] = Field(
        alias="evidenceAuthorities", min_length=1, max_length=2,
    )
    limitations: tuple[str, ...] = Field(default=(), max_length=8)


def governed_datasets(context: dict[str, object]) -> dict[str, DomainDatasetDefinition]:
    if context.get("domain_contract_version") != 3:
        raise SourceFailure("The shared domain contract version is missing or unsupported.")
    raw = context.get("datasets")
    if not isinstance(raw, list) or not raw:
        raise SourceFailure("The shared domain contract has no governed datasets.")
    values = tuple(DomainDatasetDefinition.model_validate(item) for item in raw)
    if len({item.key for item in values}) != len(values):
        raise SourceFailure("The shared domain contract contains duplicate dataset identities.")
    return {item.key: item for item in values}


class HistoricalContext(Contract):
    run_id: UUID
    source: SourceIdentity
    question: str = Field(min_length=1, max_length=8000)
    rendered_answer: str = Field(max_length=16000)
    requested_at: AwareDatetime
    proposal_ids: tuple[UUID, ...] = Field(default=(), max_length=8)


class EquipmentSelection(Contract):
    metric: Literal["signal_quality_count", "open_work_order_count"]
    quality: Literal["BAD", "UNCERTAIN"] | None = None
    tie_break: Literal["equipment_id_asc"]

    @model_validator(mode="after")
    def quality_matches_metric(self) -> "EquipmentSelection":
        if (self.metric == "signal_quality_count") != (self.quality is not None):
            raise ValueError("Signal-quality selection requires exactly one quality label.")
        return self


class EvidenceRequirement(Contract):
    evidence: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}_(?:population|focus)_(?:direct|native)$")
    purpose: Literal["facts", "identity", "coverage", "investigation", "verification"]

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_fields(cls, value: object) -> object:
        if isinstance(value, dict) and "evidence" not in value:
            kind, scope, authority = value.get("kind"), value.get("scope"), value.get("authority")
            if all(isinstance(item, str) for item in (kind, scope, authority)):
                return {"evidence": f"{kind}_{scope}_{authority}", "purpose": value.get("purpose")}
        return value

    @property
    def authority(self) -> Literal["direct", "native"]:
        return "native" if self.evidence.endswith("_native") else "direct"

    @property
    def scope(self) -> Literal["population", "focus"]:
        return "population" if self.evidence.endswith(("_population_direct", "_population_native")) else "focus"

    @property
    def kind(self) -> str:
        return self.evidence.removesuffix(f"_{self.scope}_{self.authority}")


class AnswerViewContract(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    title: str = Field(min_length=1, max_length=120)
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    scope: Literal["population", "focus"]
    presentation: Literal[
        "direct_records_table", "direct_records_bar", "direct_records_line", "direct_records_scatter",
        "direct_grouped_by_equipment_table", "direct_grouped_by_equipment_bar",
        "direct_grouped_by_equipment_line", "direct_grouped_by_equipment_scatter",
        "direct_grouped_by_facility_table", "direct_grouped_by_facility_bar",
        "direct_grouped_by_facility_line", "direct_grouped_by_facility_scatter",
        "native_records_table", "native_records_bar", "native_records_line", "native_records_scatter",
        "native_grouped_by_equipment_table", "native_grouped_by_equipment_bar",
        "native_grouped_by_equipment_line", "native_grouped_by_equipment_scatter",
        "native_grouped_by_facility_table", "native_grouped_by_facility_bar",
        "native_grouped_by_facility_line", "native_grouped_by_facility_scatter",
        "reconciliation_table",
    ]

    @property
    def authority(self) -> Literal["direct", "native", "reconciled"]:
        if self.presentation == "reconciliation_table":
            return "reconciled"
        return "native" if self.presentation.startswith("native_") else "direct"

    @property
    def mode(self) -> Literal["records", "grouped", "reconciliation"]:
        if self.presentation == "reconciliation_table":
            return "reconciliation"
        return "grouped" if "_grouped_by_" in self.presentation else "records"

    @property
    def group_by(self) -> Literal["equipment_id", "facility_id"] | None:
        if "_grouped_by_equipment_" in self.presentation:
            return "equipment_id"
        if "_grouped_by_facility_" in self.presentation:
            return "facility_id"
        return None

    @property
    def chart(self) -> Literal["bar", "line", "scatter"] | None:
        suffix = self.presentation.rsplit("_", 1)[-1]
        return cast(Literal["bar", "line", "scatter"], suffix) if suffix in (
            "bar", "line", "scatter",
        ) else None


class OperatorAnswerContract(Contract):
    version: Literal[3]
    requirements: tuple[EvidenceRequirement, ...] = Field(min_length=1, max_length=16)
    views: tuple[AnswerViewContract, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def views_are_supported(self) -> "OperatorAnswerContract":
        if len({view.id for view in self.views}) != len(self.views):
            raise ValueError("Answer view identifiers must be distinct.")
        declared = {
            (item.kind, item.scope, item.authority)
            for item in self.requirements
        }
        for view in self.views:
            if view.authority == "reconciled":
                authorities = {
                    item.authority for item in self.requirements if item.scope == view.scope
                }
                if authorities != {"direct", "native"}:
                    raise ValueError(
                        "Reconciliation views require explicit direct and native evidence requirements."
                    )
            elif (view.kind, view.scope, view.authority) not in declared:
                raise ValueError("Every answer view must have a matching evidence requirement.")
        return self


class ChatRequest(Contract):
    run_id: UUID
    source: SourceIdentity
    question: str = Field(min_length=1, max_length=8000)
    requested_at: AwareDatetime = Field(default_factory=utc_now)
    deadline: AwareDatetime
    charts_requested: bool = Field(default=False, strict=True)
    native_sources: tuple[NativeSource, ...] = ()
    proposal_priority: Literal["Low", "Medium", "High", "Critical"] = "Medium"
    equipment_selection: EquipmentSelection | None = None
    required_evidence_kinds: tuple[str, ...] = ()
    permitted_evidence_kinds: tuple[str, ...] = ()
    required_specialists: tuple[Literal["qa", "rca", "work-order"], ...] = ()
    required_specialist_steps: dict[Literal["qa", "rca", "work-order"], int] = Field(default_factory=dict)
    resolved_scope: ResolvedScope | None = None
    previous_run_id: UUID | None = None
    historical_context: HistoricalContext | None = None
    # Receipts from the previous run that this follow-up reuses instead of re-reading. They are selected and
    # validated by the backend (same principal, same source, recent, never a staged draft), never by the client.
    carried_from: UUID | None = None
    carried_evidence_ids: tuple[str, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def bounded_credential_lease(self) -> "ChatRequest":
        if bool(self.carried_evidence_ids) != (self.carried_from is not None) or (
            self.carried_from is not None and self.carried_from != self.previous_run_id
        ):
            raise ValueError("Carried evidence must come from, and only from, the preceding run.")
        if len(self.carried_evidence_ids) != len(set(self.carried_evidence_ids)):
            raise ValueError("Carried evidence identities must be distinct.")
        if not self.requested_at < self.deadline <= self.requested_at + MAX_CHAT_RUN_DURATION:
            raise ValueError("Credential lease must be within ten minutes of its persisted request clock.")
        if len(self.native_sources) != len(set(self.native_sources)):
            raise ValueError("Requested native sources must be distinct.")
        if len(self.required_specialists) != len(set(self.required_specialists)):
            raise ValueError("Required specialists must be distinct.")
        if (
            len(self.required_evidence_kinds) != len(set(self.required_evidence_kinds))
            or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", kind) for kind in self.required_evidence_kinds)
        ):
            raise ValueError("Required evidence kinds must be distinct governed identifiers.")
        if (
            len(self.permitted_evidence_kinds) != len(set(self.permitted_evidence_kinds))
            or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", kind) for kind in self.permitted_evidence_kinds)
            or not set(self.required_evidence_kinds).issubset(self.permitted_evidence_kinds)
        ):
            raise ValueError("Permitted evidence kinds must be distinct and include every required kind.")
        if (any(role not in self.required_specialists for role in self.required_specialist_steps)
                or any(count < 1 or count > 8 for count in self.required_specialist_steps.values())):
            raise ValueError("Required specialist step counts must be bounded and match required specialists.")
        if self.resolved_scope is not None and (
            self.resolved_scope.source != self.source
            or not self.requested_at <= self.resolved_scope.read_completed_at <= self.deadline
        ):
            raise ValueError("Resolved semantic scope must match this source and invocation clock.")
        if self.historical_context is not None and (
            self.historical_context.run_id != self.previous_run_id
            or self.historical_context.source != self.source
            or self.historical_context.run_id == self.run_id
            or self.historical_context.requested_at > self.requested_at
        ):
            raise ValueError("Historical context must match the same-source preceding run.")
        return self


def canonical_source_arguments(request: ChatRequest, arguments: dict[str, object]) -> dict[str, object]:
    if request.resolved_scope is None:
        return arguments

    def normalized(value: object) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value).lower())

    indexes: dict[str, dict[str, str]] = {
        "equipment_id": {},
        "tag": {},
        "facility_id": {},
        "facility_name": {},
    }

    def register(index: str, alias: str | None, canonical: str) -> None:
        if not alias:
            return
        key = normalized(alias)
        previous = indexes[index].get(key)
        if previous is not None and previous != canonical:
            raise SourceFailure("Resolved semantic scope contains an ambiguous canonical alias.")
        indexes[index][key] = canonical

    for match in request.resolved_scope.matches:
        if match.kind == "equipment":
            for alias in (match.mention, match.label, match.canonical_id):
                register("equipment_id", alias, match.canonical_id)
                register("tag", alias, match.label)
        else:
            for alias in (match.mention, match.label, match.canonical_id):
                register("facility_id", alias, match.canonical_id)
                register("facility_name", alias, match.label)

    field_indexes = {
        "equipment_id": "equipment_id",
        "equipmentId": "equipment_id",
        "equipment_ids": "equipment_id",
        "facility_id": "facility_id",
        "facilityId": "facility_id",
        "facility_ids": "facility_id",
        "facility_name": "facility_name",
        "tag": "tag",
    }

    def resolve(index: str, value: object) -> object:
        if isinstance(value, list):
            return [resolve(index, item) for item in value]
        if not isinstance(value, str):
            return value
        return indexes[index].get(normalized(value), value)

    canonical = json.loads(_encoded(arguments))
    for field, index in field_indexes.items():
        if field in canonical:
            canonical[field] = resolve(index, canonical[field])
    where = canonical.get("where")
    if isinstance(where, list):
        for condition in where:
            if not isinstance(condition, dict) or condition.get("op") not in ("eq", "in"):
                continue
            column = condition.get("column")
            index = field_indexes.get(column) if isinstance(column, str) else None
            if index is not None and "value" in condition:
                condition["value"] = resolve(index, condition["value"])
    return canonical


def normalize_filter_conjunctions(arguments: dict[str, object]) -> dict[str, object]:
    where = arguments.get("where")
    if isinstance(where, dict):
        return {**arguments, "where": [where]}
    if not isinstance(where, list) or not where:
        return arguments

    conditions: list[dict[object, object]] = []
    changed = False

    def collect(items: list[object]) -> bool:
        nonlocal changed
        for item in items:
            if isinstance(item, dict):
                conditions.append(item)
            elif isinstance(item, str) and item.strip().lower() == "and":
                changed = True
            elif isinstance(item, list) and item:
                changed = True
                if not collect(item):
                    return False
            else:
                return False
        return True

    if not collect(where) or not changed:
        return arguments
    return {**arguments, "where": conditions}


class NativeBinding(Contract):
    source: SourceIdentity
    connections: dict[NativeSource, str]


class EvidenceDataset(Contract):
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    field: str = Field(min_length=1, max_length=120)
    authority: Literal["direct", "native", "reconciled"]
    scope: Literal["population", "focus"]
    complete: bool = Field(strict=True)
    identity_fields: tuple[str, ...] = Field(min_length=1, max_length=4)
    group_by: Literal["equipment_id", "facility_id"] | None = None
    role: Literal["primary", "support", "audit"] = "primary"
    limitations: tuple[str, ...] = Field(default=(), max_length=8)


class ToolEvidence(Contract):
    id: str = Field(min_length=1, max_length=200)
    source: SourceIdentity
    tool: str
    arguments: dict[str, object]
    completed_at: AwareDatetime
    result: dict[str, object]
    limitations: tuple[str, ...] = ()
    column_units: dict[str, str] = Field(default_factory=dict)
    row_identities: dict[str, dict[Literal["equipment_id", "equipmentId", "opcua_node_id", "opcuaNodeId"], str]] = Field(default_factory=dict)
    resolved_equipment_ids: tuple[str, ...] = ()
    work_coverage_equipment_ids: tuple[str, ...] = ()
    datasets: tuple[EvidenceDataset, ...] = ()
    production_write_executed: Literal[False] = False

    def receipt(self) -> dict[str, object]:
        return {"id": self.id, "tool": self.tool, "completedAt": self.completed_at.isoformat(),
                "result": self.result}


SourceRow = tuple[ToolEvidence, str, int, dict[str, object]]


class BackendTools(Protocol):
    async def catalog(self, request: ChatRequest) -> dict[str, object]:
        """Return the source/tool schemas permitted by the verified backend configuration."""
        ...

    async def execute(
        self, name: str, arguments: dict[str, object], request: ChatRequest,
    ) -> ToolEvidence:
        """Authorize and validate a source read or an in-memory proposal; never write SQL."""
        ...


class AnswerCell(Contract):
    key: str
    source: EvidenceReference


class PlannedRow(Contract):
    cells: tuple[AnswerCell, ...] = Field(min_length=1, max_length=40)


class PlannedTable(Contract):
    id: str
    title: str
    columns: tuple[AnswerColumn, ...] = Field(min_length=1, max_length=40)
    rows: tuple[PlannedRow, ...] = Field(max_length=500)


class PlannedChart(Contract):
    table_id: str
    kind: Literal["line", "bar", "scatter"]
    x_key: str
    y_keys: tuple[str, ...] = Field(min_length=1, max_length=4)


class AnswerPlan(Contract):
    summary: str = Field(max_length=1200)
    tables: tuple[PlannedTable, ...] = Field(max_length=12)
    charts: tuple[PlannedChart, ...] = Field(max_length=4)
    limitations: tuple[str, ...] = Field(max_length=20)


class NarrativeProjection(Contract):
    summary: str = Field(min_length=1, max_length=800)
    chart_indexes: tuple[int, ...] = Field(max_length=4)


AnalysisAction = Literal["create_work_order", "covered_by_open_work", "monitor", "no_action", "needs_clarification"]
ANALYSIS_ID_PATTERN = re.compile(r"\b(?:EQUIP_RTI_[A-Z0-9_]+|FACILITY_RTI_\d+|INST_[A-Z0-9_]+|WO-\d+|T\d{3})\b")
ANALYSIS_DECIMAL_PATTERN = re.compile(r"(?<![\w.])-?\d+\.\d+(?!\w|\.\d)")
ANALYSIS_TIMEOUT_SECONDS = 90.0
ANALYSIS_ATTEMPTS = 3
ANALYSIS_MAX_ROWS_PER_TABLE = 80
CLOSED_WORK_STATUSES = frozenset({"completed", "cancelled", "canceled", "closed"})


class AnalysisRecommendation(Contract):
    action: AnalysisAction
    equipment_id: str | None = Field(default=None, max_length=80)
    priority: Literal["Low", "Medium", "High", "Critical"] | None = None
    title: str = Field(min_length=1, max_length=200)
    justification: str = Field(min_length=1, max_length=700)


class AnalysisReport(Contract):
    answer: str = Field(min_length=1, max_length=800)
    findings: tuple[str, ...] = Field(max_length=8)
    reasoning: tuple[str, ...] = Field(max_length=6)
    recommendations: tuple[AnalysisRecommendation, ...] = Field(max_length=8)
    caveats: tuple[str, ...] = Field(max_length=5)

    @model_validator(mode="after")
    def bounded_text(self) -> "AnalysisReport":
        if any(len(item) > 700 for item in (*self.findings, *self.reasoning, *self.caveats)):
            raise ValueError("Analysis statements must stay concise.")
        for item in self.recommendations:
            if item.action == "create_work_order" and (not item.equipment_id or item.priority is None):
                raise ValueError("A work-order recommendation requires an equipment identity and a priority.")
        return self


ANALYSIS_SCHEMA: dict[str, object] = {
    "type": "object", "additionalProperties": False,
    "required": ["answer", "findings", "reasoning", "recommendations", "caveats"],
    "properties": {
        "answer": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "array", "items": {"type": "string"}},
        "recommendations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["action", "equipment_id", "priority", "title", "justification"],
            "properties": {
                "action": {"type": "string", "enum": [
                    "create_work_order", "covered_by_open_work", "monitor", "no_action", "needs_clarification"]},
                "equipment_id": {"type": ["string", "null"]},
                "priority": {"type": ["string", "null"], "enum": ["Low", "Medium", "High", "Critical", None]},
                "title": {"type": "string"},
                "justification": {"type": "string"},
            },
        }},
        "caveats": {"type": "array", "items": {"type": "string"}},
    },
}

ANALYST_INSTRUCTIONS = """You are the Hydro Operations Supervisor writing the operator's final briefing for a hydropower fleet.
The JSON input holds the operator's question, the verified datasets already retrieved for it, the open work orders per equipment, specialist notes, any staged draft and source limitations. It is your only source of facts.

Write like a senior maintenance planner briefing a colleague: direct, specific, reasoned.
- "answer": one to three sentences that answer the operator's actual question first. Name the equipment, work-order numbers and key values. State plainly when the answer is "none" or "nothing needs doing".
- "findings": the concrete facts that matter, each citing equipment IDs, signal names, work-order numbers and source values (with units and timestamps where supplied). Prefer a short ranked list over restating whole tables.
- "reasoning": explain HOW you got from the data to the answer. For a question about which work orders to open or create, compare every affected equipment (BAD signal quality, highest temperatures, whatever the question targets) with open_work_by_equipment. Say which equipment is already covered and by which work order, judging from its title, status and priority whether it plausibly addresses the concern, and which has no cover. Say why a gap matters now.
- "recommendations": one entry per equipment or decision. Use create_work_order only for a verified gap with no plausibly covering open work (priority required, equipment_id required). Use covered_by_open_work when an open work order already addresses it and cite that number in the justification. Use monitor when evidence is thin, stale or only a single weak signal. Use no_action when nothing is wrong. Use needs_clarification when identity or scope is ambiguous. Give each a short actionable title (for a work order a ready-to-use inspection title) and a justification that cites the evidence. Set priority relative to the evidence (multiple affected signals, hottest-ranked, no cover, stale data) and say that the priority is a suggestion for the operator to adjust.
- If a draft is staged (staged_drafts), say which equipment it targets, why it is justified, and tell the operator to review and approve it in the card below. Never claim it was created.
- carried_evidence lists datasets that were read earlier in the conversation and reused, not re-read. When it is non-empty, say in one short clause which data was reused and when it was read (now_utc is the current time), so the operator can judge how current it is. Draft-coverage checks for staged drafts are fresh.
- "caveats": only real limits that change how far the operator can trust the conclusion (stale readings, truncated rows, missing sources, specialist limitations). Do not pad.

Hard rules:
- Use only identifiers, numbers, timestamps and statuses that appear in the input. Quote values exactly; never compute averages, sums or new numbers except simple counts of listed items. Never invent equipment, signals, work-order numbers, thresholds, limits, baselines, probabilities or confidence figures.
- BAD or UNCERTAIN is a signal-quality state, not proof of a physical fault. Recommend verifying or inspecting the signal or instrument and its equipment; never state a cause, a failure or a repair. Cause remains undetermined unless the specialist notes establish otherwise.
- Say if a table has more rows than shown. Treat specialist prose as unverified notes; only the datasets are verified facts.
- You write advice only. Nothing is created or approved by you, and production writes are never executed.
- Be concise: no filler, no restating the question, no markdown, no tables (they are shown separately)."""


class SpecialistResult(Contract):
    role: Role
    agent_name: str
    version: str
    response_id: str
    duration_ms: float
    model_round_count: int | None = Field(default=None, ge=1)
    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    text: str
    report: dict[str, object] | None = None


class ChatAnswer(Contract):
    run_id: UUID
    source: SourceIdentity
    requested_at: AwareDatetime
    source_read_times: tuple[AwareDatetime, ...]
    summary: str
    tables: tuple[AnswerTable, ...]
    charts: tuple[PlannedChart, ...]
    limitations: tuple[str, ...]
    cell_sources: tuple[PlannedTable, ...]
    specialists: tuple[SpecialistResult, ...]
    audit_url: str
    proposals: tuple[WorkOrderDraft, ...] = ()
    proposal_digests: dict[str, str] = Field(default_factory=dict)
    production_write_executed: Literal[False] = False
    presentation: ClientPresentation | None = None
    analysis: AnalysisReport | None = None


class ProjectionReceipt(Contract):
    response_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    duration_ms: float = Field(ge=0)
    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class PreparedAnswer(Contract):
    answer: ChatAnswer
    evidence: tuple[ToolEvidence, ...]
    supervisor_response_id: str
    supervisor_version: str
    supervisor_duration_ms: float
    supervisor_model_round_count: int | None = Field(default=None, ge=1)
    supervisor_output: AnswerPlan
    answer_projection: ProjectionReceipt | None = None
    request_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class SupervisorTurn(Contract):
    text: str
    response_id: str = Field(min_length=1)
    version: str
    duration_ms: float
    model_round_count: int | None = Field(default=None, ge=1)
    evidence: tuple[ToolEvidence, ...]
    specialists: tuple[SpecialistResult, ...]
    plan: dict[str, object] | None = None
    request_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    verified: bool = False
    prepared: PreparedAnswer | None = None


class AgentVersion(Contract):
    name: str
    version: str = Field(pattern=r"^[1-9][0-9]*$")
    model: str = Field(min_length=1, max_length=200)
    tools: tuple[dict[str, object], ...]


class Delegation(Contract):
    specialist: Literal["qa", "rca", "work-order", "fabric-iq"]
    question: str
    reason: str | None = None
    native_source: NativeSource | None = Field(default=None, alias="nativeSource")
    requires_selection: bool = Field(default=False, alias="requiresSelection")
    requires_verified_issue: bool = Field(default=False, alias="requiresVerifiedIssue")

    @model_validator(mode="after")
    def verified_issue_only_gates_work(self) -> "Delegation":
        if self.requires_verified_issue and self.specialist != "work-order":
            raise ValueError("Only a work-order delegation can require a verified issue.")
        return self


class OrchestrationPlan(Contract):
    steps: tuple[Delegation, ...] = Field(min_length=1, max_length=8)
    answer_contract: OperatorAnswerContract


class CachedHandoff(Contract):
    specialist: SpecialistResult
    evidence: tuple[ToolEvidence, ...]


def _encoded(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def evidence_equipment_ids(receipts: Iterable[ToolEvidence]) -> set[str]:
    selected: set[str] = set()
    for receipt in receipts:
        selected.update(value for value in receipt.resolved_equipment_ids if value)
        for identity in receipt.row_identities.values():
            for key in ("equipment_id", "equipmentId"):
                value = identity.get(key)
                if isinstance(value, str) and value:
                    selected.add(value)
    return selected


def selected_equipment_from_evidence(
    selection: EquipmentSelection | None, receipts: Iterable[ToolEvidence],
) -> set[str]:
    if selection is None:
        return set()
    counts: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()
    for receipt in receipts:
        collections: list[object] = [receipt.result.get("rows")]
        if receipt.tool == "query_work_backlog":
            collections.append(receipt.result.get("open_work_orders"))
        for collection in collections:
            if not isinstance(collection, list):
                continue
            for row in collection:
                if not isinstance(row, dict):
                    continue
                equipment = row.get("equipment_id", row.get("equipmentId"))
                if not isinstance(equipment, str) or not equipment:
                    continue
                if selection.metric == "signal_quality_count":
                    if receipt.tool != "query_signal_quality_snapshot" or row.get("quality") != selection.quality:
                        continue
                    signal = row.get("opcua_node_id", row.get("opcuaNodeId"))
                    if not isinstance(signal, str) or not signal:
                        continue
                    identity = (equipment, signal)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    counts[equipment] = counts.get(equipment, 0) + 1
                elif receipt.tool == "query_work_backlog" and collection is receipt.result.get("rows"):
                    count = row.get("open_work_order_count")
                    if type(count) is int and count >= 0:
                        counts[equipment] = max(counts.get(equipment, 0), count)
                else:
                    order = row.get("workOrderNumber", row.get("work_order_number"))
                    if not isinstance(order, str):
                        continue
                    identity = (equipment, order)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    counts[equipment] = counts.get(equipment, 0) + 1
    if not counts:
        return set()
    maximum = max(counts.values())
    return {min(equipment for equipment, count in counts.items() if count == maximum)}


ANALYTICAL_POPULATION_TOOLS = {
    "signal_quality": "query_signal_quality_snapshot",
    "temperature": "query_turbine_temperature_snapshot",
    "station_power": "query_station_power",
    "work_backlog": "query_work_backlog",
}
CATALOG_DIRECT_KINDS = {
    "facilities", "equipment", "instruments", "work_orders", "inspections",
    "notifications", "spare_parts", "asset_models",
}


def deterministic_fulfillment_route(
    kind: str, scope: Literal["population", "focus"],
) -> str | None:
    if scope == "population" and kind in ANALYTICAL_POPULATION_TOOLS:
        return ANALYTICAL_POPULATION_TOOLS[kind]
    if kind in CATALOG_DIRECT_KINDS:
        return "query_assets" if kind in {"facilities", "equipment", "instruments"} else "query_operations"
    if kind == "telemetry" and scope == "focus":
        return "query_telemetry"
    return None


def _native_markdown_tables_from_payload(value: object) -> dict[str, list[dict[str, str]]]:
    tables: dict[str, list[dict[str, str]]] = {}
    metadata_keys = {"id", "title", "url", "type", "status", "name", "server_label"}

    def visit(item: object) -> None:
        if len(tables) >= 20:
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if key not in metadata_keys and not key.startswith("@"):
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str):
            for name, rows in _native_markdown_tables(item).items():
                if name in tables and tables[name] == rows:
                    continue
                unique_name = name
                suffix = 2
                while unique_name in tables:
                    unique_name = f"{name}_{suffix}"
                    suffix += 1
                tables[unique_name] = rows
                if len(tables) >= 20:
                    break

    visit(value)
    return tables


def _bounded_citation_fragments(text: str, limit: int = 1800, maximum: int = 40) -> tuple[str, ...]:
    fragments: list[str] = []
    remaining = text.strip()
    while remaining and len(fragments) < maximum:
        if len(remaining) <= limit:
            fragments.append(remaining)
            break
        boundary = max(
            remaining.rfind("\n\n", 0, limit + 1),
            remaining.rfind("\n", 0, limit + 1),
            remaining.rfind(" ", limit // 2, limit + 1),
        )
        if boundary < limit // 2:
            boundary = limit
        fragment, remaining = remaining[:boundary].strip(), remaining[boundary:].strip()
        if fragment:
            fragments.append(fragment)
    return tuple(fragments)


def rca_reference_examples(evidence: dict[str, ToolEvidence], limit: int = 40) -> tuple[str, ...]:
    examples: list[str] = []
    for evidence_id, receipt in evidence.items():
        rows = receipt.result.get("rows")
        if isinstance(rows, list):
            for index, row in enumerate(rows[:4]):
                if len(_encoded(row)) <= 2400:
                    examples.append(f"{evidence_id}:/rows/{index}")
        fragments = receipt.result.get("citation_fragments")
        if isinstance(fragments, list):
            for index, fragment in enumerate(fragments[:8]):
                if isinstance(fragment, str) and len(_encoded(fragment)) <= 2400:
                    examples.append(f"{evidence_id}:/citation_fragments/{index}")
        for key, value in receipt.result.items():
            if key in ("rows", "citation_fragments") or isinstance(value, (dict, list)) or type(value) not in (str, int, float):
                continue
            path = "/" + key.replace("~", "~0").replace("/", "~1")
            if len(_encoded(value)) <= 2400:
                examples.append(f"{evidence_id}:{path}")
        if len(examples) >= limit:
            break
    return tuple(examples[:limit])


def repair_duplicated_evidence_id_suffixes(
    value: object, allowed_evidence_ids: set[str],
) -> tuple[object, int]:
    repairs = 0

    def visit(item: object) -> object:
        nonlocal repairs
        if isinstance(item, dict):
            repaired = {key: visit(child) for key, child in item.items()}
            evidence_id = repaired.get("evidence_id")
            if isinstance(evidence_id, str) and evidence_id not in allowed_evidence_ids:
                matches = []
                for candidate in allowed_evidence_ids:
                    if not evidence_id.startswith(candidate):
                        continue
                    duplicated_suffix = evidence_id[len(candidate):]
                    if len(duplicated_suffix) >= 4 and candidate.endswith(duplicated_suffix):
                        matches.append(candidate)
                if len(matches) == 1:
                    repaired["evidence_id"] = matches[0]
                    repairs += 1
            return repaired
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, tuple):
            return tuple(visit(child) for child in item)
        return item

    return visit(value), repairs


def repair_evidence_row_descendant_paths(
    value: object, evidence: dict[str, ToolEvidence],
) -> tuple[object, int]:
    repairs = 0

    def path_exists(result: dict[str, object], path: str) -> bool:
        current: object = result
        for encoded in path.removeprefix("/").split("/"):
            segment = encoded.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict) and segment in current:
                current = current[segment]
            elif isinstance(current, list) and segment.isascii() and segment.isdecimal():
                index = int(segment)
                if index >= len(current):
                    return False
                current = current[index]
            else:
                return False
        return True

    def visit(item: object) -> object:
        nonlocal repairs
        if isinstance(item, dict):
            repaired = {key: visit(child) for key, child in item.items()}
            evidence_id, path = repaired.get("evidence_id"), repaired.get("path")
            if isinstance(evidence_id, str) and isinstance(path, str):
                receipt = evidence.get(evidence_id)
                row_match = re.fullmatch(r"(/rows/[0-9]+)(?:/.+)", path)
                if (
                    receipt is not None
                    and row_match is not None
                    and not path_exists(receipt.result, path)
                    and path_exists(receipt.result, row_match.group(1))
                ):
                    repaired["path"] = row_match.group(1)
                    repairs += 1
            return repaired
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, tuple):
            return tuple(visit(child) for child in item)
        return item

    return visit(value), repairs


def with_production_write_status(summary: str, write_reviewed: bool) -> str:
    value = summary.strip()
    if write_reviewed and "production write" not in value.lower():
        return f"{value} No production writes executed."
    return value


RCA_GAP_LABELS = {
    "fresh_measurements": "fresh, timestamped measurements and ingestion-health evidence",
    "independent_measurement": "an independently obtained measurement",
    "matched_baseline": "a baseline matched for load and operating regime",
    "approved_limits": "versioned, engineering-approved diagnostic limits",
    "operating_context": "dispatch, control-state and operating-condition records",
    "inspection_evidence": "qualified inspection evidence relevant to the signal and time window",
    "maintenance_scope": "verified scope and closure evidence for existing maintenance work",
    "parts_compatibility": "authoritative BOM, compatibility, reservation and lead-time evidence",
}


def rca_answer_limitations(specialists: list[SpecialistResult]) -> tuple[str, ...]:
    missing: set[str] = set()
    has_rca = False
    for specialist in specialists:
        if specialist.role != "rca":
            continue
        has_rca = True
        report = specialist.report
        hypotheses = report.get("hypotheses") if isinstance(report, dict) else None
        if not isinstance(hypotheses, list):
            raise SourceFailure("Validated RCA specialist output has no hypothesis collection.")
        for hypothesis in hypotheses:
            gaps = hypothesis.get("missing") if isinstance(hypothesis, dict) else None
            if not isinstance(gaps, list) or any(gap not in RCA_GAP_LABELS for gap in gaps):
                raise SourceFailure("Validated RCA specialist output contains unsupported evidence gaps.")
            missing.update(gaps)
    if not has_rca:
        return ()
    result = ["No physical fault is established by the returned evidence; cause remains undetermined."]
    if missing:
        result.append("RCA evidence gaps: " + "; ".join(RCA_GAP_LABELS[key] for key in sorted(missing)) + ".")
    return tuple(result)


class RunJournal:
    """Atomic local receipts; the caller must hold the run's process lock."""

    def __init__(self, root: Path):
        self.root = root

    def read(self, key: str) -> dict[str, object] | None:
        path = self.root / f"{sha256(key.encode()).hexdigest()}.json"
        if not path.exists():
            return None
        value = json.loads(path.read_bytes())
        if not isinstance(value, dict) or value.get("key") != key or not isinstance(value.get("value"), dict):
            raise SourceFailure("Run receipt is corrupt; no older or empty fallback is permitted.")
        return value["value"]

    def save(self, key: str, value: dict[str, object]) -> None:
        existing = self.read(key)
        if existing is not None:
            if existing != value:
                raise SourceFailure("A committed run receipt cannot be replaced.")
            return
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{sha256(key.encode()).hexdigest()}.json"
        temporary = path.with_suffix(".pending")
        if temporary.exists():
            logger.warning("Discarding an uncommitted partial run receipt: %s", temporary.name)
            temporary.unlink()
        try:
            with temporary.open("xb") as output:
                output.write(_encoded({"key": key, "value": value}))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class FoundrySupervisor:
    """A per-run execution owner. Never share this object's mutable state across runs."""

    def __init__(
        self, endpoint: str, credential: AsyncTokenCredential, bridge: Bridge,
        tools: BackendTools, source: SourceIdentity, root: Path, *, max_delegations: int = 10,
        native_binding: NativeBinding | None = None, analysis_model: str | None = None,
    ):
        if not 1 <= max_delegations <= 20:
            raise ValueError("Delegation budget must be between 1 and 20.")
        self.endpoint, self.credential, self.bridge = verified_project_endpoint(endpoint), credential, bridge
        self.analysis_model = (analysis_model or "").strip() or None
        self.tools, self.source, self.root = tools, source, root
        self.max_delegations = max_delegations
        if native_binding is not None and native_binding.source != source:
            raise ValueError("Native connection binding differs from the verified source identity.")
        self.native_binding = native_binding
        self.evidence: dict[str, ToolEvidence] = {}
        self.specialists: list[SpecialistResult] = []
        self.failures: list[Exception] = []
        self.delegations = 0
        self.tool_calls = 0
        self.tool_lock = asyncio.Lock()
        self.source_tasks: dict[str, asyncio.Task[ToolEvidence]] = {}
        self.delegation_lock = asyncio.Lock()
        self.run_id: UUID | None = None
        self.carried_ids: set[str] = set()
        self.analysis_rejection: str | None = None
        self.selected_equipment_ids: set[str] = set()
        self.answer_contract: OperatorAnswerContract | None = None
        self.orchestration_plan: OrchestrationPlan | None = None
        self.invocation_inputs: dict[str, str] = {}
        self.event_sink: Callable[[dict[str, object]], None] | None = None
        self.chief_trace: list[dict[str, object]] = []

    def set_event_sink(self, sink: Callable[[dict[str, object]], None]) -> None:
        if self.run_id is not None or self.event_sink is not None:
            raise ValueError("Execution event sink must be assigned once before the run starts.")
        self.event_sink = sink

    def emit_event(
        self, event_id: str, role: Role, status: Literal["queued", "running", "completed", "error"],
        label: str, detail: str, *, agent_name: str | None = None, response_id: str | None = None,
        parent_id: str | None = None, parent_call_id: str | None = None,
        trace: list[dict[str, object]] | None = None,
    ) -> None:
        if self.event_sink is None:
            return
        event: dict[str, object] = {
            "id": event_id, "role": role, "status": status, "label": label, "detail": detail,
            "timestamp": int(utc_now().timestamp() * 1000),
        }
        for key, value in (
            ("agentName", agent_name), ("responseId", response_id), ("parentId", parent_id),
            ("parentCallId", parent_call_id), ("trace", trace),
        ):
            if value is not None:
                event[key] = value
        self.event_sink(event)

    def healthy(self, request: ChatRequest) -> None:
        if self.failures:
            first = self.failures[0]
            if isinstance(first, SourceFailure):
                raise SourceFailure(str(first)) from first
            raise SourceFailure(
                f"{type(first).__name__}: source, handoff or validation failed; no successful answer is permitted."
            ) from first

    def load_carried_evidence(self, request: ChatRequest, journal: RunJournal) -> None:
        """Seed this run with the verified receipts the follow-up reuses. They keep their original read times, so
        the answer can disclose their age; they are never presented as a fresh read."""
        if not request.carried_evidence_ids:
            return
        saved = journal.read("carried_evidence")
        if saved is None:
            previous = RunJournal(self.root / str(request.carried_from) / "receipts").read("evidence")
            raw = previous.get("receipts") if isinstance(previous, dict) else None
            if not isinstance(raw, list):
                raise SourceFailure("The previous answer's evidence is no longer available; ask the question again.")
            by_id = {item.id: item for item in (ToolEvidence.model_validate(value) for value in raw)}
            missing = [identity for identity in request.carried_evidence_ids if identity not in by_id]
            if missing:
                raise SourceFailure("Carried evidence differs from the previous answer's receipts.")
            chosen = [by_id[identity] for identity in request.carried_evidence_ids]
            journal.save("carried_evidence", {
                "from": str(request.carried_from), "receipts": [item.model_dump(mode="json") for item in chosen],
            })
        else:
            chosen = [ToolEvidence.model_validate(value) for value in cast(list[object], saved["receipts"])]
        for receipt in chosen:
            if (receipt.source != request.source or receipt.tool == "propose_work_order"
                    or receipt.completed_at > request.requested_at):
                raise SourceFailure("Carried evidence must be an earlier verified read from the same source.")
            self.evidence.setdefault(receipt.id, receipt)
        self.carried_ids = {item.id for item in chosen}

    def carried_limitations(self, request: ChatRequest) -> tuple[str, ...]:
        carried = [self.evidence[identity] for identity in sorted(self.carried_ids) if identity in self.evidence]
        if not carried:
            return ()
        oldest = min(item.completed_at for item in carried)
        newest = max(item.completed_at for item in carried)
        age_minutes = max(0, int((request.requested_at - oldest).total_seconds() // 60))
        return (
            f"Reused evidence: {', '.join(sorted({item.tool for item in carried}))} came from the previous answer "
            f"(read {oldest:%H:%M:%S}Z to {newest:%H:%M:%S}Z UTC, up to {age_minutes} min before this question) "
            "instead of being re-read. Ask for a fresh read to refresh it.",
        )

    async def fulfill_catalog_requirements(
        self, request: ChatRequest, journal: RunJournal,
    ) -> None:
        contract = self.answer_contract
        if contract is None:
            raise SourceFailure("Evidence fulfillment requires the Supervisor's validated answer contract.")
        catalog = SourceCatalog.model_validate(journal.read("tool_catalog"))

        async def execute_required(
            name: str, arguments: dict[str, object], kind: str, scope: Literal["population", "focus"],
            *, publish: bool = True,
        ) -> ToolEvidence:
            specification = next((tool for tool in catalog.tools if tool.name == name), None)
            if specification is None:
                raise SourceFailure(f"Required evidence reader {name} is unavailable.")
            if list(
                Draft202012Validator(specification.parameters).iter_errors(
                    json.loads(_encoded(arguments))
                )
            ):
                raise SourceFailure(
                    f"Deterministic {kind} evidence arguments violate the governed source catalog."
                )
            key = "tool:" + _encoded({"name": name, "arguments": arguments}).decode()
            saved = journal.read(key)
            if saved is None:
                async with self.tool_lock:
                    self.tool_calls += 1
                    if self.tool_calls > 40:
                        raise SourceFailure("The per-run source-tool budget was exceeded.")
                supplied = await self.tools.execute(name, arguments, request)
                result = ToolEvidence.model_validate(supplied.model_dump(mode="json"))
                journal.save(key, result.model_dump(mode="json"))
            else:
                result = ToolEvidence.model_validate(saved)
            self.healthy(request)
            if (
                result.source != request.source
                or result.tool != name
                or result.arguments != arguments
                or result.completed_at > utc_now()
                or result.completed_at < request.requested_at
            ):
                raise SourceFailure("Deterministic evidence changed its source, query, or read clock.")
            if not any(
                dataset.kind == kind
                and dataset.scope == scope
                and dataset.authority == "direct"
                and dataset.role != "audit"
                and dataset.complete
                for dataset in result.datasets
            ):
                raise SourceFailure(
                    f"Deterministic {name} read did not satisfy {kind} {scope} evidence."
                )
            if publish:
                prior = self.evidence.get(result.id)
                if prior is not None and prior != result:
                    raise SourceFailure("Evidence identity was reused for a different source result.")
                self.evidence[result.id] = result
            return result

        direct_requirements = {
            (item.kind, item.scope)
            for item in contract.requirements
            if item.authority == "direct"
        }
        unsupported = sorted(
            f"{kind}/{scope}" for kind, scope in direct_requirements
            if deterministic_fulfillment_route(
                kind, cast(Literal["population", "focus"], scope),
            ) is None
        )
        if unsupported:
            raise SourceFailure(
                "The answer contract has no deterministic pre-execution evidence route for: "
                + ", ".join(unsupported)
            )
        answer_kinds = sorted({kind for kind, _scope in direct_requirements})
        analytical_contract = await self.bridge.call({
            "action": "analytical_evidence_plan",
            "question": request.question,
            "answer_kinds": answer_kinds,
        })
        if set(analytical_contract) != {"entries"} or not isinstance(analytical_contract["entries"], list):
            raise SourceFailure("Analytical evidence planning returned an unsupported contract.")
        analytical_tools = {**ANALYTICAL_POPULATION_TOOLS, "telemetry": "query_telemetry"}
        deferred_focus_analytics: list[tuple[str, dict[str, object], dict[str, object]]] = []
        for item in analytical_contract["entries"]:
            if not isinstance(item, dict) or set(item) != {
                "kind", "tool", "arguments", "match_arguments",
            }:
                raise SourceFailure("Analytical evidence planning returned an unsupported entry.")
            kind, name = item["kind"], item["tool"]
            arguments, match_arguments = item["arguments"], item["match_arguments"]
            scope: Literal["population", "focus"] = "focus" if kind == "telemetry" else "population"
            if (
                not isinstance(kind, str)
                or not isinstance(name, str)
                or not isinstance(arguments, dict)
                or not isinstance(match_arguments, dict)
                or analytical_tools.get(kind) != name
                or (kind, scope) not in direct_requirements
                or any(key not in arguments or arguments[key] != value
                       for key, value in match_arguments.items())
            ):
                raise SourceFailure("Analytical evidence planning crossed the governed answer contract.")
            if scope == "focus":
                deferred_focus_analytics.append((name, arguments, match_arguments))
                continue
            if any(
                receipt.tool == name
                and all(receipt.arguments.get(key) == value for key, value in match_arguments.items())
                and any(
                    dataset.kind == kind and dataset.scope == scope
                    and dataset.authority == "direct" and dataset.role != "audit" and dataset.complete
                    for dataset in receipt.datasets
                )
                for receipt in self.evidence.values()
            ):
                continue
            await execute_required(name, arguments, kind, "population")

        if request.equipment_selection is not None:
            self.selected_equipment_ids = selected_equipment_from_evidence(
                request.equipment_selection, self.evidence.values(),
            )

        asset_entities = {"facilities", "equipment", "instruments"}
        operation_entities = CATALOG_DIRECT_KINDS - asset_entities
        for requirement in contract.requirements:
            if requirement.authority != "direct" or requirement.kind not in asset_entities | operation_entities:
                continue
            if any(
                dataset.kind == requirement.kind
                and dataset.scope == requirement.scope
                and dataset.authority == "direct"
                and dataset.role != "audit"
                and dataset.complete
                for receipt in self.evidence.values()
                for dataset in receipt.datasets
            ):
                continue
            if requirement.scope == "focus" and not self.selected_equipment_ids:
                continue
            if requirement.kind in asset_entities:
                name = "query_assets"
                identity_column = "equipment_id"
            else:
                name = "query_operations"
                identity_column = "equipmentId"
            arguments: dict[str, object] = {"entity": requirement.kind}
            if requirement.scope == "focus":
                arguments["where"] = [{
                    "column": identity_column,
                    "op": "in",
                    "value": sorted(self.selected_equipment_ids),
                }]
            await execute_required(name, arguments, requirement.kind, requirement.scope)

        for name, planned_arguments, match_arguments in deferred_focus_analytics:
            if not self.selected_equipment_ids:
                continue
            if any(
                receipt.tool == name
                and all(receipt.arguments.get(key) == value for key, value in match_arguments.items())
                and any(
                    dataset.kind == "telemetry" and dataset.scope == "focus"
                    and dataset.authority == "direct" and dataset.role != "audit" and dataset.complete
                    for dataset in receipt.datasets
                )
                for receipt in self.evidence.values()
            ):
                continue
            specification = next((tool for tool in catalog.tools if tool.name == name), None)
            if specification is None:
                raise SourceFailure(f"Required evidence reader {name} is unavailable.")
            properties = specification.parameters.get("properties", {})
            if not isinstance(properties, dict):
                raise SourceFailure("The governed telemetry reader has no parameter schema.")
            arguments = {
                key: value for key, value in planned_arguments.items()
                if key in properties
            }
            if "opcua_node_ids" in properties:
                prerequisites: list[ToolEvidence] = []

                def selected_nodes() -> set[str]:
                    nodes: set[str] = set()
                    for receipt in [*self.evidence.values(), *prerequisites]:
                        rows = receipt.result.get("rows")
                        if not isinstance(rows, list):
                            continue
                        for index, row in enumerate(rows):
                            if not isinstance(row, dict):
                                continue
                            identity = receipt.row_identities.get(f"/rows/{index}", {})
                            equipment = row.get(
                                "equipment_id", row.get("equipmentId",
                                identity.get("equipment_id", identity.get("equipmentId")))
                            )
                            node = row.get(
                                "opcua_node_id", row.get("opcuaNodeId",
                                identity.get("opcua_node_id", identity.get("opcuaNodeId")))
                            )
                            if equipment in self.selected_equipment_ids and isinstance(node, str) and node:
                                nodes.add(node)
                    return nodes

                nodes = selected_nodes()
                if not nodes:
                    prerequisites.append(await execute_required("query_assets", {
                        "entity": "instruments",
                        "where": [{
                            "column": "equipment_id",
                            "op": "in",
                            "value": sorted(self.selected_equipment_ids),
                        }],
                    }, "instruments", "focus", publish=False))
                    nodes = selected_nodes()
                if not nodes:
                    raise SourceFailure(
                        "Required telemetry focus evidence has no verified signal identity."
                    )
                arguments["opcua_node_ids"] = sorted(nodes)
            elif "equipment_id" in properties and len(self.selected_equipment_ids) == 1:
                arguments["equipment_id"] = next(iter(self.selected_equipment_ids))
            else:
                raise SourceFailure(
                    "The governed telemetry reader cannot bind the selected equipment."
                )
            await execute_required(name, arguments, "telemetry", "focus")

    def model_evidence(self, role: Role, targeted: bool = False) -> list[dict[str, object]]:
        if role != "work-order" and not targeted:
            return [item.receipt() for item in self.evidence.values()]
        if (role == "work-order" and not targeted and self.carried_ids
                and not any(item.role == "rca" for item in self.specialists)):
            # No RCA narrowed the focus, and the verified dataset this draft is based on was carried in: the
            # specialist needs all of it to choose its target, not a projection around an unknown focus.
            return [item.receipt() for item in self.evidence.values()]

        referenced_rows: dict[str, set[str]] = {}
        for specialist in self.specialists:
            if specialist.role != "rca" or not isinstance(specialist.report, dict):
                continue

            def collect(value: object) -> None:
                if isinstance(value, dict):
                    evidence_id, path = value.get("evidence_id"), value.get("path")
                    if isinstance(evidence_id, str) and isinstance(path, str):
                        segments = path.split("/")
                        if len(segments) >= 3 and segments[1] == "rows" and segments[2].isascii() and segments[2].isdecimal():
                            referenced_rows.setdefault(evidence_id, set()).add(f"/rows/{segments[2]}")
                    for child in value.values():
                        collect(child)
                elif isinstance(value, list):
                    for child in value:
                        collect(child)

            collect(specialist.report)

        target_equipment_ids: set[str] = set()
        for evidence_id, paths in referenced_rows.items():
            receipt = self.evidence.get(evidence_id)
            if receipt is None:
                continue
            for path in paths:
                identity = receipt.row_identities.get(path, {})
                for key in ("equipment_id", "equipmentId"):
                    value = identity.get(key)
                    if value:
                        target_equipment_ids.add(value)

        projected: list[dict[str, object]] = []
        for receipt in self.evidence.values():
            relevant_paths = set(referenced_rows.get(receipt.id, ()))
            for path, identity in receipt.row_identities.items():
                if any(identity.get(key) in target_equipment_ids for key in ("equipment_id", "equipmentId")):
                    relevant_paths.add(path)
            metadata_match = bool(
                target_equipment_ids.intersection(receipt.resolved_equipment_ids)
                or target_equipment_ids.intersection(receipt.work_coverage_equipment_ids)
            )
            result: dict[str, object] = {
                key: value for key, value in receipt.result.items()
                if type(value) in (str, int, float, bool) or value is None
            }
            rows = receipt.result.get("rows")
            if isinstance(rows, list):
                selected_rows = [
                    row for index, row in enumerate(rows) if f"/rows/{index}" in relevant_paths
                ]
                if selected_rows:
                    result["rows"] = selected_rows
                    result["projected_row_count"] = len(selected_rows)
            elif metadata_match or receipt.id in referenced_rows:
                result = dict(receipt.result)
            if not relevant_paths and not metadata_match and receipt.id not in referenced_rows:
                continue
            projected.append({
                "id": receipt.id,
                "tool": receipt.tool,
                "completedAt": receipt.completed_at.isoformat(),
                "result": result,
                "limitations": receipt.limitations,
                "resolved_equipment_ids": tuple(
                    value for value in receipt.resolved_equipment_ids if value in target_equipment_ids
                ),
                "work_coverage_equipment_ids": tuple(
                    value for value in receipt.work_coverage_equipment_ids if value in target_equipment_ids
                ),
                "model_context_scope": "Role-relevant projection; the complete immutable receipt remains in the audit.",
            })
        return projected

    def model_specialists(self, role: Role, compact: bool = False) -> list[dict[str, object]]:
        if role != "work-order" and not compact:
            return [item.model_dump(mode="json") for item in self.specialists]
        values: list[dict[str, object]] = []
        for item in self.specialists:
            report = item.report
            if compact and role == "qa" and isinstance(report, dict):
                report = {
                    key: report[key] for key in ("observations", "maintenance_follow_up")
                    if key in report
                }
            values.append({
                "role": item.role,
                "agent_name": item.agent_name,
                "version": item.version,
                "response_id": item.response_id,
                "duration_ms": item.duration_ms,
                "report": report,
            })
        return values

    async def versions(self, journal: RunJournal) -> dict[Role, AgentVersion]:
        contracts = await self.bridge.call({"action": "agent_contracts"})
        names, expected = contracts.get("names"), contracts.get("tools")
        if not isinstance(names, dict) or not isinstance(expected, dict):
            raise SourceFailure("Shared deployed-agent contracts are unavailable.")
        saved = journal.read("agent_versions")
        control_plane = AgentVersion(
            name=CONTROL_PLANE_AGENT, version=CONTROL_PLANE_VERSION,
            model=self.analysis_model or "deterministic", tools=(),
        )
        if saved is not None:
            if set(saved) != {*names, "supervisor"}:
                raise SourceFailure("Recovered agent manifest does not contain exactly the configured roles.")
            versions = {role: AgentVersion.model_validate(saved[role]) for role in saved}
            if any(versions[role].name != name for role, name in names.items()):
                raise SourceFailure("Recovered agent manifest changed the configured agent identities.")
            recovered_plane = versions["supervisor"]
            if (recovered_plane.name, recovered_plane.version) != (CONTROL_PLANE_AGENT, CONTROL_PLANE_VERSION):
                raise SourceFailure("Recovered agent manifest belongs to a different control-plane revision.")
            return cast(dict[Role, AgentVersion], versions)
        token = await self.credential.get_token("https://ai.azure.com/.default")

        async def read(role: Role, name: str, client: httpx.AsyncClient) -> tuple[Role, AgentVersion]:
            response = await client.get(f"{self.endpoint}/agents/{name}?api-version=v1",
                                        headers={"Authorization": f"Bearer {token.token}"})
            if not response.is_success:
                raise SourceFailure(f"Foundry {role} readback failed (HTTP {response.status_code}).")
            value = response.json()
            latest = value["versions"]["latest"]
            definition = latest["definition"]
            if value["name"] != name or definition["kind"] != "prompt":
                raise SourceFailure("The existing Foundry agent identity/type differs from its contract.")
            live_tools = definition.get("tools", [])
            if role == "fabric-iq":
                labels = {tool.get("server_label") for tool in live_tools if tool.get("type") == "fabric_iq_preview"}
                if (
                    labels != {"fabriciq-data-agent"}
                    or len(live_tools) != 1
                    or self.native_binding is None
                    or live_tools[0].get("project_connection_id")
                    != self.native_binding.connections.get("data-agent")
                ):
                    raise SourceFailure("The live Fabric IQ agent must retain only its verified Data Agent connection.")
            else:
                wanted = expected[role]
                for tool in wanted:
                    matches = [item for item in live_tools if item.get("name") == tool["name"]
                               and item.get("type") == "function"]
                    if len(matches) != 1 or any(matches[0].get(key) != tool.get(key) for key in ("parameters", "strict")):
                        raise SourceFailure(f"Live {role} function contract has drifted.")
                if len(live_tools) != len(wanted):
                    raise SourceFailure(f"Unexpected tools are declared on the live {role} agent.")
            return role, AgentVersion(name=name, version=latest["version"], model=definition["model"],
                                      tools=tuple(live_tools))

        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            values = await asyncio.gather(*(read(role, name, client) for role, name in names.items()))
        versions = dict(values)
        versions["supervisor"] = control_plane
        journal.save("agent_versions", {role: version.model_dump(mode="json") for role, version in versions.items()})
        return versions

    async def compile_orchestration_plan(
        self, request: ChatRequest, journal: RunJournal,
    ) -> OrchestrationPlan:
        compiled = await self.bridge.call({
            "action": "compile_orchestration_plan",
            "question": request.question,
            "intent": {
                "charts_requested": request.charts_requested,
                "native_sources": list(request.native_sources),
                "proposal_priority": request.proposal_priority,
                "equipment_selection": (
                    request.equipment_selection.model_dump(mode="json")
                    if request.equipment_selection is not None else None
                ),
                "required_evidence_kinds": list(request.required_evidence_kinds),
                "permitted_evidence_kinds": list(request.permitted_evidence_kinds),
                "required_specialists": list(request.required_specialists),
                "required_specialist_steps": request.required_specialist_steps,
                "evidence_carried": bool(request.carried_evidence_ids),
            },
        })
        if not isinstance(compiled, dict) or set(compiled) != {"steps", "answer_contract"}:
            raise SourceFailure("Deterministic orchestration compilation returned an unsupported contract.")
        raw_steps = compiled.get("steps")
        if not isinstance(raw_steps, list):
            raise SourceFailure("Deterministic orchestration steps are not a bounded list.")
        normalized_steps = []
        for raw_step in raw_steps:
            if not isinstance(raw_step, dict):
                raise SourceFailure("Deterministic orchestration emitted an invalid specialist step.")
            step = dict(raw_step)
            step["nativeSource"] = step.pop("native_source", None)
            step["requiresSelection"] = step.pop("requires_selection", False)
            step["requiresVerifiedIssue"] = step.pop("requires_verified_issue", False)
            normalized_steps.append(step)
        plan = OrchestrationPlan.model_validate({
            **compiled,
            "steps": normalized_steps,
        })
        catalog = SourceCatalog.model_validate(journal.read("tool_catalog"))
        governed = governed_datasets(catalog.context)
        requested_kinds = {
            item.kind for item in plan.answer_contract.requirements
        }.union(view.kind for view in plan.answer_contract.views)
        unknown_kinds = requested_kinds.difference(governed)
        if unknown_kinds:
            raise SourceFailure(
                "Deterministic orchestration requested ungoverned datasets: "
                + ", ".join(sorted(unknown_kinds))
            )
        unsupported_requirements = [
            f"{item.kind}/{item.scope}/{item.authority}"
            for item in plan.answer_contract.requirements
            if (
                item.scope not in governed[item.kind].evidence_scopes
                or item.authority not in governed[item.kind].evidence_authorities
            )
        ]
        if unsupported_requirements:
            raise SourceFailure(
                "Deterministic orchestration requested unsupported evidence combinations: "
                + ", ".join(unsupported_requirements)
            )
        for view in plan.answer_contract.views:
            if view.authority == "reconciled":
                continue
            layout = view.presentation.removeprefix(f"{view.authority}_")
            if layout not in governed[view.kind].presentations:
                raise SourceFailure(
                    f"Deterministic orchestration requested unsupported presentation "
                    f"{view.id}:{view.kind}/{layout}."
                )
        delegations = []
        for step in plan.steps:
            parsed = await self.bridge.call({
                "action": "validate_delegation",
                "report": json.dumps({
                    "specialist": step.specialist,
                    "question": step.question,
                    "reason": step.reason,
                    "native_source": step.native_source,
                    "requires_selection": step.requires_selection,
                    "requires_verified_issue": step.requires_verified_issue,
                }),
            })
            delegations.append(Delegation.model_validate(parsed))
        requirements = await self.bridge.call({
            "action": "validate_plan_requirements",
            "question": request.question,
            "answer_kinds": sorted(requested_kinds),
            "required_evidence_kinds": list(request.required_evidence_kinds),
            "permitted_evidence_kinds": list(request.permitted_evidence_kinds),
            "required_specialists": list(request.required_specialists),
            "required_specialist_steps": request.required_specialist_steps,
            "steps": [{
                "specialist": item.specialist,
                "native_source": item.native_source,
            } for item in delegations],
            "native_sources": list(request.native_sources),
        })
        if set(requirements) != {
            "missing_specialists", "missing_specialist_steps",
            "missing_native_sources", "missing_evidence_kinds", "unexpected_evidence_kinds",
        }:
            raise SourceFailure("Deterministic plan validation returned an unsupported contract.")
        missing_specialists = requirements.get("missing_specialists")
        missing_steps = requirements.get("missing_specialist_steps")
        missing_sources = requirements.get("missing_native_sources")
        missing_evidence = requirements.get("missing_evidence_kinds")
        unexpected_evidence = requirements.get("unexpected_evidence_kinds")
        if (
            not isinstance(missing_specialists, list)
            or not isinstance(missing_steps, dict)
            or not isinstance(missing_sources, list)
            or not isinstance(missing_evidence, list)
            or not isinstance(unexpected_evidence, list)
        ):
            raise SourceFailure("Deterministic plan validation returned invalid field shapes.")
        defects = [
            *missing_specialists,
            *(f"{role}:{count} steps" for role, count in missing_steps.items()),
            *(f"fabric-iq:{source}" for source in missing_sources),
            *(f"evidence:{kind}" for kind in missing_evidence),
            *(f"unexpected-evidence:{kind}" for kind in unexpected_evidence),
        ]
        if defects:
            raise SourceFailure(
                "Deterministic orchestration failed its complete request contract: "
                + ", ".join(str(item) for item in defects)
            )
        accepted = plan.model_copy(update={"steps": tuple(delegations)})
        self.orchestration_plan = accepted
        self.answer_contract = accepted.answer_contract
        self.selected_equipment_ids = set(initial_equipment_selection(request))
        return accepted

    async def handoff(
        self, delegation: Delegation, request: ChatRequest, journal: RunJournal, versions: dict[Role, AgentVersion],
        targeted_context: bool = False, maintenance_gate_required: bool = False,
    ) -> dict[str, object]:
        selected, selected_native = delegation.specialist, delegation.native_source
        if (selected == "fabric-iq" and selected_native not in request.native_sources
                or selected != "fabric-iq" and selected_native is not None):
            raise SourceFailure("Delegation selected an unrequested native source.")
        async with self.delegation_lock:
            self.delegations += 1
            if self.delegations > self.max_delegations:
                raise SourceFailure("The per-run specialist budget was exceeded.")
            assert self.run_id is not None
            chief_id = f"{self.run_id}:chief"
            call_id = f"{self.run_id}:delegation:{self.delegations}"
            specialist_id = f"{call_id}:{selected}"
            dispatched_at = int(utc_now().timestamp() * 1000)
            self.chief_trace.append({
                "id": f"{call_id}:dispatch", "timestamp": dispatched_at, "source": "application",
                "label": f"Supervisor dispatched {versions[selected].name}.", "callId": call_id,
                "activity": "tool-start",
            })
            self.emit_event(
                chief_id, "supervisor", "running", "Supervisor",
                "Dispatching bounded specialist work from the deterministic plan.",
                agent_name="hydro-orchestrator", trace=list(self.chief_trace),
            )
            self.emit_event(
                specialist_id, selected, "running", versions[selected].name,
                delegation.reason or f"Supervisor dispatched verified {selected} work.",
                agent_name=versions[selected].name,
                parent_id=chief_id, parent_call_id=call_id,
            )
            handoff_context = {
                "evidence": [item.receipt() for item in self.evidence.values()],
                "completed_specialists": [item.model_dump(mode="json") for item in self.specialists],
            }
            key = "delegate:" + _encoded({
                "role": selected, "question": delegation.question, "native": selected_native,
                "context_digest": sha256(_encoded(handoff_context)).hexdigest(),
                "targeted_context": targeted_context,
                "maintenance_gate_required": maintenance_gate_required,
            }).decode()
            saved = journal.read(key)
            try:
                if saved is not None:
                    handoff = CachedHandoff.model_validate(saved)
                    result = handoff.specialist
                    if result.role != selected or result.agent_name != versions[selected].name or result.version != versions[selected].version:
                        raise SourceFailure("Recovered specialist receipt differs from the pinned role or version.")
                    for receipt in handoff.evidence:
                        if receipt.source != request.source:
                            raise SourceFailure("Recovered handoff belongs to a different source.")
                        prior = self.evidence.get(receipt.id)
                        if prior is not None and prior != receipt:
                            raise SourceFailure(
                                "Recovered handoff attempted to replace an immutable evidence receipt."
                            )
                        self.evidence[receipt.id] = receipt
                    if result.report is not None:
                        await self.bridge.call({
                            "action": "validate_rca" if selected == "rca" else "validate_work_review",
                            "report": json.dumps(result.report),
                            "receipts": [item.receipt() for item in self.evidence.values()],
                        })
                else:
                    response, assessment, elapsed = await self.invoke(
                        selected, delegation.question, request, journal, versions, selected_native,
                        targeted_context=targeted_context, maintenance_gate_required=maintenance_gate_required,
                    )
                    if not response.response_id:
                        raise SourceFailure("Specialist invocation has no service response identity.")
                    result = SpecialistResult(
                        role=selected, agent_name=versions[selected].name, version=versions[selected].version,
                        response_id=response.response_id, duration_ms=elapsed,
                        model_round_count=sum(message.role == "assistant" for message in response.messages),
                        input_digest=self.invocation_inputs[response.response_id], text=response.text, report=assessment,
                    )
                    journal.save(key, CachedHandoff(specialist=result, evidence=tuple(self.evidence.values()))
                                 .model_dump(mode="json"))
            except Exception:
                failed_at = int(utc_now().timestamp() * 1000)
                self.emit_event(
                    specialist_id, selected, "error", versions[selected].name,
                    "Specialist execution failed; no result was accepted.", agent_name=versions[selected].name,
                    parent_id=chief_id, parent_call_id=call_id,
                )
                self.chief_trace.append({
                    "id": f"{call_id}:return", "timestamp": failed_at, "source": "application",
                    "label": f"{versions[selected].name} returned a failure.", "callId": call_id,
                    "failed": True, "activity": "delegation-return",
                })
                self.emit_event(
                    chief_id, "supervisor", "running", "Supervisor", "A specialist failure was returned.",
                    agent_name="hydro-orchestrator", trace=list(self.chief_trace),
                )
                raise
            self.emit_event(
                specialist_id, selected, "completed", versions[selected].name,
                f"Version {result.version}; measured duration {result.duration_ms:.0f} ms.",
                agent_name=result.agent_name, response_id=result.response_id,
                parent_id=chief_id, parent_call_id=call_id,
            )
            self.chief_trace.append({
                "id": f"{call_id}:return", "timestamp": int(utc_now().timestamp() * 1000),
                "source": "foundry", "label": f"{versions[selected].name} returned verified output.",
                "responseId": result.response_id, "callId": call_id, "activity": "delegation-return",
            })
            self.emit_event(
                chief_id, "supervisor", "running", "Supervisor",
                "Specialist output returned to the deterministic verifier.",
                agent_name="hydro-orchestrator", trace=list(self.chief_trace),
            )
            if result not in self.specialists:
                self.specialists.append(result)
            return {"specialist": result.model_dump(mode="json"),
                    "evidence": [item.receipt() for item in self.evidence.values()]}

    async def invoke(
        self, role: Role, question: str, request: ChatRequest, journal: RunJournal,
        versions: dict[Role, AgentVersion], native_source: NativeSource | None = None,
        targeted_context: bool = False, maintenance_gate_required: bool = False,
    ) -> tuple[AgentResponse, dict[str, object] | None, float]:
        if role == "supervisor":
            raise SourceFailure("The deterministic Supervisor control plane cannot invoke a planning model.")
        self.healthy(request)
        version = versions[role]
        selected: list[dict[str, object]] = []
        if role == "fabric-iq":
            if native_source != "data-agent":
                raise SourceFailure(
                    "Direct ontology-native chat execution is disabled; Sparky is bound only to the Data Agent."
                )
            if self.native_binding is None or native_source not in self.native_binding.connections:
                raise SourceFailure("A separately verified target-specific native connection binding is required.")
            selected = [tool for tool in version.tools
                        if tool.get("server_label") == f"fabriciq-{native_source}"]
            if len(selected) != 1 or selected[0].get("project_connection_id") != self.native_binding.connections[native_source]:
                raise SourceFailure("Native agent connection does not match the verified selected source binding.")
        completed_report: dict[str, object] | None = None

        input_repairs: dict[str, int] = {}

        async def guarded(name: str, callback, **kwargs):
            try:
                self.healthy(request)
                return await callback(**kwargs)
            except ToolInputError as error:
                input_repairs[name] = input_repairs.get(name, 0) + 1
                logger.warning("Foundry %s %s arguments rejected before execution; correction %s/1.",
                               role, name, input_repairs[name])
                if input_repairs[name] > 1:
                    self.failures.append(error)
                    raise
                return {"status": "invalid_arguments", "source_executed": False, "error": str(error),
                        "corrections_remaining": 0}
            except Exception as error:
                # Agent Framework turns callback errors into model-visible tool errors.
                # Retain the failure so the outer workflow cannot report success.
                self.failures.append(error)
                logger.error("Foundry %s callback failed (%s); run cannot succeed.", role, type(error).__name__)
                raise

        async def query(tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
            arguments = canonical_source_arguments(request, arguments)
            normalized_arguments = normalize_filter_conjunctions(arguments)
            if normalized_arguments != arguments:
                logger.warning(
                    "Normalized redundant AND separators in Foundry %s %s filter arguments.",
                    role, tool_name,
                )
                arguments = normalized_arguments
            catalog = SourceCatalog.model_validate(journal.read("tool_catalog"))
            specification = next((tool for tool in catalog.tools if tool.name == tool_name), None)
            if specification is None:
                raise ToolInputError("Selected tool is not enabled by the backend source catalog. Choose a listed tool.")
            if tool_name == "propose_work_order" and not requests_new_work_order_proposal(request.question):
                raise ToolInputError(
                    "This turn did not explicitly request a new work-order proposal. Treat any historical draft as "
                    "read-only conversation context and complete a no-draft review instead."
                )
            errors = list(Draft202012Validator(specification.parameters).iter_errors(json.loads(_encoded(arguments))))
            if errors:
                fields = ", ".join(str(key) for key in errors[0].absolute_path) or "arguments"
                filter_shape = (
                    ' The "where" field is an array of condition objects and already uses AND semantics; '
                    'do not insert "and" strings between objects.'
                    if errors[0].validator == "type"
                    and list(errors[0].absolute_path)[:1] == ["where"]
                    else ""
                )
                raise ToolInputError(
                    f"Invalid {tool_name} {fields}: {errors[0].validator} constraint failed. "
                    "Correct arguments using the exact supplied JSON schema; do not add aliases or wrappers."
                    f"{filter_shape}"
                )
            parsed = await self.bridge.call({"action": "validate_hydro_query",
                                            "role": role,
                                            "report": json.dumps({"tool_name": tool_name, "arguments": arguments})})
            if parsed.get("toolName") != tool_name:
                raise SourceFailure("Shared tool validation changed the tool identity.")
            if tool_name == "visualize_dataset" and not request.charts_requested:
                raise SourceFailure("Unrequested visualization calls are not permitted.")
            key = "tool:" + _encoded({"name": tool_name, "arguments": arguments}).decode()

            async def read_source() -> ToolEvidence:
                supplied = await self.tools.execute(tool_name, arguments, request)
                result = ToolEvidence.model_validate(supplied.model_dump(mode="json"))
                self.healthy(request)
                if result.source != request.source or result.tool != tool_name or result.arguments != arguments:
                    raise SourceFailure("Tool receipt changed its authoritative source or arguments.")
                if result.completed_at > utc_now():
                    raise SourceFailure("Source receipt timestamp exceeds the run clock.")
                if result.completed_at < request.requested_at:
                    raise SourceFailure("Prior-run analytical values cannot be reused as a new source read.")
                if tool_name == "propose_work_order":
                    draft = WorkOrderDraft.model_validate(result.result.get("proposal"))
                    if (draft.run_id != request.run_id or draft.source != request.source
                            or not request.requested_at <= draft.work_read_at <= result.completed_at
                            or draft.equipment_id not in result.resolved_equipment_ids
                            or draft.equipment_id not in result.work_coverage_equipment_ids
                            or result.result.get("confirmation_required") is not True
                            or result.result.get("production_write_executed") is not False):
                        raise SourceFailure("A draft requires resolved identity, fresh complete work coverage and human approval.")
                if len(_encoded(result.model_dump(mode="json"))) > 262144:
                    raise SourceFailure("Source receipt exceeds the bounded execution-context size.")
                journal.save(key, result.model_dump(mode="json"))
                return result

            async with self.tool_lock:
                saved = journal.read(key)
                task: asyncio.Task[ToolEvidence] | None = None
                if saved is None:
                    task = self.source_tasks.get(key)
                    if task is None:
                        self.tool_calls += 1
                        if self.tool_calls > 40:
                            raise SourceFailure("The per-run source-tool budget was exceeded.")
                        task = asyncio.create_task(read_source())
                        self.source_tasks[key] = task
            if saved is None:
                if task is None:
                    raise RuntimeError("The source read was not scheduled.")
                result = await task
            else:
                result = ToolEvidence.model_validate(saved)
            async with self.tool_lock:
                if result.source != request.source or result.tool != tool_name or result.arguments != arguments:
                    raise SourceFailure("Tool receipt changed its authoritative source or arguments.")
                if result.completed_at > utc_now():
                    raise SourceFailure("Source receipt timestamp exceeds the run clock.")
                if result.completed_at < request.requested_at:
                    raise SourceFailure("Prior-run analytical values cannot be reused as a new source read.")
                prior = self.evidence.get(result.id)
                if prior is not None and prior != result:
                    raise SourceFailure("Evidence identity was reused for a different source result.")
                self.evidence[result.id] = result
                return {"evidence_id": result.id, "data": result.result, "limitations": result.limitations}

        async def report(**kwargs) -> dict[str, object]:
            nonlocal completed_report
            action = "validate_rca" if role == "rca" else "validate_work_review"
            if completed_report is not None:
                raise SourceFailure("A specialist cannot replace its completed structured assessment.")
            if role == "rca":
                repaired, repair_count = repair_duplicated_evidence_id_suffixes(kwargs, set(self.evidence))
                kwargs = cast(dict[str, object], repaired)
                if repair_count:
                    logger.warning(
                        "Foundry rca completion duplicated a terminal evidence-ID suffix; "
                        "repaired %s exact run-scoped reference(s) before validation.",
                        repair_count,
                    )
                repaired, repair_count = repair_evidence_row_descendant_paths(kwargs, self.evidence)
                kwargs = cast(dict[str, object], repaired)
                if repair_count:
                    logger.warning(
                        "Foundry rca completion referenced missing row descendant fields; "
                        "repaired %s reference(s) to their existing verified rows before validation.",
                        repair_count,
                    )
            if role == "rca" and not maintenance_gate_required:
                kwargs = {
                    **kwargs,
                    "maintenance_follow_up": {
                        "decision": "not_supported",
                        "reason": "missing_issue_evidence",
                        "equipment_ids": [],
                        "evidence": [],
                    },
                }
            try:
                validated_report = await self.bridge.call({
                    "action": action, "report": json.dumps(kwargs),
                    "receipts": [item.receipt() for item in self.evidence.values()],
                })
            except SourceFailure as error:
                allowed_evidence_ids = ", ".join(list(self.evidence)[:20])
                allowed_references = ", ".join(rca_reference_examples(self.evidence)[:20])
                raise ToolInputError(f"Structured assessment rejected: {str(error)[:512]}. "
                                     "Correct only the completion using existing evidence; no new source read is needed. "
                                     f"Use one of these exact evidence IDs: {allowed_evidence_ids}. "
                                     f"Use exact result-relative ID/path pairs such as: {allowed_references}. "
                                     "Never reference receipt metadata such as /limitations.") from error
            if role == "work-order" and validated_report.get("decision") == "no_draft" and not any(
                item.work_coverage_equipment_ids for item in self.evidence.values()
            ):
                raise ToolInputError(
                    "A no-draft decision requires actual complete work-source coverage. "
                    "Read the selected equipment's full open-work inventory, then retry the completion."
                )
            if role == "work-order" and any(item.tool == "propose_work_order" for item in self.evidence.values()):
                raise SourceFailure("Fixer cannot both stage a proposal and complete a no-draft review.")
            completed_report = validated_report
            return completed_report

        callbacks = {
            "hydro_query": query,
            "complete_rca_assessment": report,
            "complete_work_order_review": report,
        }
        functions = []
        for definition in version.tools:
            name = definition.get("name")
            if definition.get("type") != "function":
                continue
            if not isinstance(name, str) or name not in callbacks or not isinstance(definition.get("parameters"), dict):
                raise SourceFailure("Existing agent declared an unimplemented function.")
            callback = callbacks[name]
            parameters = definition["parameters"]
            if not isinstance(parameters, dict):
                raise SourceFailure("Existing function parameters are invalid.")

            def bind(selected_name: str, selected_callback):
                async def call(**kwargs):
                    return await guarded(selected_name, selected_callback, **kwargs)
                return call

            functions.append(FunctionTool(name=name, func=bind(name, callback), input_model=parameters))
        proposal_authorized = requests_new_work_order_proposal(request.question)
        backlog_contract = await self.bridge.call({
            "action": "work_backlog_group", "question": request.question,
        })
        if set(backlog_contract) != {"group_by"} or backlog_contract["group_by"] not in (
            None, "facility", "equipment",
        ):
            raise SourceFailure("Grouped-backlog intent returned an unsupported contract.")
        required_backlog_group = backlog_contract["group_by"]
        permitted_catalog = SourceCatalog.model_validate(journal.read("tool_catalog"))
        if not proposal_authorized:
            permitted_catalog = permitted_catalog.model_copy(update={
                "tools": tuple(tool for tool in permitted_catalog.tools if tool.name != "propose_work_order"),
            })
        context = {
            "assignment": question, "operator_question": request.question,
            "historical_context": (request.historical_context.model_dump(mode="json")
                                   if request.historical_context else None),
            "historical_context_policy": "Historical display is only conversational context, not current evidence. "
                                         "Execute fresh reads for all current facts and work proposals. "
                                         "Never reuse a prior approval or infer human consent from conversation. "
                                         "A prior draft does not authorize staging another proposal.",
            "source": request.source.model_dump(mode="json"),
            "resolved_scope": request.resolved_scope.model_dump(mode="json") if request.resolved_scope else None,
            "resolved_scope_policy": (
                "The backend resolved these operator mentions against the complete authoritative asset/facility "
                "inventories. Use only these canonical IDs for source filters, handoffs and draft targets. Do not "
                "reinterpret the original alias. Empty means the operator supplied no explicit identity mention."
            ),
            "selected_equipment_ids": sorted(self.selected_equipment_ids),
            "selected_equipment_policy": (
                "When nonempty, these IDs are the backend-computed focus selected from verified source evidence "
                "using the operator's metric and tie-break rule. Restrict this dependent assignment and any draft "
                "target to these IDs. Do not recompute or broaden the selection."
            ),
            "evidence": self.model_evidence(role, targeted=targeted_context),
            "allowed_evidence_ids": list(self.evidence),
            "allowed_evidence_references": rca_reference_examples(self.evidence) if role == "rca" else (),
            "completed_specialists": self.model_specialists(role, compact=targeted_context),
            "output_policy": "Tables by default. Values must be source references, never authored cells. "
                             "BAD is signal quality, not a physical diagnosis. Cause remains undetermined. "
                             "Only an in-memory proposal is permitted; no SQL writes or dispatch.",
            "charts_requested": request.charts_requested, "native_source": native_source,
            "required_specialists": request.required_specialists,
            "required_specialist_steps": request.required_specialist_steps,
            "conditional_maintenance_gate_requested": maintenance_gate_required,
            "required_source_capability": (
                {
                    "tool": "query_work_backlog",
                    "arguments": {"group_by": required_backlog_group},
                    "reason": (
                        "The operator requested a complete grouped open-work reconciliation and chart. Use this "
                        "authoritative capability instead of reconstructing joins or counts from separate reads."
                    ),
                }
                if role == "qa" and required_backlog_group is not None
                else None
            ),
            "permitted_source_catalog": permitted_catalog.model_dump(mode="json"),
            "proposal_authorization": (
                "This current turn explicitly requests a new editable proposal; propose_work_order may be called "
                "only after fresh complete work coverage and canonical equipment verification."
                if proposal_authorized
                else "This current turn does not request a new proposal. Do not call propose_work_order. Any draft "
                     "mentioned in historical context is read-only and must remain separate from current evidence."
            ),
            "completion_constraints": {
                "complete_work_order_review": {"exact_fields": ["decision", "reason"], "reason_max_characters": 1000,
                                               "decisions": ["no_draft", "needs_clarification"]},
                "complete_rca_assessment": {"references": "Use an exact value from allowed_evidence_ids. JSON pointers "
                                                           "resolve inside that receipt's result object, without a "
                                                           "/result prefix. Prefer an exact pair from "
                                                           "allowed_evidence_references. Never reference receipt "
                                                           "metadata such as /limitations.",
                                          "hypotheses": "Two to four distinct categories; no prose diagnosis fields."},
            },
            "trusted_operation_skills": operation_skill_guidance(role),
        }
        if self.carried_ids:
            context["carried_evidence"] = [
                {"id": item.id, "tool": item.tool, "read_at_utc": item.completed_at.isoformat()}
                for identity in sorted(self.carried_ids) if (item := self.evidence.get(identity)) is not None
            ]
            context["carried_evidence_policy"] = (
                "The receipts listed in carried_evidence were read earlier in this conversation (read times above) "
                "and are the dataset the operator is referring to. Treat them as the current condition evidence: do not "
                "re-read, refresh or re-query signal quality, temperature or the complete work-order list, because a "
                "second read would describe a different moment and contradict the operator's dataset. Read only what "
                "a draft or no-draft decision still needs, which is fresh complete open-work coverage for the "
                "equipment you choose."
            )
        if targeted_context:
            context["evidence_scope"] = (
                "Role-relevant projection for bounded independent verification. Complete immutable receipts remain "
                "in the audit; verify only supplied source rows and structured specialist reports without rereading."
            )
        if role == "work-order":
            verified_gate: dict[str, object] | None = None
            for specialist in reversed(self.specialists):
                report_value = specialist.report
                if specialist.role != "rca" or not isinstance(report_value, dict):
                    continue
                gate_value = report_value.get("maintenance_follow_up")
                if isinstance(gate_value, dict) and gate_value.get("decision") == "verified_uncovered_issue":
                    verified_gate = gate_value
                    break
            if verified_gate is not None:
                context["conditional_maintenance_gate"] = verified_gate
                context["work_order_execution"] = (
                    "The backend has already validated this source-referenced uncovered-issue gate and complete "
                    "work coverage is present in the supplied evidence. Do not repeat the investigation or reread "
                    "unchanged sources. Immediately call propose_work_order once for the verified equipment and "
                    "uncovered purpose, using a factual editable title and description with no invented diagnosis."
                )
        if len(_encoded(context)) > 524288:
            raise SourceFailure("Combined evidence exceeds the bounded model-context size.")

        @function_middleware
        async def finish_assessment(
            invocation: FunctionInvocationContext, call_next: Callable[[], Awaitable[None]],
        ) -> None:
            await call_next()
            self.healthy(request)
            if ((completed_report is not None and invocation.function.name in (
                    "complete_rca_assessment", "complete_work_order_review"))
                    or invocation.function.name == "propose_work_order"):
                raise MiddlewareTermination("The grounded structured assessment is complete.")

        service_timeout = (
            FOUNDRY_NATIVE_CALL_TIMEOUT_SECONDS if role == "fabric-iq"
            else FOUNDRY_SERVICE_CALL_TIMEOUT_SECONDS
        )
        project_client = BoundedAIProjectClient(
            endpoint=self.endpoint, credential=self.credential, request_timeout=service_timeout,
        )
        agent = FoundryAgent(
            project_client=project_client, agent_name=version.name, agent_version=version.version,
            tools=functions, timeout=service_timeout,
            middleware=[finish_assessment],
            function_invocation_configuration={
                "max_iterations": 6,
                "max_function_calls": 8,
                "max_consecutive_errors_per_request": 2,
                "terminate_on_unknown_calls": True, "include_detailed_errors": False,
                "allow_concurrent_invocation": role != "supervisor",
            },
        )
        started = perf_counter()
        async with project_client, agent:
            options: ChatOptions = {
                "store": False,
                "max_tokens": 4096,
            }
            active_context = context
            response: AgentResponse | None = None
            for completion_attempt in range(2):
                input_repairs.clear()
                try:
                    response = await agent.run(json.dumps(active_context), options=options)
                except AgentFrameworkException as error:
                    logger.error("Foundry %s invocation failed (%s).", role, safe_exception_signature(error))
                    transient_failure = transient_native_failure(error) if role == "fabric-iq" else None
                    if (
                        transient_failure is not None
                        and completion_attempt == 0
                    ):
                        logger.warning(
                            "Foundry native invocation had a transient %s before a completed receipt; retrying the "
                            "same selected source once.",
                            transient_failure,
                        )
                        active_context = {
                            **context,
                            "native_timeout_recovery": (
                                "The prior selected-source invocation ended with a transient upstream transport "
                                "failure before returning a completed receipt. Retry exactly the same assigned "
                                "native-source request once. Do not use another connection, broaden the scope, or "
                                "substitute direct tools."
                            ),
                        }
                        continue
                    self.healthy(request)
                    raise
                except APIStatusError as error:
                    raise SourceFailure(f"Foundry {role} invocation failed (HTTP {error.status_code}).") from error
                except APITimeoutError as error:
                    raise SourceFailure(f"Foundry {role} invocation exceeded an upstream service deadline.") from error
                self.healthy(request)
                if not response.response_id or response.finish_reason == "length":
                    if completion_attempt == 0 and role != "supervisor":
                        logger.warning(
                            "Foundry %s returned an incomplete completion; requesting one bounded evidence-only "
                            "completion repair.",
                            role,
                        )
                        active_context = {
                            **context,
                            "evidence": self.model_evidence(role, targeted=True),
                            "completed_specialists": self.model_specialists(role, compact=True),
                            "completion_repair": (
                                "The prior completion was incomplete after its source calls. Use only the captured "
                                "immutable evidence below and finish the assigned factual response now. Do not call "
                                "another source tool, broaden scope, or repeat completed work."
                            ),
                        }
                        continue
                    raise SourceFailure("Foundry response has no completed, inspectable service identity.")
                if response.finish_reason == "content_filter":
                    raise SourceFailure("Foundry response has no completed, inspectable service identity.")
                for message in response.messages:
                    if any(content.type in ("error", "function_approval_request") for content in message.contents):
                        raise SourceFailure("Foundry execution returned an error or unresolved approval request.")
                self.invocation_inputs[response.response_id] = sha256(_encoded(active_context)).hexdigest()
                backlog_complete = required_backlog_group is None or any(
                    item.tool == "query_work_backlog"
                    and item.arguments.get("group_by") in (
                        required_backlog_group,
                        f"{required_backlog_group}_id",
                    )
                    for item in self.evidence.values()
                )
                if role == "qa" and not backlog_complete:
                    if completion_attempt == 0:
                        logger.warning(
                            "Foundry QA omitted the required grouped work-backlog capability; requesting one bounded "
                            "same-scope correction."
                        )
                        active_context = {
                            **context,
                            "evidence": self.model_evidence(role),
                            "completed_specialists": self.model_specialists(role),
                            "source_capability_repair": (
                                f"The prior response omitted the required query_work_backlog call with "
                                f"group_by={required_backlog_group}. Call that exact capability now and finish from "
                                "its receipt. Do not reconstruct joins or counts from separate reads, broaden scope, "
                                "or repeat already completed source calls."
                            ),
                        }
                        continue
                    raise SourceFailure(
                        f"Required complete {required_backlog_group}-grouped work backlog was not returned."
                    )
                if role == "fabric-iq":
                    try:
                        native_receipts = [
                            content.raw_representation.model_dump(mode="json")
                            for message in response.messages for content in message.contents
                            if content.type == "mcp_server_tool_call" and content.raw_representation is not None
                        ]
                        if not native_receipts:
                            raise SourceFailure("Native execution has no inspectable service receipt.")
                        native_equipment_ids: set[str] = set()
                        for raw in native_receipts:
                            await self.bridge.call({
                                "action": "validate_native_receipt", "receipt": raw, "source": native_source,
                            })
                            native_equipment_ids.update(
                                _native_equipment_ids(_validated_native_result(raw.get("output")))
                            )
                        required_native_equipment_ids = initial_equipment_selection(request)
                        if (
                            required_native_equipment_ids
                            and not required_native_equipment_ids.issubset(native_equipment_ids)
                        ):
                            raise SourceFailure(
                                "Native identity coverage missing: the selected source did not return the resolved "
                                "equipment instance requested by the operator."
                            )
                    except SourceFailure as error:
                        source_mismatch = str(error).startswith("Native source mismatch:")
                        source_refusal = str(error).startswith("Native source refusal:")
                        identity_missing = str(error).startswith("Native identity coverage missing:")
                        if completion_attempt == 0 and (source_mismatch or source_refusal or identity_missing):
                            logger.warning(
                                "Foundry native agent returned %s; requesting one bounded same-source correction.",
                                "the wrong connection" if source_mismatch
                                else "a moderation/refusal payload" if source_refusal
                                else "incomplete requested-identity coverage",
                            )
                            active_context = {
                                **context,
                                "native_source_repair": (
                                    (
                                        "The prior call used the wrong native connection. "
                                        if source_mismatch
                                        else (
                                            "The prior selected-source call returned moderation/refusal text "
                                            "instead of domain evidence. "
                                            if source_refusal
                                            else "The prior selected-source result omitted the operator's resolved "
                                                 "equipment instance. Return actual instance values and relationships "
                                                 "for every requested resolved equipment ID. "
                                        )
                                    )
                                    + f"Call only fabriciq-{native_source}, which is the backend-selected source for "
                                      "this step. Retry the same bounded factual request without broadening scope or "
                                      "substituting another connection. Do not return prose without a successful "
                                      "selected-source receipt containing domain evidence."
                                ),
                            }
                            continue
                        raise
                    break
                proposal_staged = role == "work-order" and any(
                    item.tool == "propose_work_order" for item in self.evidence.values()
                )
                if role not in ("rca", "work-order") or completed_report is not None or proposal_staged:
                    break
                if completion_attempt == 1:
                    break
                logger.warning("Foundry %s omitted its structured completion; requesting one bounded repair.", role)
                active_context = {
                    **context,
                    "evidence": self.model_evidence(role),
                    "allowed_evidence_ids": list(self.evidence),
                    "allowed_evidence_references": (
                        rca_reference_examples(self.evidence) if role == "rca" else ()
                    ),
                    "completed_specialists": self.model_specialists(role),
                    "completion_repair": (
                        "The prior response was not accepted because it omitted the required structured completion. "
                        "Use the captured evidence and call the role's completion function now. Do not return prose, "
                        "repeat the investigation, or claim a result without the function call."
                    ),
                }
            self.healthy(request)
            if response is None:
                raise RuntimeError("Foundry invocation did not produce a response.")
            if role == "rca" and completed_report is None:
                raise SourceFailure("Sleuth returned prose without its required grounded assessment.")
            if role == "fabric-iq":
                found = set()
                for message in response.messages:
                    for content in message.contents:
                        if content.type != "mcp_server_tool_call":
                            continue
                        representation = content.raw_representation
                        if representation is None:
                            raise SourceFailure("Native execution has no inspectable service receipt.")
                        raw = representation.model_dump(mode="json")
                        await self.bridge.call({"action": "validate_native_receipt",
                                                "receipt": raw, "source": native_source})
                        if raw["id"] in found:
                            continue
                        found.add(raw["id"])
                        native_result = _validated_native_result(raw.get("output"))
                        native_units, native_presentation = _native_count_chart_metadata(native_result)
                        if (
                            request.charts_requested
                            and native_presentation is not None
                            and not isinstance(native_result.get("presentation"), dict)
                        ):
                            native_result["presentation"] = native_presentation
                        native_text = next((
                            value for key in ("response", "native_text")
                            if isinstance((value := native_result.get(key)), str) and len(value) > 2400
                        ), None)
                        if native_text is not None:
                            native_result["citation_fragments"] = list(_bounded_citation_fragments(native_text))
                        native_fields = tuple(
                            key for key, value in native_result.items()
                            if isinstance(value, list)
                            and all(isinstance(item, dict) for item in value)
                        )
                        native_group_by: Literal["equipment_id", "facility_id"] | None = None
                        if native_presentation is not None:
                            chart_value = native_presentation.get("chart")
                            x_key = chart_value.get("x_key") if isinstance(chart_value, dict) else None
                            if x_key in ("equipment_id", "equipmentId"):
                                native_group_by = "equipment_id"
                            elif x_key in ("facility_id", "facilityId", "Station"):
                                native_group_by = "facility_id"
                        native_scopes: set[Literal["population", "focus"]] = {
                            item.scope for item in self.answer_contract.requirements
                            if item.kind == "native_claims" and item.authority == "native"
                        } if self.answer_contract is not None else set()
                        receipt = ToolEvidence(id=raw["id"], source=request.source, tool=f"fabriciq-{native_source}",
                                               arguments={"assignment": question}, completed_at=utc_now(),
                                               result=native_result,
                                               resolved_equipment_ids=tuple(sorted(_native_equipment_ids(native_result))),
                                               column_units=native_units,
                                               datasets=tuple(
                                                   EvidenceDataset(
                                                       kind="native_claims", field=field, authority="native",
                                                       scope=scope, complete=True,
                                                       identity_fields=("source_identity",),
                                                       group_by=(
                                                           native_group_by
                                                           if native_presentation is not None
                                                           and native_presentation.get("table_field") == field
                                                           else None
                                                       ),
                                                       role="primary" if index == 0 else "support",
                                                   )
                                                   for scope in sorted(native_scopes)
                                                   for index, field in enumerate(native_fields)
                                               ),
                                               limitations=("Native connection receipt does not attest SQL/combined-source provenance.",))
                        self.evidence[receipt.id] = receipt
                if not found:
                    raise SourceFailure("Native specialist returned prose without a successful selected-source receipt.")
            if role == "work-order" and completed_report is None and not any(
                item.tool == "propose_work_order" for item in self.evidence.values()
            ):
                raise SourceFailure("Fixer returned neither a staged approval card nor a structured review.")
            return response, completed_report, (perf_counter() - started) * 1000

    def contract_answer_plan(self, request: ChatRequest) -> AnswerPlan:
        contract = self.answer_contract
        if contract is None:
            raise SourceFailure("Presentation requires the Supervisor's validated typed answer contract.")
        tables: list[PlannedTable] = []
        charts: list[PlannedChart] = []
        limitations: list[str] = []
        summary_parts: list[str] = []
        for requirement in contract.requirements:
            matches = [
                dataset
                for receipt in self.evidence.values()
                for dataset in receipt.datasets
                if (
                    dataset.kind == requirement.kind
                    and dataset.scope == requirement.scope
                    and dataset.authority == requirement.authority
                    and dataset.role != "audit"
                )
            ]
            if not matches:
                if requirement.scope == "focus" and not self.selected_equipment_ids:
                    limitations.append(
                        f"{requirement.kind} focus evidence was not read because no equipment was selected."
                    )
                    continue
                raise SourceFailure(
                    f"Required {requirement.authority} {requirement.kind} "
                    f"{requirement.scope} evidence was not produced."
                )
            if not any(dataset.complete for dataset in matches):
                raise SourceFailure(
                    f"Required {requirement.authority} {requirement.kind} evidence is incomplete."
                )
        for view in contract.views:
            if view.authority == "reconciled":
                requirements = tuple(
                    item for item in contract.requirements if item.scope == view.scope
                )
                source_matches = [
                    (receipt, dataset)
                    for receipt in self.evidence.values()
                    for dataset in receipt.datasets
                    if dataset.role != "audit" and any(
                        dataset.kind == item.kind
                        and dataset.scope == item.scope
                        and dataset.authority == item.authority
                        for item in requirements
                    )
                ]
                authorities = {dataset.authority for _receipt, dataset in source_matches}
                if not {"direct", "native"}.issubset(authorities):
                    raise SourceFailure(
                        f"Reconciliation view {view.id} requires complete direct and native evidence."
                    )
                reconciled_rows: list[dict[str, object]] = []
                reconciled_limitations: list[str] = [
                    "Reconciliation preserves source rows and identities; it does not infer matches "
                    "between records with different governed identity fields."
                ]
                for receipt, dataset in source_matches:
                    records = receipt.result.get(dataset.field)
                    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
                        raise SourceFailure(
                            f"Governed dataset {dataset.kind} does not expose its declared record collection."
                        )
                    for index, record in enumerate(records):
                        reconciled_rows.append({
                            **record,
                            "_source_authority": dataset.authority,
                            "_source_dataset": dataset.kind,
                            "_source_evidence_id": receipt.id,
                            "_source_path": f"/{dataset.field}/{index}",
                        })
                    reconciled_limitations.extend(receipt.limitations)
                    reconciled_limitations.extend(dataset.limitations)
                derived = ToolEvidence(
                    id=f"reconciliation:{view.id}",
                    source=request.source,
                    tool="reconcile_evidence",
                    arguments={"view_id": view.id},
                    completed_at=max(receipt.completed_at for receipt, _dataset in source_matches),
                    result={"reconciliation_rows": reconciled_rows},
                    limitations=tuple(dict.fromkeys(reconciled_limitations))[:8],
                    datasets=(EvidenceDataset(
                        kind=view.kind,
                        field="reconciliation_rows",
                        authority="reconciled",
                        scope=view.scope,
                        complete=True,
                        identity_fields=("_source_evidence_id", "_source_path"),
                    ),),
                )
                existing = self.evidence.get(derived.id)
                if existing is not None and existing != derived:
                    raise SourceFailure(
                        f"Reconciliation view {view.id} conflicts with its immutable derived evidence."
                    )
                self.evidence[derived.id] = existing or derived
                derived = self.evidence[derived.id]
                matches = [(derived, derived.datasets[0])]
            else:
                matches = [
                    (receipt, dataset)
                    for receipt in self.evidence.values()
                    for dataset in receipt.datasets
                    if (
                        dataset.kind == view.kind
                        and dataset.scope == view.scope
                        and dataset.authority == view.authority
                        and dataset.role != "audit"
                    )
                ]
            if not matches:
                if view.scope == "focus" and not self.selected_equipment_ids:
                    limitations.append(
                        f"{view.title} was omitted because the population evidence selected no equipment."
                    )
                    continue
                raise SourceFailure(
                    f"Required answer view {view.id} has no governed {view.kind} evidence."
                )
            if not any(dataset.complete for _receipt, dataset in matches):
                raise SourceFailure(f"Required answer view {view.id} has no complete source result.")
            if view.mode == "grouped" and any(
                dataset.group_by != view.group_by for _receipt, dataset in matches
            ):
                raise SourceFailure(
                    f"Required grouped answer view {view.id} does not match the governed grouping."
                )
            source_rows: list[tuple[ToolEvidence, EvidenceDataset, int, dict[str, object]]] = []
            seen: set[tuple[str, ...]] = set()
            for receipt, dataset in matches:
                records = receipt.result.get(dataset.field)
                if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
                    raise SourceFailure(
                        f"Governed dataset {dataset.kind} does not expose its declared record collection."
                    )
                for index, record in enumerate(records):
                    identity = tuple(str(record.get(key, "")) for key in dataset.identity_fields)
                    if any(identity):
                        if identity in seen:
                            continue
                        seen.add(identity)
                    source_rows.append((receipt, dataset, index, record))
                limitations.extend(item for item in receipt.limitations if item not in limitations)
                limitations.extend(item for item in dataset.limitations if item not in limitations)
            scalar_keys: list[str] = []
            for _receipt, _dataset, _index, record in source_rows:
                for key in record:
                    values_for_key = [
                        candidate[key]
                        for _other_receipt, _other_dataset, _other_index, candidate in source_rows
                        if key in candidate
                    ]
                    if key not in scalar_keys and all(
                        value is None
                        or type(value) in (int, float)
                        or isinstance(value, str) and len(value) <= 2400
                        for value in values_for_key
                    ):
                        scalar_keys.append(key)
            if not scalar_keys:
                scalar_keys = list(matches[0][1].identity_fields)
            columns = tuple(AnswerColumn(
                key=key,
                label=key,
                kind=(
                    "timestamp" if key.endswith("_time") or key.endswith("At")
                    else "number" if source_rows and all(
                        row.get(key) is None or type(row.get(key)) in (int, float)
                        for _receipt, _dataset, _index, row in source_rows
                    ) and any(
                        type(row.get(key)) in (int, float)
                        for _receipt, _dataset, _index, row in source_rows
                    )
                    else "text"
                ),
            ) for key in scalar_keys[:40])
            rows_list = []
            omitted_non_scalar_rows = 0
            for receipt, dataset, index, record in source_rows[:500]:
                cells = tuple(
                    AnswerCell(
                        key=column.key,
                        source=EvidenceReference(
                            evidence_id=receipt.id,
                            path=f"/{dataset.field.replace('~', '~0').replace('/', '~1')}/{index}/"
                                 f"{column.key.replace('~', '~0').replace('/', '~1')}",
                        ),
                    )
                    for column in columns if column.key in record
                )
                if cells:
                    rows_list.append(PlannedRow(cells=cells))
                else:
                    omitted_non_scalar_rows += 1
            rows = tuple(rows_list)
            if omitted_non_scalar_rows:
                limitations.append(
                    f"{view.title}: {omitted_non_scalar_rows} nested-only source record(s) remain available "
                    "in the audit but were omitted from the scalar answer table."
                )
            table = PlannedTable(id=view.id, title=view.title, columns=columns, rows=rows)
            tables.append(table)
            if not source_rows:
                summary_parts.append(f"{view.title}: no matching records.")
            elif view.mode == "grouped":
                numeric = [
                    (key, value)
                    for _receipt, _dataset, _index, record in source_rows
                    for key, value in record.items()
                    if type(value) in (int, float)
                ]
                if numeric:
                    measure = numeric[0][0]
                    def numeric_measure(
                        item: tuple[ToolEvidence, EvidenceDataset, int, dict[str, object]],
                    ) -> float:
                        value = item[3].get(measure)
                        return float(cast(int | float, value)) if type(value) in (int, float) else float("-inf")

                    leader = max(source_rows, key=numeric_measure)
                    group = leader[3].get(view.group_by or "")
                    summary_parts.append(
                        f"{view.title}: {group} has the highest {measure} "
                        f"({leader[3].get(measure)}) across {len(source_rows)} groups."
                    )
                else:
                    summary_parts.append(f"{view.title}: {len(source_rows)} groups returned.")
            else:
                if len(source_rows) == 1:
                    detail = ", ".join(
                        f"{key}={value}"
                        for key, value in source_rows[0][3].items()
                        if value is not None and type(value) in (str, int, float)
                    )
                    summary_parts.append(f"{view.title}: {detail}.")
                else:
                    summary_parts.append(f"{view.title}: {len(source_rows)} verified records returned.")
            if view.chart is not None:
                x_candidates = [
                    view.group_by,
                    *(field for field in matches[0][1].identity_fields),
                    *(column.key for column in columns if column.kind in ("text", "timestamp")),
                ]
                x_key = next((key for key in x_candidates if key and key in scalar_keys), None)
                unit_keys = {
                    key for receipt, _dataset in matches for key in receipt.column_units
                }
                candidate_y_keys = tuple(
                    column.key for column in columns
                    if column.kind == "number" and column.key in unit_keys
                )
                preferred_measures = (
                    "open_work_orders", "open_work_order_count", "record_count", "work_order_count", "count",
                )
                primary_y = next(
                    (key for key in preferred_measures if key in candidate_y_keys),
                    candidate_y_keys[0] if candidate_y_keys else None,
                )
                primary_units = {
                    receipt.column_units[primary_y]
                    for receipt, _dataset in matches
                    if primary_y is not None and primary_y in receipt.column_units
                }
                y_keys = tuple(
                    key for key in candidate_y_keys
                    if {
                        receipt.column_units[key]
                        for receipt, _dataset in matches
                        if key in receipt.column_units
                    } == primary_units
                )[:4]
                if x_key is None or not y_keys:
                    raise SourceFailure(
                        f"Requested chart for answer view {view.id} lacks governed axes."
                    )
                charts.append(PlannedChart(
                    table_id=view.id, kind=view.chart, x_key=x_key, y_keys=y_keys,
                ))
        return AnswerPlan(
            summary=(
                "No equipment could be selected from the complete qualifying population. "
                if (
                    not self.selected_equipment_ids
                    and self.orchestration_plan is not None
                    and any(step.requires_selection for step in self.orchestration_plan.steps)
                ) else ""
            ) + " ".join(summary_parts),
            tables=tuple(tables),
            charts=tuple(charts),
            limitations=tuple(limitations[:20]),
        )

    def open_work_by_equipment(self) -> dict[str, list[dict[str, object]]]:
        found: dict[str, dict[str, dict[str, object]]] = {}
        for receipt in self.evidence.values():
            for key in ("rows", "open_work_orders", "existing_work"):
                rows = receipt.result.get(key)
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    number = row.get("workOrderNumber", row.get("work_order_number"))
                    equipment = row.get("equipmentId", row.get("equipment_id"))
                    if not isinstance(number, str) or not isinstance(equipment, str):
                        continue
                    if str(row.get("status", "")).strip().lower() in CLOSED_WORK_STATUSES:
                        continue
                    found.setdefault(equipment, {})[number] = {
                        "work_order": number, "title": row.get("title"), "priority": row.get("priority"),
                        "status": row.get("status"), "instrument": row.get("instrumentId", row.get("instrument_id")),
                    }
        return {equipment: list(orders.values()) for equipment, orders in sorted(found.items())}

    def analysis_context(self, request: ChatRequest, answer: ChatAnswer) -> dict[str, object]:
        datasets = [{
            "title": table.title,
            "columns": {column.key: column.label for column in table.columns},
            "row_count": len(table.rows),
            "rows_truncated": len(table.rows) > ANALYSIS_MAX_ROWS_PER_TABLE,
            "rows": [dict(row.values) for row in table.rows[:ANALYSIS_MAX_ROWS_PER_TABLE]],
        } for table in answer.tables]
        notes = []
        for item in answer.specialists:
            if item.role not in ("rca", "work-order", "fabric-iq"):
                continue
            notes.append({
                "role": item.role, "unverified_note": item.text[:1500],
                "report": json.loads(json.dumps(item.report, default=str))
                if item.report is not None and len(json.dumps(item.report, default=str)) <= 4000 else None,
            })
        drafts = []
        for draft in answer.proposals:
            value = draft.model_dump(mode="json")
            drafts.append({key: value[key] for key in (
                "equipment_id", "title", "description", "priority", "instrument_id", "opcua_node_id",
            ) if key in value})
        return {
            "operator_question": request.question,
            "now_utc": utc_now().isoformat(),
            "datasets": datasets,
            "open_work_by_equipment": self.open_work_by_equipment(),
            "source_limitations": list(answer.limitations),
            "specialist_notes": notes,
            "staged_drafts": drafts,
            "carried_evidence": [
                {"tool": item.tool, "read_at_utc": item.completed_at.isoformat()}
                for identity in sorted(self.carried_ids) if (item := self.evidence.get(identity)) is not None
            ],
        }

    def analysis_violations(self, report: AnalysisReport, context_text: str) -> list[str]:
        allowed = set(ANALYSIS_ID_PATTERN.findall(context_text))
        known_numbers = [float(value) for value in ANALYSIS_DECIMAL_PATTERN.findall(context_text)]
        open_work = self.open_work_by_equipment()
        statements = [report.answer, *report.findings, *report.reasoning, *report.caveats]
        for item in report.recommendations:
            statements.extend((item.title, item.justification))
        violations: list[str] = []
        for statement in statements:
            for identifier in ANALYSIS_ID_PATTERN.findall(statement):
                if identifier not in allowed:
                    violations.append(f"{identifier} is not in the supplied data")
            for raw in ANALYSIS_DECIMAL_PATTERN.findall(statement):
                if not any(abs(float(raw) - known) <= 0.051 for known in known_numbers):
                    violations.append(f"the number {raw} is not in the supplied data")
        for item in report.recommendations:
            if item.equipment_id is not None and item.equipment_id not in allowed:
                violations.append(f"recommendation equipment {item.equipment_id} is not in the supplied data")
            covering = {str(entry["work_order"]) for entry in open_work.get(item.equipment_id or "", [])}
            cited = set(ANALYSIS_ID_PATTERN.findall(item.justification))
            if item.action == "covered_by_open_work" and not covering.intersection(cited):
                violations.append(
                    f"covered_by_open_work for {item.equipment_id} must cite one of its open work orders "
                    f"{sorted(covering) or 'none exist'}")
            if item.action == "create_work_order" and covering and not covering.intersection(cited):
                violations.append(
                    f"create_work_order for {item.equipment_id} must explain why open work "
                    f"{sorted(covering)} does not already cover it")
        return list(dict.fromkeys(violations))[:8]

    async def analysis_for(
        self, request: ChatRequest, answer: ChatAnswer, journal: RunJournal,
    ) -> AnalysisReport | None:
        saved = journal.read("answer_analysis")
        if saved is not None:
            return AnalysisReport.model_validate(saved["analysis"])
        if not self.analysis_model or not answer.tables:
            return None
        context_text = json.dumps(
            self.analysis_context(request, answer), sort_keys=True, default=str, ensure_ascii=False,
        )
        if len(context_text) > 200000:
            logger.warning("Analysis context exceeds its bounded size; the grounded narrative is skipped.")
            return None
        started = perf_counter()
        feedback: str | None = None
        self.analysis_rejection = None
        try:
            async with asyncio.timeout(ANALYSIS_ATTEMPTS * ANALYSIS_TIMEOUT_SECONDS + 5):
                async with BoundedAIProjectClient(
                    endpoint=self.endpoint, credential=self.credential, request_timeout=ANALYSIS_TIMEOUT_SECONDS,
                ) as project_client:
                    openai_client = project_client.get_openai_client()
                    for _attempt in range(ANALYSIS_ATTEMPTS):
                        self.healthy(request)
                        response = await openai_client.responses.create(
                            model=self.analysis_model, instructions=ANALYST_INSTRUCTIONS,
                            input=context_text if feedback is None else (
                                f"{context_text}\n\nYour previous briefing was rejected: {feedback}. "
                                "Rewrite it using only values present in the input, and keep every other part "
                                "of the briefing."),
                            max_output_tokens=3500, reasoning={"effort": "low"}, store=False,
                            text={"format": {"type": "json_schema", "name": "hydro_briefing",
                                             "schema": ANALYSIS_SCHEMA, "strict": True}},
                        )
                        if getattr(response, "status", "completed") != "completed":
                            feedback = "the response was incomplete; be more concise"
                            continue
                        try:
                            report = AnalysisReport.model_validate_json(response.output_text)
                        except ValueError as error:
                            feedback = f"it did not match the required structure ({type(error).__name__})"
                            continue
                        violations = self.analysis_violations(report, context_text)
                        if violations:
                            feedback = "; ".join(violations)
                            continue
                        journal.save("answer_analysis", {
                            "analysis": report.model_dump(mode="json"), "response_id": response.id,
                            "model": self.analysis_model,
                            "duration_ms": (perf_counter() - started) * 1000,
                            "input_digest": sha256(context_text.encode()).hexdigest(),
                        })
                        return report
        except SourceFailure:
            raise
        except (APIStatusError, APITimeoutError, AgentFrameworkException, TimeoutError, OSError, ValueError) as error:
            logger.error("Grounded analysis was unavailable (%s).", type(error).__name__)
            self.analysis_rejection = f"the briefing service was unavailable ({type(error).__name__})"
            return None
        logger.error("Grounded analysis was rejected after verification: %s", feedback)
        self.analysis_rejection = (feedback or "no acceptable briefing was returned")[:240]
        return None

    def answer(
        self, request: ChatRequest, plan: AnswerPlan, *, use_projected_summary: bool = False,
        analysis: AnalysisReport | None = None,
    ) -> ChatAnswer:
        if not self.evidence:
            raise SourceFailure("No executed source receipts exist; agent prose is not grounded data.")
        if plan.charts and not request.charts_requested:
            raise SourceFailure("Unrequested charts are not permitted.")
        chart_limitations: tuple[str, ...] = ()
        if request.charts_requested and not plan.charts:
            def has_chart_measure(value: object, units: dict[str, str], depth: int = 0) -> bool:
                if depth > 16:
                    raise SourceFailure("Source result exceeds the bounded chart-validation nesting depth.")
                if isinstance(value, dict):
                    for key, child in value.items():
                        unit = value.get("unit", value.get("Unit")) if key == "value" else None
                        unit = unit if isinstance(unit, str) and unit.strip() else units.get(key)
                        if type(child) in (int, float) and isinstance(unit, str) and unit.strip():
                            return True
                    return any(has_chart_measure(child, units, depth + 1) for child in value.values())
                if isinstance(value, list):
                    return any(has_chart_measure(child, units, depth + 1) for child in value)
                return False

            if any(has_chart_measure(receipt.result, receipt.column_units) for receipt in self.evidence.values()):
                raise SourceFailure("A chart was explicitly requested and verified numeric source units exist; "
                                    "the answer cannot silently omit the chart.")
            chart_limitations = ("A chart was requested, but the returned source receipts contain no numeric "
                                 "measure with verified units.",)
        tables = []
        measure_units: dict[str, dict[str, set[str]]] = {}
        for table in plan.tables:
            table_units: dict[str, set[str]] = {}
            rows = []
            column_keys = {column.key for column in table.columns}
            for row in table.rows:
                if not row.cells or len({cell.key for cell in row.cells}) != len(row.cells):
                    raise SourceFailure("Answer cells require distinct column keys.")
                if any(cell.key not in column_keys for cell in row.cells):
                    raise SourceFailure("Answer cells must match declared table columns.")
                values: dict[str, object] = {column.key: None for column in table.columns}
                identities = set()
                signal_identities = set()
                equipment_sources = set()
                signal_sources = set()
                source_rows: dict[str, set[str]] = {}
                for cell in row.cells:
                    receipt = self.evidence.get(cell.source.evidence_id)
                    if receipt is None:
                        raise SourceFailure("Answer references evidence that was not returned.")
                    value: object = receipt.result
                    source_unit = receipt.column_units.get(cell.key)
                    if re.search(r"~(?![01])", cell.source.path):
                        raise SourceFailure("Answer contains an invalid JSON pointer escape.")
                    if cell.source.path.rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~") != cell.key:
                        raise SourceFailure(f"Answer column key {cell.key!r} differs from its literal source field "
                                            f"{cell.source.path.rsplit('/', 1)[-1]!r}. Use literal source fields, not aliases.")
                    scope = cell.source.path.rsplit("/", 1)[0]
                    source_rows.setdefault(receipt.id, set()).add(scope)
                    for key, identity in receipt.row_identities.get(scope, {}).items():
                        if not identity.strip():
                            raise SourceFailure("Private source row identity is blank.")
                        if key in ("equipment_id", "equipmentId"):
                            identities.add(identity)
                            equipment_sources.add(receipt.id)
                        else:
                            signal_identities.add(identity)
                            signal_sources.add(receipt.id)
                    for encoded in cell.source.path.split("/")[1:]:
                        key = encoded.replace("~1", "/").replace("~0", "~")
                        if isinstance(value, dict) and key in value:
                            if cell.key == "value":
                                for unit_key in ("unit", "Unit"):
                                    unit = value.get(unit_key)
                                    if isinstance(unit, str):
                                        table_units.setdefault(cell.key, set()).add(unit)
                            for identity_key in ("equipment_id", "equipmentId"):
                                identity = value.get(identity_key)
                                if isinstance(identity, str) and identity.strip():
                                    identities.add(identity)
                                    equipment_sources.add(receipt.id)
                            for signal_key in ("opcua_node_id", "opcuaNodeId"):
                                signal_identity = value.get(signal_key)
                                if isinstance(signal_identity, str) and signal_identity.strip():
                                    signal_identities.add(signal_identity)
                                    signal_sources.add(receipt.id)
                            value = value[key]
                        elif isinstance(value, list) and key.isascii() and key.isdecimal() and int(key) < len(value):
                            value = value[int(key)]
                        else:
                            raise SourceFailure("Answer pointer does not resolve into its source receipt.")
                    if value is not None and type(value) not in (str, int, float):
                        raise SourceFailure("An answer cell must reference a scalar source value.")
                    if isinstance(value, str) and len(value) > 2400:
                        raise SourceFailure("Large technical source payloads belong in the audit, not an answer cell.")
                    values[cell.key] = value
                    if source_unit is not None:
                        table_units.setdefault(cell.key, set()).add(source_unit)
                if len(identities) > 1:
                    raise SourceFailure("Answer row mixed source fields from different equipment identities.")
                same_equipment_join = (
                    len(source_rows) > 1
                    and len(equipment_sources) == len(source_rows)
                    and len(identities) == 1
                )
                if (
                    len(signal_identities) > 1 and not same_equipment_join
                    or any(len(scopes) > 1 for scopes in source_rows.values())
                ):
                    raise SourceFailure("Answer row mixed fields from different source rows or signal identities.")
                if len(source_rows) > 1 and not (
                    same_equipment_join
                    or len(signal_sources) == len(source_rows) and len(signal_identities) == 1
                ):
                    raise SourceFailure("Cross-source answer rows require matching verified equipment or signal "
                                        "identities in every source row; projected-away identities cannot prove a join.")
                rows.append(AnswerRow.model_validate({"values": values, "source": row.cells[0].source}))
            columns = tuple(AnswerColumn(key=column.key, label=column.key, kind=column.kind) for column in table.columns)
            tables.append(AnswerTable(id=table.id, title=table.title,
                                      columns=columns, rows=tuple(rows)))
            measure_units[table.id] = table_units
        ids = {table.id for table in tables}
        if len(ids) != len(tables):
            raise SourceFailure("Answer table identities must be distinct.")
        for chart in plan.charts:
            table = next((item for item in tables if item.id == chart.table_id), None)
            if table is None:
                raise SourceFailure("Chart must reference an actual answer table.")
            columns = {column.key: column for column in table.columns}
            if chart.x_key not in columns or any(key not in columns or columns[key].kind != "number"
                                                 for key in chart.y_keys):
                raise SourceFailure("Chart axes do not match the typed source columns.")
            if len(set(chart.y_keys)) != len(chart.y_keys) or (
                chart.kind == "scatter" and columns[chart.x_key].kind != "number"
            ):
                raise SourceFailure("Chart requires distinct series and compatible typed axes.")
            units = [measure_units[table.id].get(key, set()) for key in chart.y_keys]
            if any(len(unit) != 1 or not all(value.strip() for value in unit) for unit in units) or len(set.union(*units)) != 1:
                raise SourceFailure("Chart measures require explicit, consistent source units; incompatible units cannot share an axis.")
        zero_result_limitations: list[str] = []
        empty_quality_snapshots = [
            item for item in self.evidence.values()
            if item.tool == "query_signal_quality_snapshot"
            and item.result.get("row_count") == 0
        ]
        for receipt in empty_quality_snapshots:
            population = receipt.result.get("population")
            if not isinstance(population, dict):
                continue
            expected = population.get("expected_signal_count")
            missing = population.get("signals_without_readings")
            if type(expected) is int and isinstance(missing, list):
                zero_result_limitations.append(
                    f"Telemetry population: {expected} expected signals; {len(missing)} had no reading "
                    "in the requested window, so no current/stale reading classification was possible."
                )
        limitations = tuple(dict.fromkeys([
            *self.carried_limitations(request),
            *(gap for item in self.evidence.values() for gap in item.limitations),
            *plan.limitations,
            *zero_result_limitations,
            *chart_limitations,
            *rca_answer_limitations(self.specialists),
        ]))
        row_count = sum(len(table.rows) for table in tables)
        write_reviewed = (
            any(item.role == "work-order" for item in self.specialists)
            or any(item.tool == "propose_work_order" for item in self.evidence.values())
        )
        summary = (
            "No matching signal-quality rows were returned; no equipment was selected and no production writes "
            "executed."
            if not use_projected_summary and not tables and empty_quality_snapshots
            else (
                with_production_write_status(plan.summary, write_reviewed)
                if use_projected_summary
                else with_production_write_status(
                    f"Returned {row_count} source rows in {len(tables)} tables.", write_reviewed,
                )
            )
        )
        proposals_by_id: dict[UUID, WorkOrderDraft] = {}
        for item in self.evidence.values():
            if item.tool != "propose_work_order":
                continue
            draft = WorkOrderDraft.model_validate(item.result["proposal"])
            previous = proposals_by_id.get(draft.id)
            if previous is not None and previous != draft:
                raise SourceFailure("Conflicting source receipts share an approval-card identity.")
            proposals_by_id[draft.id] = draft
        proposals = tuple(proposals_by_id.values())
        if any(draft.run_id != request.run_id or draft.source != request.source
               or draft.work_read_at < request.requested_at or draft.expires_at <= utc_now()
               for draft in proposals):
            raise SourceFailure("A staged approval card differs from this run or its fresh source coverage.")
        return ChatAnswer(run_id=request.run_id, source=request.source, requested_at=request.requested_at,
                          source_read_times=tuple(item.completed_at for item in self.evidence.values()), summary=summary,
                          tables=tuple(tables), charts=plan.charts, limitations=limitations,
                          cell_sources=plan.tables, specialists=tuple(self.specialists),
                          audit_url=f"/chat/runs/{request.run_id}/evidence", proposals=proposals,
                          proposal_digests={str(draft.id): draft.digest() for draft in proposals},
                          analysis=analysis)

    async def run(self, request: ChatRequest) -> ChatAnswer:
        try:
            return await self._run(request)
        finally:
            tasks = tuple(self.source_tasks.values())
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, request: ChatRequest) -> ChatAnswer:
        if request.source != self.source:
            raise SourceFailure("Request source differs from the verified deployment identity.")
        if self.run_id is not None and self.run_id != request.run_id:
            raise SourceFailure("Each run requires its own isolated Foundry execution owner.")
        self.run_id = request.run_id
        path = self.root / str(request.run_id)
        path.mkdir(parents=True, exist_ok=True)
        with FileLock(path / "run.lock", timeout=0):
            journal = RunJournal(path / "receipts")
            encoded = request.model_dump(mode="json")
            existing = journal.read("request")
            if existing is not None and existing != encoded:
                raise SourceFailure("Run identity already belongs to a different immutable request.")
            journal.save("request", encoded)
            if (
                not request.required_specialists
                or not request.required_evidence_kinds
                or not request.permitted_evidence_kinds
            ):
                intent = await self.bridge.call({"action": "chat_intent", "question": request.question})
                if set(intent) != {
                    "charts_requested", "native_sources", "proposal_priority", "equipment_selection",
                    "required_evidence_kinds", "permitted_evidence_kinds", "required_specialists",
                    "required_specialist_steps",
                }:
                    raise SourceFailure("Executor intent recovery returned an unsupported contract.")
                parsed_intent = ChatRequest.model_validate({
                    **request.model_dump(),
                    **intent,
                })
                if (parsed_intent.required_specialists
                        and parsed_intent.native_sources == request.native_sources):
                    recovered = request.model_copy(update={
                        "required_specialists": (
                            request.required_specialists or parsed_intent.required_specialists
                        ),
                        "required_specialist_steps": (
                            request.required_specialist_steps or parsed_intent.required_specialist_steps
                        ),
                        "required_evidence_kinds": (
                            request.required_evidence_kinds or parsed_intent.required_evidence_kinds
                        ),
                        "permitted_evidence_kinds": (
                            request.permitted_evidence_kinds or parsed_intent.permitted_evidence_kinds
                        ),
                        "equipment_selection": (
                            request.equipment_selection or parsed_intent.equipment_selection
                        ),
                        "charts_requested": request.charts_requested or parsed_intent.charts_requested,
                        "proposal_priority": parsed_intent.proposal_priority,
                    })
                    logger.warning(
                        "Recovered a missing backend specialist manifest at the executor boundary: %s.",
                        list(recovered.required_specialists),
                    )
                    request = recovered
                    journal.save("executor_intent", {
                        "required_specialists": list(request.required_specialists),
                        "required_specialist_steps": request.required_specialist_steps,
                        "native_sources": list(request.native_sources),
                    })
            committed = journal.read("answer")
            if committed is not None:
                return ChatAnswer.model_validate(committed)
            self.healthy(request)
            versions = await self.versions(journal)
            self.emit_event(
                f"{request.run_id}:chief", "supervisor", "running", "Supervisor",
                "Compiling the governed request, evidence, and presentation contract.",
                agent_name="hydro-orchestrator", trace=list(self.chief_trace),
            )
            if journal.read("tool_catalog") is None:
                catalog = SourceCatalog.model_validate(await self.tools.catalog(request))
                journal.save("tool_catalog", catalog.model_dump(mode="json"))
            self.load_carried_evidence(request, journal)
            if self.carried_ids:
                self.emit_event(
                    f"{request.run_id}:chief", "supervisor", "running", "Supervisor",
                    "Reusing the verified evidence from the previous answer instead of re-reading it.",
                    agent_name="hydro-orchestrator", trace=list(self.chief_trace),
                )
            owner = self
            storage = RequiredCheckpointStorage(path / "checkpoints")
            request_digest = sha256(request.model_dump_json().encode()).hexdigest()

            def restore_turn(encoded_turn: str) -> SupervisorTurn:
                turn = SupervisorTurn.model_validate_json(encoded_turn)
                if turn.request_digest != request_digest or turn.version != versions["supervisor"].version:
                    raise SourceFailure("Workflow stage belongs to a different request or pinned supervisor.")
                if (len({item.id for item in turn.evidence}) != len(turn.evidence)
                        or any(item.source != request.source for item in turn.evidence)):
                    raise SourceFailure("Workflow stage contains duplicate or differently bound source evidence.")
                if any(item.agent_name != versions[item.role].name or item.version != versions[item.role].version
                       for item in turn.specialists):
                    raise SourceFailure("Workflow stage contains a differently pinned specialist.")
                owner.evidence = {item.id: item for item in turn.evidence}
                owner.specialists = list(turn.specialists)
                if turn.plan is not None:
                    owner.orchestration_plan = OrchestrationPlan.model_validate(turn.plan)
                    owner.answer_contract = owner.orchestration_plan.answer_contract
                return turn

            class ExecuteSupervisor(Executor):
                def __init__(self):
                    super().__init__(id="chief_orchestration")

                @handler
                async def handle(self, question: str, ctx: WorkflowContext[str]) -> None:
                    storage.require_healthy()
                    saved = journal.read("prepared_answer")
                    if saved is not None:
                        prepared = PreparedAnswer.model_validate(saved)
                        if (prepared.answer.run_id != request.run_id or prepared.answer.source != request.source
                                or prepared.request_digest != sha256(request.model_dump_json().encode()).hexdigest()):
                            raise SourceFailure("Prepared answer belongs to a different run or source.")
                        owner.evidence = {item.id: item for item in prepared.evidence}
                        if any(item.source != request.source for item in owner.evidence.values()):
                            raise SourceFailure("Prepared answer contains a different source identity.")
                        owner.specialists = list(prepared.answer.specialists)
                        if owner.answer(
                            request, prepared.supervisor_output,
                            use_projected_summary=prepared.answer_projection is not None,
                            analysis=prepared.answer.analysis,
                        ) != prepared.answer:
                            raise SourceFailure("Prepared answer differs from its immutable source projections.")
                        await ctx.send_message(SupervisorTurn(
                            text=prepared.supervisor_output.model_dump_json(),
                            response_id=prepared.supervisor_response_id,
                            version=prepared.supervisor_version, duration_ms=prepared.supervisor_duration_ms,
                            model_round_count=prepared.supervisor_model_round_count,
                            evidence=prepared.evidence, specialists=prepared.answer.specialists,
                            request_digest=request_digest, verified=True, prepared=prepared,
                        ).model_dump_json())
                        return
                    recovered = journal.read("verified_supervisor_turn")
                    if recovered is None:
                        recovered = journal.read("supervisor_turn")
                    if recovered is not None:
                        await ctx.send_message(restore_turn(SupervisorTurn.model_validate(recovered).model_dump_json())
                                               .model_dump_json())
                        return
                    started = perf_counter()
                    plan = await owner.compile_orchestration_plan(request, journal)
                    elapsed = (perf_counter() - started) * 1000
                    response_text = plan.model_dump_json(by_alias=True)
                    response_id = "deterministic-orchestrator:" + sha256(
                        response_text.encode()
                    ).hexdigest()
                    model_round_count = None
                    journal.save(f"supervisor_output:{response_id}", {
                        "text": response_text,
                        "control_plane": "deterministic",
                        "model_invoked": False,
                    })
                    owner.chief_trace.append({
                        "id": f"{request.run_id}:plan-compiled",
                        "timestamp": int(utc_now().timestamp() * 1000),
                        "source": "application",
                        "label": "Supervisor compiled and validated the deterministic execution plan.",
                        "activity": "validation",
                    })
                    owner.emit_event(
                        f"{request.run_id}:chief", "supervisor", "running", "Supervisor",
                        "Deterministic plan compiled; dispatching bounded specialist work.",
                        agent_name="hydro-orchestrator", response_id=response_id,
                        trace=list(owner.chief_trace),
                    )
                    turn = SupervisorTurn(
                        text=response_text, response_id=response_id,
                        version=versions["supervisor"].version, duration_ms=elapsed,
                        model_round_count=model_round_count,
                        evidence=tuple(owner.evidence.values()), specialists=tuple(owner.specialists),
                        plan=plan.model_dump(mode="json", by_alias=True),
                        request_digest=request_digest,
                    )
                    journal.save("supervisor_turn", turn.model_dump(mode="json"))
                    await ctx.send_message(turn.model_dump_json())

            class ExecuteDelegation(Executor):
                def __init__(self, index: int):
                    super().__init__(id=f"specialist_handoff_{index + 1}")
                    self.index = index

                @handler
                async def handle(self, encoded_turn: str, ctx: WorkflowContext[str]) -> None:
                    storage.require_healthy()
                    turn = restore_turn(encoded_turn)
                    if turn.prepared is not None or turn.plan is None:
                        await ctx.send_message(turn.model_dump_json())
                        return
                    plan = OrchestrationPlan.model_validate(turn.plan)
                    if self.index >= len(plan.steps):
                        await ctx.send_message(turn.model_dump_json())
                        return
                    delegation = plan.steps[self.index]
                    selected = (
                        selected_equipment_from_evidence(request.equipment_selection, owner.evidence.values())
                        if request.equipment_selection is not None
                        else set(owner.selected_equipment_ids)
                    )
                    verified_issue_ids: set[str] = set()
                    for specialist in reversed(owner.specialists):
                        if specialist.role != "rca" or not isinstance(specialist.report, dict):
                            continue
                        follow_up = specialist.report.get("maintenance_follow_up")
                        if (
                            isinstance(follow_up, dict)
                            and follow_up.get("decision") == "verified_uncovered_issue"
                            and isinstance(follow_up.get("equipment_ids"), list)
                        ):
                            verified_issue_ids.update(
                                value for value in follow_up["equipment_ids"]
                                if isinstance(value, str) and value
                            )
                        break
                    missing_selection = (
                        delegation.requires_selection
                        and delegation.specialist != "qa"
                        and not selected
                    )
                    missing_verified_issue = (
                        delegation.requires_verified_issue
                        and not verified_issue_ids.intersection(selected)
                    )
                    if missing_selection or missing_verified_issue:
                        reason = (
                            "no prior equipment selection was grounded"
                            if missing_selection else "RCA did not verify an uncovered equipment issue"
                        )
                        owner.chief_trace.append({
                            "id": f"{request.run_id}:skip:{self.index + 1}",
                            "timestamp": int(utc_now().timestamp() * 1000),
                            "source": "application",
                            "label": f"Supervisor skipped {delegation.specialist}: {reason}.",
                            "activity": "delegation-return",
                        })
                        owner.emit_event(
                            f"{request.run_id}:chief", "supervisor", "running", "Supervisor",
                            f"A conditional specialist step was skipped because {reason}.",
                            agent_name="hydro-orchestrator", trace=list(owner.chief_trace),
                        )
                    else:
                        prior_evidence_ids = set(owner.evidence)
                        maintenance_gate_required = (
                            delegation.specialist == "rca"
                            and any(
                                later.specialist == "work-order" and later.requires_verified_issue
                                for later in plan.steps[self.index + 1:]
                            )
                        )
                        async with asyncio.timeout(max(1.0, (request.deadline - utc_now()).total_seconds())):
                            await owner.handoff(
                                delegation, request, journal, versions,
                                maintenance_gate_required=maintenance_gate_required,
                            )
                        new_receipts = [
                            receipt for evidence_id, receipt in owner.evidence.items()
                            if evidence_id not in prior_evidence_ids
                        ]
                        grounded_ids = evidence_equipment_ids(new_receipts)
                        deterministic = selected_equipment_from_evidence(
                            request.equipment_selection, owner.evidence.values(),
                        )
                        owner.selected_equipment_ids = (
                            set(deterministic)
                            if request.equipment_selection is not None
                            else updated_equipment_selection(
                                set(owner.selected_equipment_ids),
                                grounded_ids,
                                requires_selection=delegation.requires_selection,
                            )
                        )
                    updated = turn.model_copy(update={
                        "evidence": tuple(owner.evidence.values()),
                        "specialists": tuple(owner.specialists),
                    })
                    journal.save(
                        f"specialist_stage:{self.index + 1}",
                        updated.model_dump(mode="json"),
                    )
                    await ctx.send_message(updated.model_dump_json())

            class VerifyEvidence(Executor):
                def __init__(self):
                    super().__init__(id="evidence_verification")

                @handler
                async def handle(self, encoded_turn: str, ctx: WorkflowContext[str]) -> None:
                    storage.require_healthy()
                    turn = restore_turn(encoded_turn)
                    if turn.verified:
                        await ctx.send_message(turn.model_dump_json())
                        return
                    await owner.fulfill_catalog_requirements(request, journal)
                    if any(item.role in ("rca", "work-order") for item in owner.specialists):
                        verified_at = int(utc_now().timestamp() * 1000)
                        owner.chief_trace.append({
                            "id": f"{request.run_id}:evidence-verification",
                            "timestamp": verified_at,
                            "source": "application",
                            "label": "Supervisor accepted deterministic evidence and specialist-contract validation.",
                            "activity": "validation",
                        })
                        owner.emit_event(
                            f"{request.run_id}:chief", "supervisor", "running", "Supervisor",
                            "Deterministic evidence verification completed.",
                            agent_name="hydro-orchestrator", trace=list(owner.chief_trace),
                        )
                    verified = turn.model_copy(update={
                        "verified": True, "evidence": tuple(owner.evidence.values()),
                        "specialists": tuple(owner.specialists),
                    })
                    journal.save("verified_supervisor_turn", verified.model_dump(mode="json"))
                    await ctx.send_message(verified.model_dump_json())

            class PresentGroundedAnswer(Executor):
                def __init__(self):
                    super().__init__(id="grounded_presentation")

                @handler
                async def handle(self, encoded_turn: str, ctx: WorkflowContext[None, str]) -> None:
                    storage.require_healthy()
                    turn = restore_turn(encoded_turn)
                    if not turn.verified:
                        raise SourceFailure("Presentation requires completed independent-evidence verification.")
                    if turn.prepared is not None:
                        if owner.answer(
                            request, turn.prepared.supervisor_output,
                            use_projected_summary=turn.prepared.answer_projection is not None,
                            analysis=turn.prepared.answer.analysis,
                        ) != turn.prepared.answer:
                            raise SourceFailure("Recovered presentation differs from immutable source projections.")
                        await ctx.yield_output(turn.prepared.answer.model_dump_json())
                        return
                    plan = owner.contract_answer_plan(request)
                    projection = ProjectionReceipt(
                        response_id="deterministic-contract-compiler",
                        model="application",
                        duration_ms=0,
                        input_digest=sha256(_encoded({
                            "contract": owner.answer_contract.model_dump(mode="json")
                            if owner.answer_contract is not None else None,
                            "evidence": [item.receipt() for item in owner.evidence.values()],
                        })).hexdigest(),
                    )
                    journal.save("answer_projection:deterministic-contract-compiler", {
                        "text": plan.summary,
                        **projection.model_dump(mode="json"),
                    })
                    result = owner.answer(request, plan, use_projected_summary=True)
                    if owner.analysis_model:
                        owner.emit_event(
                            f"{request.run_id}:chief", "supervisor", "running", "Supervisor",
                            "Writing the grounded briefing: comparing the verified evidence with open work and "
                            "reasoning to a recommendation.",
                            agent_name="hydro-orchestrator", trace=list(owner.chief_trace),
                        )
                    analysis = await owner.analysis_for(request, result, journal)
                    if analysis is not None:
                        result = owner.answer(request, plan, use_projected_summary=True, analysis=analysis)
                    elif owner.analysis_model and result.tables:
                        plan = plan.model_copy(update={"limitations": (
                            *plan.limitations,
                            "The written briefing could not be verified against the evidence for this run"
                            + (f" ({owner.analysis_rejection})" if owner.analysis_rejection else "")
                            + ", so only the verified tables are shown.",
                        )[:20]})
                        result = owner.answer(request, plan, use_projected_summary=True)
                    journal.save("prepared_answer", PreparedAnswer(
                        answer=result, evidence=tuple(owner.evidence.values()),
                        supervisor_response_id=turn.response_id, supervisor_version=turn.version,
                        supervisor_duration_ms=turn.duration_ms, supervisor_output=plan,
                        supervisor_model_round_count=turn.model_round_count,
                        answer_projection=projection,
                        request_digest=request_digest,
                    ).model_dump(mode="json"))
                    await ctx.yield_output(result.model_dump_json())

            chief, verifier, presenter = ExecuteSupervisor(), VerifyEvidence(), PresentGroundedAnswer()
            handoffs = [ExecuteDelegation(index) for index in range(8)]
            builder = WorkflowBuilder(
                name="hydro-foundry-supervisor-v2",
                start_executor=chief,
                checkpoint_storage=storage,
                max_iterations=16,
            )
            previous: Executor = chief
            for handoff in handoffs:
                builder = builder.add_edge(previous, handoff)
                previous = handoff
            workflow = builder.add_edge(previous, verifier).add_edge(verifier, presenter).build()
            journal.save("workflow_graph", {"format": "mermaid",
                                           "definition": WorkflowViz(workflow).to_mermaid()})
            result = await workflow.run(request.question)
            storage.require_healthy()
            outputs = result.get_outputs()
            if len(outputs) != 1 or not isinstance(outputs[0], str):
                raise SourceFailure("Supervisor workflow did not return exactly one typed answer.")
            answer = ChatAnswer.model_validate_json(outputs[0])
            prepared = PreparedAnswer.model_validate(journal.read("prepared_answer"))
            journal.save("evidence", {
                "receipts": [item.model_dump(mode="json") for item in self.evidence.values()],
                "supervisor": {"response_id": prepared.supervisor_response_id, "version": prepared.supervisor_version,
                               "duration_ms": prepared.supervisor_duration_ms,
                               "model_round_count": prepared.supervisor_model_round_count,
                               "output": journal.read(f"supervisor_output:{prepared.supervisor_response_id}")},
                "answer_plan": prepared.supervisor_output.model_dump(mode="json"),
                "answer_projection": (prepared.answer_projection.model_dump(mode="json")
                                      if prepared.answer_projection else None),
            })
            journal.save("answer", answer.model_dump(mode="json"))
            self.emit_event(
                f"{request.run_id}:chief", "supervisor", "completed", "Supervisor",
                "Verified answer committed with source and specialist receipts.",
                agent_name="hydro-orchestrator",
                response_id=prepared.supervisor_response_id, trace=list(self.chief_trace),
            )
            return answer
