import asyncio
import logging
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

from agent_framework import WorkflowException
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from filelock import FileLock

from .contracts import Approval, Outcome, Proposal, ReviewInput, ReviewRequest, SourceIdentity
from .store import Conflict, Store, StoredRun
from .workflow import ReviewAdapters, build_workflow, checkpoint_store

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
            self.store.fail(run_id, f"{type(error).__name__}: {error}")

    async def execute(self, run_id: UUID) -> None:
        async with self.capacity:
            try:
                run = self.store.get(run_id)
                if run.request.source != self.source:
                    raise Conflict("Saved source identity differs from the configured deployment.")
                if run.status not in ("queued", "running"):
                    return
                if run.outcome:
                    self.store.finish(run.outcome)
                    self.store.event(run_id, "committed_outcome_recovered", "application")
                    return
                self.store.start(run_id)
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
                self.store.fail(run_id, f"{type(error).__name__}: {error}")

    async def close(self) -> None:
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)


def create_app(
    root: Path, token: str, *, adapters: ReviewAdapters | None = None,
    source: SourceIdentity | None = None, step_timeout: float = 30,
) -> FastAPI:
    if len(token) < 32 or not token.isascii() or any(character.isspace() for character in token):
        raise ValueError("A local development API token of at least 32 characters is required.")
    if (adapters is None) != (source is None):
        raise ValueError("Adapters and verified source configuration must be supplied together.")
    store = Store(root)
    runner = LocalRunner(store, adapters, source, step_timeout=step_timeout) if adapters and source else None
    worker_lock = FileLock(root / "worker.lock")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        with worker_lock.acquire(timeout=0):
            if runner:
                for run_id in store.recoverable():
                    runner.schedule(run_id)
            try:
                yield
            finally:
                if runner:
                    await runner.close()

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

    @app.get("/runs/{run_id}/events", dependencies=[Depends(authorize)])
    async def events(run_id: UUID, after: Annotated[int, Query(ge=0)] = 0) -> list[dict[str, object]]:
        get_run(run_id)
        return store.events(run_id, after)

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
