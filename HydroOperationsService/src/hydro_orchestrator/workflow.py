import asyncio
from pathlib import Path
from typing import Protocol

from agent_framework import (
    Executor,
    FileCheckpointStorage,
    Workflow,
    WorkflowBuilder,
    WorkflowContext,
    WorkflowCheckpoint,
    WorkflowException,
    handler,
    response_handler,
)

from .contracts import (
    AgentReceipt, Approval, Assessment, Evidence, EvidenceReference, Observation, Outcome,
    Proposal, RcaHypothesis, RcaReport, ReviewRequest, SourceIdentity, WorkReview, utc_now,
)


class ReviewAdapters(Protocol):
    """Provider boundary; no browser tokens, prose parsing or fallback source."""

    async def read(self, request: ReviewRequest) -> Evidence: ...

    async def investigate(self, evidence: Evidence) -> Assessment: ...

    async def propose(self, assessment: Assessment) -> Proposal | WorkReview: ...


class ValidationWriter(Protocol):
    def record_decision(self, proposal: Proposal, approval: Approval) -> Outcome: ...

    def record_review(self, review: WorkReview) -> Outcome: ...


class RequiredCheckpointStorage(FileCheckpointStorage):
    """The SDK logs save failures and continues; Hydro must fail closed instead."""

    def __init__(self, path: Path):
        contracts = (ReviewRequest, SourceIdentity, Observation, Evidence, Assessment, Proposal, Approval, Outcome,
                     EvidenceReference, RcaHypothesis, RcaReport, AgentReceipt, WorkReview)
        super().__init__(
            path, allowed_checkpoint_types=[f"{item.__module__}:{item.__qualname__}" for item in contracts],
        )
        self.failure: Exception | None = None

    def require_healthy(self) -> None:
        if self.failure:
            raise RuntimeError("Durable checkpoint failed; no subsequent step is permitted.") from self.failure

    async def save(self, checkpoint: WorkflowCheckpoint) -> str:
        self.require_healthy()
        try:
            return await super().save(checkpoint)
        except (WorkflowException, OSError, ValueError, TypeError) as error:
            self.failure = error
            raise

    async def get_latest(self, *, workflow_name: str) -> WorkflowCheckpoint | None:
        for path in self.storage_path.glob("*.json"):
            await self.load(path.stem)
        return await super().get_latest(workflow_name=workflow_name)


class ReadEvidence(Executor):
    def __init__(self, adapters: ReviewAdapters, timeout: float, storage: RequiredCheckpointStorage):
        super().__init__(id="read_evidence")
        self.adapters, self.timeout = adapters, timeout
        self.storage = storage

    @handler
    async def handle(self, request: ReviewRequest, ctx: WorkflowContext[Evidence]) -> None:
        self.storage.require_healthy()
        async with asyncio.timeout(self.timeout):
            evidence = await self.adapters.read(request)
        if evidence.request != request:
            raise ValueError("Source adapter changed the run or operator request.")
        await ctx.send_message(Evidence.model_validate(evidence.model_dump()))


class Investigate(Executor):
    def __init__(self, adapters: ReviewAdapters, timeout: float, storage: RequiredCheckpointStorage):
        super().__init__(id="investigate")
        self.adapters, self.timeout = adapters, timeout
        self.storage = storage

    @handler
    async def handle(self, evidence: Evidence, ctx: WorkflowContext[Assessment]) -> None:
        self.storage.require_healthy()
        async with asyncio.timeout(self.timeout):
            assessment = await self.adapters.investigate(evidence)
        if assessment.evidence != evidence:
            raise ValueError("Investigation changed its source evidence.")
        await ctx.send_message(Assessment.model_validate(assessment.model_dump()))


class PrepareProposal(Executor):
    def __init__(
        self, adapters: ReviewAdapters, timeout: float,
        storage: RequiredCheckpointStorage, writer: ValidationWriter,
    ):
        super().__init__(id="prepare_proposal")
        self.adapters, self.timeout = adapters, timeout
        self.storage, self.writer = storage, writer

    @handler
    async def handle(self, assessment: Assessment, ctx: WorkflowContext[Proposal, Outcome]) -> None:
        self.storage.require_healthy()
        async with asyncio.timeout(self.timeout):
            proposal = await self.adapters.propose(assessment)
        if proposal.assessment != assessment:
            raise ValueError("Proposal changed the verified assessment.")
        if isinstance(proposal, WorkReview):
            review = WorkReview.model_validate(proposal.model_dump())
            await ctx.yield_output(self.writer.record_review(review))
            return
        if proposal.expires_at <= utc_now():
            raise ValueError("Proposal has already expired.")
        await ctx.send_message(Proposal.model_validate(proposal.model_dump()))


class HumanApproval(Executor):
    def __init__(self, writer: ValidationWriter, storage: RequiredCheckpointStorage):
        super().__init__(id="human_approval")
        self.writer = writer
        self.storage = storage

    @handler
    async def request(self, proposal: Proposal, ctx: WorkflowContext[None, Outcome]) -> None:
        self.storage.require_healthy()
        await ctx.request_info(proposal, Approval, request_id=str(proposal.request.run_id))

    @response_handler
    async def decide(
        self, original_request: Proposal, response: Approval, ctx: WorkflowContext[None, Outcome]
    ) -> None:
        self.storage.require_healthy()
        if response.proposal_digest != original_request.digest():
            raise ValueError("Approval does not match the immutable displayed proposal.")
        await ctx.yield_output(self.writer.record_decision(original_request, response))


def checkpoint_store(path: Path) -> RequiredCheckpointStorage:
    return RequiredCheckpointStorage(path)


def build_workflow(
    storage: RequiredCheckpointStorage, adapters: ReviewAdapters, writer: ValidationWriter, *, step_timeout: float = 30
) -> Workflow:
    if step_timeout <= 0:
        raise ValueError("Step timeout must be positive.")
    read = ReadEvidence(adapters, step_timeout, storage)
    investigate = Investigate(adapters, step_timeout, storage)
    propose = PrepareProposal(adapters, step_timeout, storage, writer)
    approval = HumanApproval(writer, storage)
    return (
        WorkflowBuilder(
            name="hydro-maintenance-review-v1",
            start_executor=read,
            checkpoint_storage=storage,
            max_iterations=12,
        )
        .add_edge(read, investigate)
        .add_edge(investigate, propose)
        .add_edge(propose, approval)
        .build()
    )
