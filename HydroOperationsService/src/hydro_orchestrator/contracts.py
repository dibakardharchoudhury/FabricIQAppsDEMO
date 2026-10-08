from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


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


class RcaReport(Contract):
    observations: tuple[EvidenceReference, ...] = Field(min_length=1, max_length=12)
    hypotheses: tuple[RcaHypothesis, ...] = Field(min_length=2, max_length=4)


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
            if self.agent_receipt.input_digest != sha256(self.evidence.model_dump_json().encode()).hexdigest():
                raise ValueError("Agent receipt does not match the immutable evidence input.")
            if self.hypotheses != tuple(item.category for item in self.report.hypotheses):
                raise ValueError("Assessment changed the structured hypothesis categories.")
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
        return sha256(self.model_dump_json().encode()).hexdigest()


class Approval(Contract):
    approved: bool = Field(strict=True)
    proposal_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class Outcome(Contract):
    run_id: UUID
    status: Literal["rejected", "validation_work_recorded"]
    validation_work_id: str | None = None
    production_write_executed: Literal[False] = False


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
