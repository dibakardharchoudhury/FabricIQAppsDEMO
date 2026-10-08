import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field

from .contracts import Approval, Contract, Outcome, Proposal, ReviewRequest, utc_now


class Conflict(ValueError):
    pass


class StoredRun(Contract):
    request: ReviewRequest
    status: Literal["queued", "running", "waiting", "completed", "failed"]
    proposal: Proposal | None = None
    approval: Approval | None = None
    outcome: Outcome | None = None
    error: str | None = None
    retries: int = Field(ge=0, le=2)


class Store:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.database = root / "runs.sqlite"
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, request TEXT NOT NULL,
                    status TEXT NOT NULL, proposal TEXT, approval TEXT, outcome TEXT,
                    error TEXT, retries INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL, kind TEXT NOT NULL,
                    executor TEXT, recorded_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS validation_work (
                    run_id TEXT PRIMARY KEY, digest TEXT NOT NULL, work_id TEXT NOT NULL
                );
            """)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        finally:
            db.close()

    def create(self, request: ReviewRequest) -> StoredRun:
        encoded = request.model_dump_json()
        with self.transaction() as db:
            row = db.execute("SELECT request FROM runs WHERE id=?", (str(request.run_id),)).fetchone()
            if row and row["request"] != encoded:
                raise Conflict("Idempotency key already belongs to a different request.")
            if not row:
                db.execute(
                    "INSERT INTO runs(id, request, status) VALUES (?, ?, 'queued')",
                    (str(request.run_id), encoded),
                )
        return self.get(request.run_id)

    def get(self, run_id: UUID) -> StoredRun:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (str(run_id),)).fetchone()
        if row is None:
            raise KeyError(str(run_id))
        return StoredRun.model_validate({
            "request": json.loads(row["request"]), "status": row["status"],
            "proposal": json.loads(row["proposal"]) if row["proposal"] else None,
            "approval": json.loads(row["approval"]) if row["approval"] else None,
            "outcome": json.loads(row["outcome"]) if row["outcome"] else None,
            "error": row["error"], "retries": row["retries"],
        })

    def recoverable(self) -> list[UUID]:
        with self.transaction() as db:
            return [UUID(row[0]) for row in db.execute(
                "SELECT id FROM runs WHERE status IN ('queued', 'running')"
            )]

    def start(self, run_id: UUID) -> None:
        with self.transaction() as db:
            updated = db.execute(
                "UPDATE runs SET status='running', error=NULL WHERE id=? AND status IN ('queued','running')",
                (str(run_id),),
            ).rowcount
            if updated != 1:
                raise Conflict("Run is not queued or recoverable.")

    def wait(self, proposal: Proposal) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE runs SET status='waiting', proposal=? WHERE id=?",
                (proposal.model_dump_json(), str(proposal.request.run_id)),
            )

    def approve(self, run_id: UUID, approval: Approval) -> StoredRun:
        with self.transaction() as db:
            row = db.execute("SELECT status, proposal, approval FROM runs WHERE id=?", (str(run_id),)).fetchone()
            if row is None:
                raise KeyError(str(run_id))
            if row["approval"]:
                if Approval.model_validate_json(row["approval"]) != approval:
                    raise Conflict("The recorded human decision cannot be changed.")
            else:
                if row["status"] != "waiting" or not row["proposal"]:
                    raise Conflict("Run is not waiting for human approval.")
                proposal = Proposal.model_validate_json(row["proposal"])
                if approval.proposal_digest != proposal.digest():
                    raise Conflict("Approval does not match the displayed proposal.")
                if approval.approved and proposal.expires_at <= utc_now():
                    raise Conflict("Proposal expired; request a new review.")
                db.execute(
                    "UPDATE runs SET status='queued', approval=? WHERE id=?",
                    (approval.model_dump_json(), str(run_id)),
                )
        return self.get(run_id)

    def finish(self, outcome: Outcome) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE runs SET status='completed', outcome=?, error=NULL WHERE id=?",
                (outcome.model_dump_json(), str(outcome.run_id)),
            )

    def fail(self, run_id: UUID, message: str) -> None:
        with self.transaction() as db:
            db.execute("UPDATE runs SET status='failed', error=? WHERE id=?", (message, str(run_id)))

    def retry(self, run_id: UUID) -> StoredRun:
        with self.transaction() as db:
            changed = db.execute(
                "UPDATE runs SET status='queued', error=NULL, retries=retries+1 "
                "WHERE id=? AND status='failed' AND retries<2", (str(run_id),)
            ).rowcount
            if changed != 1:
                raise Conflict("Only failed runs can retry, at most twice.")
        return self.get(run_id)

    def event(self, run_id: UUID, kind: str, executor: str | None) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO events(run_id,kind,executor,recorded_at) VALUES (?,?,?,?)",
                (str(run_id), kind, executor, utc_now().isoformat()),
            )

    def events(self, run_id: UUID, after: int = 0) -> list[dict[str, object]]:
        self.get(run_id)
        with self.transaction() as db:
            return [dict(row) for row in db.execute(
                "SELECT sequence,kind,executor,recorded_at FROM events "
                "WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT 200", (str(run_id), after)
            )]

    def record_decision(self, proposal: Proposal, approval: Approval) -> Outcome:
        run_id, digest = str(proposal.request.run_id), proposal.digest()
        with self.transaction() as db:
            row = db.execute("SELECT proposal,approval,outcome FROM runs WHERE id=?", (run_id,)).fetchone()
            if (approval.proposal_digest != digest or row is None
                    or row["proposal"] != proposal.model_dump_json()
                    or row["approval"] != approval.model_dump_json()):
                raise Conflict("A matching persisted human approval is required before a validation write.")
            if row["outcome"]:
                return Outcome.model_validate_json(row["outcome"])
            if approval.approved:
                if proposal.expires_at <= utc_now():
                    raise Conflict("Proposal expired before execution.")
                work_id = f"VALIDATION-{run_id}"
                db.execute(
                    "INSERT INTO validation_work(run_id,digest,work_id) VALUES (?,?,?)",
                    (run_id, digest, work_id),
                )
                outcome = Outcome(
                    run_id=proposal.request.run_id, status="validation_work_recorded",
                    validation_work_id=work_id,
                )
            else:
                outcome = Outcome(run_id=proposal.request.run_id, status="rejected")
            # The business effect and its recoverable terminal receipt commit together.
            db.execute("UPDATE runs SET outcome=? WHERE id=?", (outcome.model_dump_json(), run_id))
            return outcome
