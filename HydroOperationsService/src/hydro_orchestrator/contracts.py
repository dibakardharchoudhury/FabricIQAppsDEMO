from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


def contract_digest(value: Contract) -> str:
    encoded = json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return sha256(encoded).hexdigest()


class SourceIdentity(Contract):
    tenant_id: UUID
    workspace_id: UUID
    ontology_id: UUID
    generation: Literal[2]
    configuration_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("generation", mode="before")
    @classmethod
    def numeric_generation(cls, value: object) -> object:
        if type(value) is not int or value != 2:
            raise ValueError("Live numeric ontology generation 2 is required.")
        return value


class ReviewInput(Contract):
    equipment_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    priority: Literal["Low", "Medium", "High", "Critical"] = "Medium"


class ReviewRequest(ReviewInput):
    run_id: UUID
    source: SourceIdentity


class Observation(Contract):
    evidence_id: str = Field(min_length=1, max_length=200)
    metric: str = Field(min_length=1, max_length=200)
    value: float = Field(strict=True)
    unit: str = Field(min_length=1, max_length=40)
    event_time: AwareDatetime
    quality: Literal["GOOD", "BAD", "UNCERTAIN"]


class Evidence(Contract):
    request: ReviewRequest
    source: SourceIdentity
    equipment_id: str
    read_completed_at: AwareDatetime
    observations: tuple[Observation, ...] = Field(max_length=100)
    open_work_numbers: tuple[str, ...] = Field(max_length=500)
    missing_sources: tuple[str, ...] = Field(max_length=20)
    work_orders_read_at: AwareDatetime | None = None

    def telemetry_receipt(self) -> dict[str, object]:
        return {
            "id": f"{self.request.run_id}:telemetry", "tool": "query_telemetry",
            "completedAt": self.read_completed_at.isoformat(),
            "result": {
                "rows": [item.model_dump(mode="json") for item in self.observations],
                "read_completed_at_utc": self.read_completed_at.isoformat(),
                "missing_sources": list(self.missing_sources),
            },
        }

    @model_validator(mode="after")
    def bound_identity(self) -> "Evidence":
        if self.source != self.request.source or self.equipment_id != self.request.equipment_id:
            raise ValueError("Evidence does not match the requested verified source and equipment.")
        ids = [item.evidence_id for item in self.observations]
        if len(ids) != len(set(ids)):
            raise ValueError("Evidence identifiers must be unique.")
        if any(item.event_time > self.read_completed_at for item in self.observations):
            raise ValueError("Observation timestamp exceeds its source read clock.")
        if self.work_orders_read_at is None and self.open_work_numbers:
            raise ValueError("Work-order numbers require a completed work-source read.")
        if self.work_orders_read_at is not None:
            if self.work_orders_read_at > self.read_completed_at:
                raise ValueError("Work-source timestamp exceeds the complete evidence read clock.")
            if any(source in self.missing_sources for source in ("work_orders", "work_orders_not_requested")):
                raise ValueError("Work-source coverage contradicts its missing-source declaration.")
        return self


class EvidenceReference(Contract):
    evidence_id: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1, max_length=200, pattern=r"^/")


class RcaHypothesis(Contract):
    category: Literal["sensor_or_ingestion", "equipment_condition", "operating_conditions", "sampling_or_quality"]
    supporting: tuple[EvidenceReference, ...] = Field(max_length=12)
    contradicting: tuple[EvidenceReference, ...] = Field(max_length=12)
    missing: tuple[Literal[
        "fresh_measurements", "independent_measurement", "matched_baseline", "approved_limits",
        "operating_context", "inspection_evidence", "maintenance_scope", "parts_compatibility",
    ], ...] = Field(min_length=1, max_length=8)


class MaintenanceFollowUp(Contract):
    decision: Literal["verified_uncovered_issue", "not_supported"]
    reason: Literal[
        "uncovered_equipment_issue", "missing_issue_evidence", "missing_equipment_relation",
        "existing_work_covers_issue",
    ]
    equipment_ids: tuple[str, ...] = Field(max_length=20)
    evidence: tuple[EvidenceReference, ...] = Field(max_length=12)

    @model_validator(mode="after")
    def consistent_gate(self) -> "MaintenanceFollowUp":
        verified = self.decision == "verified_uncovered_issue"
        if verified != (self.reason == "uncovered_equipment_issue"):
            raise ValueError("Only an uncovered equipment issue may enable maintenance follow-up.")
        if verified and (not self.equipment_ids or not self.evidence):
            raise ValueError("Verified maintenance follow-up requires equipment and evidence.")
        if len(set(self.equipment_ids)) != len(self.equipment_ids):
            raise ValueError("Maintenance follow-up equipment identities must be unique.")
        return self


class RcaReport(Contract):
    observations: tuple[EvidenceReference, ...] = Field(min_length=1, max_length=12)
    hypotheses: tuple[RcaHypothesis, ...] = Field(min_length=2, max_length=4)
    maintenance_follow_up: MaintenanceFollowUp


class AgentReceipt(Contract):
    agent_name: str = Field(min_length=1, max_length=200)
    requested_version: str = Field(pattern=r"^[1-9][0-9]*$")
    response_id: str = Field(min_length=1, max_length=200)
    request_id: str | None = Field(default=None, max_length=200)
    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    duration_ms: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, strict=True, ge=0)
    output_tokens: int | None = Field(default=None, strict=True, ge=0)


class Assessment(Contract):
    evidence: Evidence
    observations: tuple[str, ...] = Field(max_length=100)
    hypotheses: tuple[Literal[
        "sensor_or_ingestion", "equipment_condition", "operating_regime", "operating_conditions", "sampling_or_quality",
    ], ...] = Field(
        min_length=2, max_length=4
    )
    missing_evidence: tuple[str, ...] = Field(min_length=1, max_length=20)
    conclusion: Literal["cause_undetermined"] = "cause_undetermined"
    report: RcaReport | None = None
    agent_receipt: AgentReceipt | None = None

    @model_validator(mode="after")
    def validate_references(self) -> "Assessment":
        known = {item.evidence_id for item in self.evidence.observations}
        if not set(self.observations) <= known:
            raise ValueError("Assessment references evidence that was not returned.")
        if len(set(self.hypotheses)) != len(self.hypotheses):
            raise ValueError("Competing hypotheses must be distinct.")
        if (self.report is None) != (self.agent_receipt is None):
            raise ValueError("Live assessment requires both a structured report and an invocation receipt.")
        if self.report and self.agent_receipt:
            if self.agent_receipt.agent_name != "hydro-rca-agent":
                raise ValueError("Assessment receipt belongs to a different specialist.")
            if self.agent_receipt.input_digest != sha256(self.evidence.model_dump_json().encode()).hexdigest():
                raise ValueError("Agent receipt does not match the immutable evidence input.")
            if self.hypotheses != tuple(item.category for item in self.report.hypotheses):
                raise ValueError("Assessment changed the structured hypothesis categories.")
            receipt = self.evidence.telemetry_receipt()
            references = [
                *self.report.observations,
                *(reference for item in self.report.hypotheses
                  for reference in (*item.supporting, *item.contradicting)),
            ]
            for reference in references:
                if reference.evidence_id != receipt["id"]:
                    raise ValueError("Assessment references a different source receipt.")
                value = receipt["result"]
                for encoded in reference.path.split("/")[1:]:
                    key = encoded.replace("~1", "/").replace("~0", "~")
                    if isinstance(value, dict) and key in value:
                        value = value[key]
                    elif isinstance(value, list) and key.isascii() and key.isdecimal() and int(key) < len(value):
                        value = value[int(key)]
                    else:
                        raise ValueError("Assessment pointer does not resolve into its source receipt.")
            selected = {
                self.evidence.observations[int(segments[2])].evidence_id
                for reference in self.report.observations
                if len(segments := reference.path.split("/")) >= 3 and segments[1] == "rows"
                and segments[2].isascii() and segments[2].isdecimal()
            }
            if not selected or set(self.observations) != selected:
                raise ValueError("Selected observations differ from the source-referenced report.")
            gaps = {gap for hypothesis in self.report.hypotheses for gap in hypothesis.missing}
            if not gaps <= set(self.missing_evidence):
                raise ValueError("Assessment omitted missing evidence from the structured report.")
        return self


class WorkReview(Contract):
    assessment: Assessment
    decision: Literal["no_draft", "needs_clarification"]
    reason: str = Field(min_length=1, max_length=1000)
    agent_receipt: AgentReceipt | None = None

    @model_validator(mode="after")
    def bound_review(self) -> "WorkReview":
        if not self.reason.strip():
            raise ValueError("Work review requires an explicit reason.")
        if self.decision == "no_draft" and self.assessment.evidence.work_orders_read_at is None:
            raise ValueError("A no-draft coverage decision requires a completed work-order read.")
        if self.agent_receipt:
            digest = sha256(self.assessment.model_dump_json().encode()).hexdigest()
            if (self.agent_receipt.agent_name != "hydro-work-order-agent"
                    or self.agent_receipt.input_digest != digest):
                raise ValueError("Work-review receipt does not match the specialist and immutable assessment.")
        return self


class Proposal(Contract):
    request: ReviewRequest
    description: str = Field(min_length=1, max_length=4000)
    assessment: Assessment
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def same_request(self) -> "Proposal":
        if self.assessment.evidence.request != self.request:
            raise ValueError("Proposal changed the operator request or source binding.")
        work_read = self.assessment.evidence.work_orders_read_at
        if work_read is None:
            raise ValueError("A proposal requires a completed work-order coverage read.")
        if self.expires_at > work_read + timedelta(minutes=15):
            raise ValueError("Approval expiry cannot extend beyond 15 minutes from the work-order read.")
        return self

    def digest(self) -> str:
        return contract_digest(self)


class Approval(Contract):
    approved: bool = Field(strict=True)
    proposal_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

class WorkOrderDraft(Contract):
    id: UUID
    run_id: UUID
    source: SourceIdentity
    equipment_id: str = Field(min_length=1, max_length=200)
    instrument_id: str | None = Field(default=None, min_length=1, max_length=200)
    opcua_node_id: str | None = Field(default=None, min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    priority: Literal["Low", "Medium", "High", "Critical"]
    work_read_at: AwareDatetime
    expires_at: AwareDatetime
    existing_work: tuple[dict[str, object], ...] = Field(max_length=500)

    @model_validator(mode="after")
    def verified_draft(self) -> "WorkOrderDraft":
        if any(not value.strip() for value in (self.equipment_id, self.title, self.description)):
            raise ValueError("Work-order identity, title and description must not be blank.")
        if not self.work_read_at < self.expires_at <= self.work_read_at + timedelta(minutes=15):
            raise ValueError("A draft expires within 15 minutes of its complete work read.")
        identities = set()
        for row in self.existing_work:
            identity, status = row.get("id"), row.get("status")
            if (not isinstance(identity, str) or not identity.strip() or identity in identities
                    or row.get("equipmentId") != self.equipment_id
                    or not isinstance(status, str) or not status.strip()
                    or status.strip().lower() in ("completed", "cancelled")):
                raise ValueError("Draft work coverage contains invalid, duplicate or unrelated open work.")
            identities.add(identity)
        return self

    def digest(self) -> str:
        return contract_digest(self)


class WorkOrderEdits(Contract):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    priority: Literal["Low", "Medium", "High", "Critical"]

    @model_validator(mode="after")
    def not_blank(self) -> "WorkOrderEdits":
        if not self.title.strip() or not self.description.strip():
            raise ValueError("Approved work requires a nonblank title and description.")
        return self


class WorkOrderDecision(Contract):
    proposal_id: UUID
    proposal_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved: bool = Field(strict=True)
    edits: WorkOrderEdits | None = None

    @model_validator(mode="after")
    def explicit_edits(self) -> "WorkOrderDecision":
        if self.approved != (self.edits is not None):
            raise ValueError("Approval requires the reviewed fields; rejection cannot edit a draft.")
        return self


class Outcome(Contract):
    run_id: UUID
    status: Literal["rejected", "validation_work_recorded", "no_draft", "needs_clarification"]
    validation_work_id: str | None = None
    production_write_executed: Literal[False] = False
    review: WorkReview | None = None

    @model_validator(mode="after")
    def review_outcome(self) -> "Outcome":
        if self.status in ("no_draft", "needs_clarification"):
            if (self.review is None or self.review.decision != self.status
                    or self.review.assessment.evidence.request.run_id != self.run_id
                    or self.validation_work_id is not None):
                raise ValueError("A non-proposal outcome must retain its matching review and cannot record work.")
        elif self.review is not None:
            raise ValueError("Approval outcomes cannot contain a non-proposal review.")
        return self


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
