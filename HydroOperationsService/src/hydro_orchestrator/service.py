import asyncio
import hashlib
import json
import logging
import secrets
import sqlite3
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal
from uuid import UUID, uuid4, uuid5

from agent_framework import WorkflowException
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from filelock import FileLock, Timeout as LockTimeout

from .contracts import Approval, Contract, Outcome, Proposal, ReviewInput, ReviewRequest, SourceIdentity, WorkOrderDecision, utc_now
from .foundry_supervisor import ChatAnswer, ChatRequest, FoundrySupervisor, HistoricalContext, NativeBinding, NativeSource, RunJournal
from pydantic import Field, ValidationError, model_validator
from starlette.responses import StreamingResponse
from .source_auth import DelegatedCredential, DelegatedTokens, SourceAuthPolicy, SourceAuthorizationError, SourceTokenVerifier
from .store import Conflict, Store, StoredRun
from .workflow import ReviewAdapters, build_workflow, checkpoint_store
from .presentation import OperatorAnswer, client_presentation, operator_answer
from .live_sources import Bridge, FabricBackendTools, LiveSources, NodeSourceBridge, SourceFailure
from .foundry_rca import verified_project_endpoint

logger = logging.getLogger(__name__)


class LocalRunner:
    def __init__(self, store: Store, adapters: ReviewAdapters, source: SourceIdentity, *, step_timeout: float = 30):
        self.store, self.adapters, self.source = store, adapters, source
        self.step_timeout = step_timeout
        self.tasks: dict[UUID, asyncio.Task[None]] = {}
        self.capacity = asyncio.Semaphore(4)

    def schedule(self, run_id: UUID) -> None:
        task = self.tasks.get(run_id)
        if task is None or task.done():
            task = asyncio.create_task(self.execute(run_id))
            self.tasks[run_id] = task
            task.add_done_callback(lambda done: self.finished(run_id, done))

    def finished(self, run_id: UUID, task: asyncio.Task[None]) -> None:
        if self.tasks.get(run_id) is task:
            del self.tasks[run_id]
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("Unexpected local workflow failure for %s", run_id, exc_info=error)
            self.record_failure(run_id, error)

    def record_failure(self, run_id: UUID, error: BaseException) -> None:
        try:
            self.store.fail(run_id, f"{type(error).__name__}: {error}")
        except Conflict:
            if self.store.get(run_id).status != "completed":
                raise
            logger.error("Rejected late failure for completed workflow %s", run_id, exc_info=error)
            self.store.event(run_id, "late_failure_rejected", "application")

    async def execute(self, run_id: UUID) -> None:
        async with self.capacity:
            try:
                run = self.store.get(run_id)
                if run.request.source != self.source:
                    raise Conflict("Saved source identity differs from the configured deployment.")
                if run.status not in ("queued", "running"):
                    return
                self.store.start(run_id)
                if run.outcome:
                    self.store.finish(run.outcome)
                    self.store.event(run_id, "committed_outcome_recovered", "application")
                    return
                path = self.store.root / "checkpoints" / str(run_id)
                storage = checkpoint_store(path)
                workflow = build_workflow(storage, self.adapters, self.store, step_timeout=self.step_timeout)
                saved = await storage.get_latest(workflow_name="hydro-maintenance-review-v1")
                if run.approval and saved is None:
                    raise Conflict("Cannot resume approval without its workflow checkpoint.")
                stream = workflow.run(
                    None if saved else run.request,
                    checkpoint_id=saved.checkpoint_id if saved else None,
                    responses={str(run_id): run.approval}
                    if run.approval and saved and str(run_id) in saved.pending_request_info_events else None,
                    stream=True,
                )
                async for event in stream:
                    self.store.event(run_id, event.type, event.executor_id)
                    storage.require_healthy()
                result = await stream.get_final_response()
                storage.require_healthy()
                requests, outputs = result.get_request_info_events(), result.get_outputs()
                if len(outputs) == 1 and isinstance(outputs[0], Outcome):
                    self.store.finish(outputs[0])
                elif len(requests) == 1 and isinstance(requests[0].data, Proposal):
                    self.store.wait(requests[0].data)
                else:
                    raise RuntimeError("Workflow returned neither one approval request nor one typed outcome.")
            except (WorkflowException, ValueError, RuntimeError, OSError, sqlite3.Error) as error:
                logger.exception("Local validation workflow %s failed", run_id)
                self.record_failure(run_id, error)

    async def close(self) -> None:
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)


class ChatInput(Contract):
    question: str = Field(min_length=1, max_length=8000)
    charts_requested: bool = Field(default=False, strict=True)
    native_sources: tuple[NativeSource, ...] = ()
    proposal_priority: Literal["Low", "Medium", "High", "Critical"] = "Medium"
    previous_run_id: UUID | None = None


def normalized_chat_input(chat: ChatInput, intent: dict[str, object]) -> ChatInput:
    fields = {"charts_requested", "native_sources", "proposal_priority"}
    if set(intent) != fields:
        raise ValueError("Intent normalization returned an unsupported contract.")
    normalized = ChatInput.model_validate({"question": chat.question, **intent})
    return ChatInput.model_validate({
        **normalized.model_dump(),
        **{key: getattr(chat, key) for key in chat.model_fields_set},
    })


def preceding_context(state: Path, source: SourceIdentity, previous_run_id: UUID | None) -> HistoricalContext | None:
    if previous_run_id is None:
        return None
    journal = RunJournal(state / str(previous_run_id) / "receipts")
    saved_answer, saved_request = journal.read("answer"), journal.read("request")
    if saved_answer is None or saved_request is None:
        raise HTTPException(404, "No preceding completed conversation is available for this signed-in user.")
    answer, request = ChatAnswer.model_validate(saved_answer), ChatRequest.model_validate(saved_request)
    if answer.source != source or request.source != source or answer.run_id != previous_run_id or request.run_id != previous_run_id:
        raise HTTPException(409, "The preceding conversation has a different source identity.")
    if utc_now() - answer.requested_at > timedelta(minutes=30):
        raise HTTPException(409, "The preceding conversation expired; start a new chat with an explicit target.")
    text = client_presentation(answer).text
    if len(text) > 16000:
        limitations = "\n".join(f"- {item}" for item in answer.limitations)
        text = (
            f"Previous grounded conclusion:\n{answer.summary}\n\n"
            f"Previous source limitations:\n{limitations or '- None reported.'}\n\n"
            "The previous supporting tables exceeded the bounded conversation context and are intentionally omitted. "
            "Historical content is context only; re-read current sources for any factual follow-up."
        )
    return HistoricalContext(run_id=previous_run_id, source=source, question=request.question,
                             rendered_answer=text, requested_at=answer.requested_at,
                             proposal_ids=tuple(item.id for item in answer.proposals))


class RuntimeConfiguration(Contract):
    source: SourceIdentity
    authorization: SourceAuthPolicy
    project_endpoint: str
    native_binding: NativeBinding | None = None
    production_writes_enabled: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def consistent_runtime(self) -> "RuntimeConfiguration":
        if self.source.tenant_id != self.authorization.tenant_id or not self.source.configuration_digest:
            raise ValueError("Runtime requires matching tenants and a complete source configuration digest.")
        if (self.authorization.scopes["fabric"] != "https://api.fabric.microsoft.com/.default"
                or self.authorization.scopes["foundry"] != "https://ai.azure.com/.default"):
            raise ValueError("Runtime resource scopes must match the Fabric and Foundry SDK resources.")
        verified_project_endpoint(self.project_endpoint)
        if self.native_binding is not None and (
            self.native_binding.source != self.source
            or any(not connection.strip() for connection in self.native_binding.connections.values())
        ):
            raise ValueError("Native bindings require the same source identity and explicit connection IDs.")
        return self


async def create_runtime_app(
    root: Path, configuration: RuntimeConfiguration, *, bridge: Bridge | None = None,
) -> FastAPI:
    bridge = bridge or NodeSourceBridge()
    source_config = await bridge.call({"action": "configuration"})
    if (source_config.get("tenant_id") != str(configuration.source.tenant_id)
            or source_config.get("workspace_id") != str(configuration.source.workspace_id)
            or source_config.get("ontology_id") != str(configuration.source.ontology_id)
            or source_config.get("configuration_digest") != configuration.source.configuration_digest):
        raise SourceFailure("Runtime source configuration differs from the configured source identity.")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with TemporaryDirectory(prefix=".state-probe-", dir=root) as directory:
        with FileLock(str(Path(directory) / "state.lock"), timeout=0):
            pass
    client = httpx.AsyncClient()
    try:
        app = create_fabric_delegated_app(
            root, SourceTokenVerifier(configuration.authorization, client), configuration.source,
            configuration.project_endpoint, bridge=bridge,
            native_binding=configuration.native_binding,
            production_writes_enabled=configuration.production_writes_enabled,
        )
    except Exception:
        await client.aclose()
        raise

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with client:
            yield

    app.router.lifespan_context = lifespan

    @app.get("/readiness")
    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "awaiting_authorized_invocation", "live_sources_verified": False,
            "distributed_durability": False, "production_writes_enabled": configuration.production_writes_enabled,
        }

    return app


class DelegatedInvocation(DelegatedTokens):
    operation: Literal["run", "evidence", "decide", "reconcile"]
    chat: ChatInput | None = None
    run_id: UUID | None = None
    decision: WorkOrderDecision | None = None
    stream: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def distinct_operations(self) -> "DelegatedInvocation":
        if self.operation == "run" and (self.chat is None or self.run_id is not None or self.decision is not None):
            raise ValueError("Run invocations require chat input and a server-assigned run identity.")
        if self.operation == "evidence" and (self.run_id is None or self.chat is not None or self.decision is not None):
            raise ValueError("Evidence invocations require only a run identity.")
        if self.operation in ("decide", "reconcile") and (self.run_id is None or self.chat is not None or self.decision is None):
            raise ValueError("Decision invocations require a run identity and an explicit reviewed decision.")
        if self.operation != "run" and self.stream:
            raise ValueError("Only run invocations can stream execution events.")
        return self


async def decide_work_order(
    state: Path, source: SourceIdentity, credential: DelegatedCredential, run_id: UUID,
    decision: WorkOrderDecision, tools: FabricBackendTools | None, *, require_previous: bool = False,
) -> dict[str, object]:
    journal = RunJournal(state / str(run_id) / "receipts")
    saved = journal.read("answer")
    if saved is None:
        raise HTTPException(404, "No completed run is available for this signed-in user.")
    answer = ChatAnswer.model_validate(saved)
    draft = next((item for item in answer.proposals if item.id == decision.proposal_id), None)
    if (answer.run_id != run_id or answer.source != source or draft is None
            or draft.source != source or draft.run_id != run_id):
        raise HTTPException(404, "No approval card is available for this signed-in user and source.")
    expected_digest = answer.proposal_digests.get(str(draft.id))
    if expected_digest is None or not secrets.compare_digest(expected_digest, decision.proposal_digest):
        raise HTTPException(409, "The approval card changed; review the current draft.")
    approval_key = f"approval:{draft.id}"
    lock = FileLock(str(journal.root / f"approval-{draft.id}.lock"), timeout=0)
    try:
        with lock:
            intent = {"decision": decision.model_dump(mode="json"), "principal_id": str(credential.principal_id)}
            previous = journal.read(approval_key + ":intent")
            if require_previous and previous is None:
                raise HTTPException(409, {
                    "run_id": str(run_id), "proposal_id": str(draft.id), "status": "blocked",
                    "message": "No submitted decision exists. Reconciliation cannot create a work order.",
                    "production_write_executed": False,
                })
            if previous is not None and previous != intent:
                raise HTTPException(409, "This card already has a different reviewed decision.")
            completed = journal.read(approval_key + ":result")
            if completed is not None:
                if completed.get("status") == "blocked":
                    raise HTTPException(409, completed)
                return completed
            if not decision.approved:
                journal.save(approval_key + ":intent", intent)
                result = {"run_id": str(run_id), "proposal_id": str(draft.id), "status": "rejected",
                          "production_write_executed": False}
                journal.save(approval_key + ":result", result)
                return result
            if tools is None:
                raise HTTPException(503, {
                    "run_id": str(run_id), "proposal_id": str(draft.id), "status": "disabled",
                    "message": "Production work-order writes are disabled; no SQL write was attempted.",
                    "production_write_executed": False,
                })
            if previous is None and draft.expires_at <= utc_now():
                raise HTTPException(409, "The draft expired; request fresh evidence before approving.")
            journal.save(approval_key + ":intent", intent)
            assert decision.edits is not None
            creation_id = uuid5(draft.id, f"{source.model_dump_json()}:{credential.principal_id}")
            try:
                record = await tools.approve(draft, decision.edits, credential.principal_id,
                                             creation_id, allow_create=previous is None)
            except SourceFailure as error:
                if error.write_attempted is False:
                    logger.warning("Approval %s was blocked before a SQL write.", draft.id)
                    result = {"run_id": str(run_id), "proposal_id": str(draft.id), "status": "blocked",
                              "message": str(error), "production_write_executed": False}
                    journal.save(approval_key + ":result", result)
                    raise HTTPException(409, result) from None
                logger.error("Approval %s remains unconfirmed; reconcile without another write.", draft.id)
                raise HTTPException(409, {"message": "Work creation is unconfirmed. Reconciliation will not repeat the SQL write.",
                                         "run_id": str(run_id), "proposal_id": str(draft.id),
                                         "status": "uncertain", "audit_url": "/invocations"}) from None
            except (SourceAuthorizationError, httpx.HTTPError, TimeoutError, ValidationError):
                logger.error("Approval %s remains unconfirmed; subsequent requests may reconcile but cannot repeat a write.", draft.id)
                raise HTTPException(409, {"message": "Work creation is unconfirmed. Reconciliation will not repeat the SQL write.",
                                         "run_id": str(run_id), "proposal_id": str(draft.id),
                                         "status": "uncertain", "audit_url": "/invocations"}) from None
            result = {"run_id": str(run_id), "proposal_id": str(draft.id), "status": "created",
                      "record": record, "production_write_executed": True}
            journal.save(approval_key + ":result", result)
            return result
    except LockTimeout:
        raise HTTPException(409, "This card is already being submitted; wait for its result.") from None


def create_delegated_app(
    root: Path, verifier: SourceTokenVerifier, source: SourceIdentity,
    factory: Callable[[Path, DelegatedCredential], AbstractAsyncContextManager[FoundrySupervisor]],
    *, run_timeout: float = 300, max_concurrent_runs: int = 4,
    decision_handler: Callable[[Path, DelegatedCredential, UUID, WorkOrderDecision], Awaitable[dict[str, object]]] | None = None,
    reconciliation_handler: Callable[[Path, DelegatedCredential, UUID, WorkOrderDecision], Awaitable[dict[str, object]]] | None = None,
    normalize_chat: Callable[[ChatInput], Awaitable[ChatInput]] | None = None,
) -> FastAPI:
    """Raw invocations boundary; the deployment must supply an authorized source factory."""
    if source.tenant_id != verifier.policy.tenant_id:
        raise ValueError("Source and delegated authorization tenants must match.")
    if not 0 < run_timeout <= 300 or max_concurrent_runs < 1:
        raise ValueError("Invocation deadlines and concurrency must be bounded.")
    capacity = asyncio.Semaphore(max_concurrent_runs)
    app = FastAPI()

    @app.post("/invocations")
    async def invoke(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 100000:
                raise HTTPException(413, "Invocation body exceeds its bounded size.")
            raw.extend(chunk)
        try:
            body = DelegatedInvocation.model_validate_json(raw)
        except ValidationError:
            logger.warning("Rejected invalid delegated invocation body.")
            # Pydantic errors include input values; never return them for credential ingress.
            raise HTTPException(422, "Invalid invocation body.") from None
        finally:
            raw.clear()
        credential: DelegatedCredential | None = None
        chat: ChatRequest | None = None
        state: Path | None = None
        requested_at = utc_now()
        deadline = requested_at + timedelta(seconds=run_timeout)

        def failed(status: int, category: str, message: str) -> HTTPException:
            if chat is None or state is None:
                return HTTPException(status, message)
            detail: dict[str, object] = {
                "message": message, "run_id": str(chat.run_id), "audit_url": "/invocations",
                "audit_available": True,
            }
            try:
                RunJournal(state / str(chat.run_id) / "receipts").save("failure", {
                    "run_id": str(chat.run_id), "status": "failed", "category": category,
                    "source": source.model_dump(mode="json"),
                    "requested_at": chat.requested_at.isoformat(), "failed_at": utc_now().isoformat(),
                })
            except (OSError, SourceFailure, ValueError) as error:
                logger.error("Cannot persist delegated failure audit for %s (%s).",
                             chat.run_id, type(error).__name__)
                detail.update(message="Orchestration failed and its execution audit could not be saved.",
                              audit_available=False)
                return HTTPException(500, detail)
            logger.error("Delegated run %s failed (%s).", chat.run_id, category)
            return HTTPException(status, detail)

        try:
            credential = await verifier.verify(body, deadline)
            principal_key = hashlib.sha256(
                f"{source.model_dump_json()}:{credential.principal_id}".encode(),
            ).hexdigest()
            state = root / principal_key
            if body.operation in ("decide", "reconcile"):
                handler = decision_handler if body.operation == "decide" else reconciliation_handler
                if handler is None:
                    raise HTTPException(503, "No work-order approval provider is configured.")
                if capacity.locked():
                    raise HTTPException(429, "Orchestration capacity is busy; retry later.")
                assert body.run_id is not None and body.decision is not None
                async with capacity:
                    async with asyncio.timeout(max(0, (deadline - utc_now()).total_seconds())):
                        return await handler(state, credential, body.run_id, body.decision)
            if body.operation == "evidence":
                assert body.run_id is not None
                journal = RunJournal(state / str(body.run_id) / "receipts")
                failure = journal.read("failure")
                if failure is not None:
                    return failure
                completed_answer = journal.read("answer")
                if completed_answer is None:
                    raise HTTPException(404, "No completed run is available for this signed-in user.")
                evidence = journal.read("evidence")
                if evidence is None:
                    raise HTTPException(409, "The completed run has no execution audit.")
                answer = ChatAnswer.model_validate(completed_answer)
                return {**evidence, "work_order_decisions": [{
                    "proposal_id": str(draft.id),
                    "intent": journal.read(f"approval:{draft.id}:intent"),
                    "result": journal.read(f"approval:{draft.id}:result"),
                } for draft in answer.proposals]}
            if capacity.locked():
                raise HTTPException(429, "Orchestration capacity is busy; retry later.")
            if utc_now() >= deadline:
                raise TimeoutError("Delegated authorization exceeded the invocation deadline.")
            assert body.chat is not None
            input_chat = await normalize_chat(body.chat) if normalize_chat else body.chat
            if input_chat.question != body.chat.question:
                raise ValueError("Intent normalization changed the operator question.")
            chat = ChatRequest(run_id=uuid4(), source=source, requested_at=requested_at, deadline=deadline,
                               historical_context=preceding_context(state, source, input_chat.previous_run_id),
                               **input_chat.model_dump())
            if body.stream:
                assert credential is not None and state is not None
                stream_credential = credential
                credential = None

                async def stream_run() -> AsyncIterator[bytes]:
                    events: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=64)

                    def line(value: dict[str, object]) -> bytes:
                        return (json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n").encode()

                    def terminal_event(detail: str) -> dict[str, object]:
                        return {
                            "type": "event",
                            "event": {
                                "id": f"{chat.run_id}:chief",
                                "role": "supervisor",
                                "status": "error",
                                "label": "Chief",
                                "detail": detail,
                                "timestamp": int(utc_now().timestamp() * 1000),
                            },
                        }

                    async def execute() -> ChatAnswer:
                        async with capacity:
                            async with asyncio.timeout(max(0, (deadline - utc_now()).total_seconds())):
                                async with factory(state, stream_credential) as owner:
                                    if owner.root != state or owner.source != source or owner.credential is not stream_credential:
                                        raise ValueError("Supervisor factory changed its user, source or delegated credential.")
                                    owner.set_event_sink(events.put_nowait)
                                    return await owner.run(chat)

                    task = asyncio.create_task(execute())
                    try:
                        yield line({"type": "run", "run_id": str(chat.run_id),
                                    "source": source.model_dump(mode="json")})
                        while not task.done():
                            try:
                                event = await asyncio.wait_for(events.get(), timeout=.25)
                            except TimeoutError:
                                continue
                            yield line({"type": "event", "event": event})
                        while not events.empty():
                            yield line({"type": "event", "event": events.get_nowait()})
                        answer = await task
                        if (answer.run_id != chat.run_id or answer.source != source
                                or answer.requested_at != chat.requested_at):
                            raise ValueError("Supervisor returned an answer for a different invocation.")
                        completed = answer.model_copy(update={
                            "audit_url": "/invocations", "presentation": client_presentation(answer),
                        })
                        yield line({"type": "answer", "answer": completed.model_dump(mode="json")})
                    except asyncio.CancelledError:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        raise
                    except SourceAuthorizationError:
                        yield line(terminal_event("Source authorization expired; no answer was certified."))
                        error = failed(401, "authorization", "Renew source access through the existing sign-in session.")
                        yield line({"type": "error", "status": error.status_code, "detail": error.detail})
                    except httpx.HTTPError as failure:
                        logger.error("Streamed source request failed (%s).", type(failure).__name__)
                        yield line(terminal_event("An authorized source request failed; no answer was certified."))
                        error = failed(502, "source_request",
                                       "An authorized source request failed; inspect the execution audit.")
                        yield line({"type": "error", "status": error.status_code, "detail": error.detail})
                    except TimeoutError:
                        yield line(terminal_event("The orchestration deadline elapsed; no answer was certified."))
                        error = failed(504, "deadline",
                                       "Orchestration exceeded its deadline; no answer is certified.")
                        yield line({"type": "error", "status": error.status_code, "detail": error.detail})
                    except Exception as failure:
                        if isinstance(failure, SourceFailure):
                            detail = str(failure).replace("\r", " ").replace("\n", " ")[:512]
                            logger.error("Streamed orchestration failed (SourceFailure: %s).", detail)
                        else:
                            logger.error("Streamed orchestration failed (%s).", type(failure).__name__)
                        yield line(terminal_event("Orchestration failed; no answer was certified."))
                        error = failed(500, "execution", "Orchestration failed; inspect the execution audit.")
                        yield line({"type": "error", "status": error.status_code, "detail": error.detail})
                    finally:
                        if not task.done():
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                        await stream_credential.close()

                return StreamingResponse(
                    stream_run(), media_type="application/x-ndjson",
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
                )
            async with capacity:
                async with asyncio.timeout(max(0, (deadline - utc_now()).total_seconds())):
                    async with factory(state, credential) as owner:
                        if owner.root != state or owner.source != source or owner.credential is not credential:
                            raise ValueError("Supervisor factory changed its user, source or delegated credential.")
                        answer = await owner.run(chat)
                        if (answer.run_id != chat.run_id or answer.source != source
                                or answer.requested_at != chat.requested_at):
                            raise ValueError("Supervisor returned an answer for a different invocation.")
                        return answer.model_copy(update={
                            "audit_url": "/invocations", "presentation": client_presentation(answer),
                        })
        except SourceAuthorizationError:
            logger.warning("Rejected delegated invocation authorization.")
            raise failed(401, "authorization", "Renew source access through the existing sign-in session.") from None
        except httpx.HTTPError as error:
            logger.error("Delegated invocation source request failed (%s).", type(error).__name__)
            raise failed(502, "source_request", "An authorized source request failed; inspect the execution audit.") from None
        except TimeoutError:
            logger.warning("Delegated invocation exceeded its bounded deadline.")
            raise failed(504, "deadline", "Orchestration exceeded its deadline; no answer is certified.") from None
        except HTTPException:
            raise
        except Exception as error:
            logger.error("Delegated invocation failed (%s).", type(error).__name__)
            raise failed(500, "execution", "Orchestration failed; inspect the execution audit.") from None
        finally:
            body.tokens.clear()
            if credential is not None:
                await credential.close()

    return app


def create_fabric_delegated_app(
    root: Path, verifier: SourceTokenVerifier, source: SourceIdentity, endpoint: str,
    *, bridge: Bridge | None = None, native_binding: NativeBinding | None = None,
    production_writes_enabled: bool = False,
) -> FastAPI:
    """Compose the request boundary with verified Fabric read-only primitives."""
    endpoint = verified_project_endpoint(endpoint)
    if native_binding is not None and native_binding.source != source:
        raise ValueError("Native binding differs from the configured source identity.")
    bridge = bridge or NodeSourceBridge()

    @asynccontextmanager
    async def factory(state: Path, credential: DelegatedCredential):
        sources = await LiveSources.open_delegated(credential, bridge)
        try:
            if sources.discovery.source != source:
                raise SourceFailure("Live source discovery differs from the configured invocation identity.")
            yield FoundrySupervisor(endpoint, credential, bridge, FabricBackendTools(sources), source, state,
                                    native_binding=native_binding)
        finally:
            await sources.close()

    async def decide(state: Path, credential: DelegatedCredential, run_id: UUID, decision: WorkOrderDecision,
                     *, require_previous: bool = False):
        if not decision.approved or not production_writes_enabled:
            return await decide_work_order(state, source, credential, run_id, decision, None,
                                           require_previous=require_previous)
        sources = await LiveSources.open_delegated(credential, bridge)
        try:
            if sources.discovery.source != source:
                raise SourceFailure("Approval source discovery differs from the configured identity.")
            return await decide_work_order(state, source, credential, run_id, decision, FabricBackendTools(sources),
                                           require_previous=require_previous)
        finally:
            await sources.close()

    async def normalize_chat(chat: ChatInput) -> ChatInput:
        intent = await bridge.call({"action": "chat_intent", "question": chat.question})
        return normalized_chat_input(chat, intent)

    async def reconcile(state: Path, credential: DelegatedCredential, run_id: UUID, decision: WorkOrderDecision):
        return await decide(state, credential, run_id, decision, require_previous=True)

    return create_delegated_app(root, verifier, source, factory, decision_handler=decide,
                               reconciliation_handler=reconcile, normalize_chat=normalize_chat)


class SupervisorRunner:
    def __init__(self, root: Path, factory: Callable[[Path], FoundrySupervisor], source: SourceIdentity):
        self.root, self.factory, self.source = root, factory, source
        root.mkdir(parents=True, exist_ok=True)
        self.tasks: dict[UUID, asyncio.Task[None]] = {}
        self.capacity = asyncio.Semaphore(4)

    def journal(self, run_id: UUID) -> RunJournal:
        return RunJournal(self.root / str(run_id) / "receipts")

    def schedule(self, request: ChatRequest) -> None:
        if request.run_id not in self.tasks:
            if len(self.tasks) >= 32:
                raise HTTPException(429, "Local supervisor queue is full; retry submission later.")
            task = asyncio.create_task(self.execute(request))
            self.tasks[request.run_id] = task
            task.add_done_callback(lambda done: self.finished(request.run_id, done))

    def finished(self, run_id: UUID, task: asyncio.Task[None]) -> None:
        self.tasks.pop(run_id, None)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("Supervisor run %s could not persist its failure (%s).", run_id, type(error).__name__)

    async def execute(self, request: ChatRequest) -> None:
        async with self.capacity:
            try:
                owner = self.factory(self.root)
                if owner.root != self.root or owner.source != self.source:
                    raise ValueError("Supervisor factory changed its configured state or source identity.")
                await owner.run(request)
            except Exception as error:
                logger.error("Supervisor run %s failed (%s).", request.run_id, type(error).__name__)
                journal = self.journal(request.run_id)
                if journal.read("answer") is not None:
                    logger.error("Rejected late failure for completed supervisor run %s.", request.run_id)
                    return
                # The local API exposes the error category, not SDK response bodies or tokens.
                from .live_sources import SourceFailure
                message = str(error) if isinstance(error, SourceFailure) else "Supervisor execution failed; inspect the audit."
                journal.save("failure", {"category": type(error).__name__, "message": message,
                                        "recorded_at": utc_now().isoformat()})

    async def close(self) -> None:
        tasks = tuple(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def create_app(
    root: Path, token: str, *, adapters: ReviewAdapters | None = None,
    source: SourceIdentity | None = None, step_timeout: float = 30,
    supervisor_factory: Callable[[Path], FoundrySupervisor] | None = None,
) -> FastAPI:
    if len(token) < 32 or not token.isascii() or any(character.isspace() for character in token):
        raise ValueError("A local development API token of at least 32 characters is required.")
    if (adapters is not None or supervisor_factory is not None) != (source is not None):
        raise ValueError("Adapters and verified source configuration must be supplied together.")
    store = Store(root)
    runner = LocalRunner(store, adapters, source, step_timeout=step_timeout) if adapters and source else None
    supervisor = SupervisorRunner(root / "chat", supervisor_factory, source) if supervisor_factory and source else None
    worker_lock = FileLock(root / "worker.lock")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        with worker_lock.acquire(timeout=0):
            if runner:
                for run_id in store.recoverable():
                    runner.schedule(run_id)
            if supervisor:
                for path in supervisor.root.iterdir():
                    if not path.is_dir():
                        continue
                    run_id = UUID(path.name)
                    journal = supervisor.journal(run_id)
                    saved = journal.read("request")
                    if saved is not None and journal.read("answer") is None and journal.read("failure") is None:
                        supervisor.schedule(ChatRequest.model_validate(saved))
            try:
                yield
            finally:
                if runner:
                    await runner.close()
                if supervisor:
                    await supervisor.close()

    app = FastAPI(title="Hydro durable workflow local validation", lifespan=lifespan)

    def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        if authorization is None or not secrets.compare_digest(authorization.encode(), f"Bearer {token}".encode()):
            raise HTTPException(401, "Local development authorization required.")

    def configured() -> LocalRunner:
        if runner is None:
            raise HTTPException(503, "No source/agent adapters configured. This is not a live agent deployment.")
        return runner

    def get_run(run_id: UUID) -> StoredRun:
        try:
            run = store.get(run_id)
        except KeyError as error:
            raise HTTPException(404, "Run not found.") from error
        if source and run.request.source != source:
            raise HTTPException(409, "Run belongs to a different source configuration.")
        return run

    @app.get("/healthz")
    async def health() -> dict[str, object]:
        return {"mode": "local-validation", "live_fabric_connected": False, "adapters_configured": runner is not None}

    @app.get("/readyz", dependencies=[Depends(authorize)])
    async def readiness() -> dict[str, str]:
        configured()
        return {"mode": "local-validation"}

    @app.post("/runs", status_code=202, dependencies=[Depends(authorize)])
    async def submit(body: ReviewInput, idempotency_key: Annotated[UUID, Header()]) -> StoredRun:
        active = configured()
        request = ReviewRequest(**body.model_dump(), source=active.source, run_id=idempotency_key)
        try:
            run = store.create(request)
        except Conflict as error:
            raise HTTPException(409, str(error)) from error
        if run.status == "queued":
            active.schedule(request.run_id)
        return run

    @app.get("/runs/{run_id}", dependencies=[Depends(authorize)])
    async def status(run_id: UUID) -> StoredRun:
        return get_run(run_id)

    def chat_status(active: SupervisorRunner, run_id: UUID) -> dict[str, object]:
        journal = active.journal(run_id)
        saved = journal.read("request")
        if saved is None:
            raise HTTPException(404, "Supervisor run not found.")
        request = ChatRequest.model_validate(saved)
        if request.source != active.source:
            raise HTTPException(409, "Supervisor run belongs to a different source configuration.")
        state = "completed" if journal.read("answer") is not None else (
            "failed" if journal.read("failure") is not None else (
                "queued_or_running" if run_id in active.tasks else "interrupted"
            )
        )
        return {"run_id": str(run_id), "state": state, "mode": "local-validation",
                "failure": journal.read("failure") if state == "failed" else None}

    def configured_supervisor() -> SupervisorRunner:
        if supervisor is None:
            raise HTTPException(503, "No backend Foundry supervisor and source tools are configured.")
        return supervisor

    @app.post("/chat/runs", status_code=202, dependencies=[Depends(authorize)])
    async def submit_chat(body: ChatInput, idempotency_key: Annotated[UUID, Header()]) -> dict[str, object]:
        active = configured_supervisor()
        journal = active.journal(idempotency_key)
        saved = journal.read("request")
        if saved is not None:
            request = ChatRequest.model_validate(saved)
            if any(getattr(request, key) != value for key, value in body.model_dump().items()):
                raise HTTPException(409, "Idempotency key belongs to a different supervisor request.")
            if request.source != active.source:
                raise HTTPException(409, "Supervisor source configuration changed.")
        else:
            if len(active.tasks) >= 32:
                raise HTTPException(429, "Local supervisor queue is full; retry submission later.")
            request = ChatRequest(**body.model_dump(), run_id=idempotency_key, source=active.source,
                                  deadline=utc_now() + timedelta(minutes=3))
            journal.save("request", request.model_dump(mode="json"))
        if journal.read("answer") is None and journal.read("failure") is None:
            active.schedule(request)
        return chat_status(active, idempotency_key)

    @app.get("/chat/runs/{run_id}", dependencies=[Depends(authorize)])
    async def status_chat(run_id: UUID) -> dict[str, object]:
        return chat_status(configured_supervisor(), run_id)

    @app.get("/chat/runs/{run_id}/answer", dependencies=[Depends(authorize)])
    async def answer_chat(run_id: UUID) -> ChatAnswer:
        active = configured_supervisor()
        if chat_status(active, run_id)["state"] != "completed":
            raise HTTPException(409, "A committed source-grounded supervisor answer is not available.")
        return ChatAnswer.model_validate(active.journal(run_id).read("answer"))

    @app.get("/chat/runs/{run_id}/evidence", dependencies=[Depends(authorize)])
    async def evidence_chat(run_id: UUID) -> dict[str, object]:
        active = configured_supervisor()
        if chat_status(active, run_id)["state"] != "completed":
            raise HTTPException(409, "A committed supervisor execution audit is not available.")
        evidence = active.journal(run_id).read("evidence")
        if evidence is None:
            raise HTTPException(409, "Supervisor source receipts are missing.")
        return evidence

    @app.get("/runs/{run_id}/events", dependencies=[Depends(authorize)])
    async def events(run_id: UUID, after: Annotated[int, Query(ge=0)] = 0) -> list[dict[str, object]]:
        get_run(run_id)
        return store.events(run_id, after)

    @app.get("/runs/{run_id}/answer", dependencies=[Depends(authorize)])
    async def answer(run_id: UUID) -> OperatorAnswer:
        try:
            return operator_answer(get_run(run_id))
        except Conflict as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/runs/{run_id}/evidence", dependencies=[Depends(authorize)])
    async def evidence(run_id: UUID) -> list[dict[str, object]]:
        run = get_run(run_id)
        if run.status not in ("waiting", "completed"):
            raise HTTPException(409, "Run has no final source-backed assessment.")
        assessment = run.outcome.review.assessment if run.outcome and run.outcome.review else (
            run.proposal.assessment if run.proposal else None
        )
        if assessment is None or assessment.evidence.request != run.request:
            raise HTTPException(409, "No assessment matches the persisted request.")
        receipts = [assessment.evidence.telemetry_receipt()]
        if assessment.evidence.work_orders_read_at is not None:
            receipts.append({
                "id": f"{run_id}:work-coverage", "tool": "work_source_adapter",
                "completedAt": assessment.evidence.work_orders_read_at.isoformat(),
                "result": {"open_work_numbers": list(assessment.evidence.open_work_numbers)},
            })
        return receipts

    @app.post("/runs/{run_id}/approval", status_code=202, dependencies=[Depends(authorize)])
    async def approval(run_id: UUID, decision: Approval) -> StoredRun:
        active = configured()
        get_run(run_id)
        try:
            run = store.approve(run_id, decision)
        except Conflict as error:
            raise HTTPException(409, str(error)) from error
        if run.status == "queued":
            active.schedule(run_id)
        return run

    @app.post("/runs/{run_id}/retry", status_code=202, dependencies=[Depends(authorize)])
    async def retry(run_id: UUID) -> StoredRun:
        active = configured()
        get_run(run_id)
        try:
            run = store.retry(run_id)
        except Conflict as error:
            raise HTTPException(409, str(error)) from error
        active.schedule(run_id)
        return run

    return app
