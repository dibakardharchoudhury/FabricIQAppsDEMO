from datetime import datetime, timezone
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

    @model_validator(mode="after")
    def bound_identity(self) -> "Evidence":
        if self.source != self.request.source or self.equipment_id != self.request.equipment_id:
            raise ValueError("Evidence does not match the requested verified source and equipment.")
        ids = [item.evidence_id for item in self.observations]
        if len(ids) != len(set(ids)):
            raise ValueError("Evidence identifiers must be unique.")
        if any(item.event_time > self.read_completed_at for item in self.observations):
            raise ValueError("Observation timestamp exceeds its source read clock.")
        return self


class Assessment(Contract):
    evidence: Evidence
    observations: tuple[str, ...] = Field(max_length=100)
    hypotheses: tuple[Literal["sensor_or_ingestion", "equipment_condition", "operating_regime"], ...] = Field(
        min_length=2, max_length=3
    )
    missing_evidence: tuple[str, ...] = Field(min_length=1, max_length=20)
    conclusion: Literal["cause_undetermined"] = "cause_undetermined"

    @model_validator(mode="after")
    def validate_references(self) -> "Assessment":
        known = {item.evidence_id for item in self.evidence.observations}
        if not set(self.observations) <= known:
            raise ValueError("Assessment references evidence that was not returned.")
        if len(set(self.hypotheses)) != len(self.hypotheses):
            raise ValueError("Competing hypotheses must be distinct.")
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
