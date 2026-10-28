from datetime import datetime
import csv
from io import StringIO
from typing import TYPE_CHECKING, Literal

from pydantic import StrictFloat, StrictInt, StrictStr, model_validator

from .contracts import Contract, EvidenceReference, utc_now
from .store import Conflict, StoredRun

if TYPE_CHECKING:
    from .foundry_supervisor import ChatAnswer


class ClientVisualization(Contract):
    chartType: Literal["line", "bar", "scatter"]
    title: str
    xColumn: str
    yColumns: tuple[str, ...]
    xAxisTitle: str
    inlineCsvData: str


class ClientExecutionEvent(Contract):
    id: str
    role: Literal["supervisor", "qa", "rca", "work-order", "fabric-iq", "weather"]
    status: Literal["completed"] = "completed"
    label: str
    detail: str
    timestamp: int
    agentName: str
    responseId: str


class ClientPresentation(Contract):
    schema_version: Literal[1] = 1
    text: str
    visualizations: tuple[ClientVisualization, ...] = ()
    execution_events: tuple[ClientExecutionEvent, ...] = ()


ANALYSIS_ACTION_LABELS = {
    "create_work_order": "Create a work order",
    "covered_by_open_work": "Already covered by open work",
    "monitor": "Monitor",
    "no_action": "No action needed",
    "needs_clarification": "Needs clarification",
}

ANALYSIS_CATEGORY_LABELS = {
    "instrument_or_sensor": "instrument or sensor",
    "communication_or_ingestion": "communication or ingestion",
    "operating_conditions": "operating conditions",
    "equipment_condition": "equipment condition",
    "insufficient_evidence": "insufficient evidence",
}


def client_presentation(answer: "ChatAnswer") -> ClientPresentation:
    received_at = int(utc_now().timestamp() * 1000)
    events = tuple(ClientExecutionEvent(
        id=f"{answer.run_id}:{index}:{item.response_id}", role=item.role,
        label="Verified specialist execution receipt",
        detail=f"Version {item.version}; response {item.response_id}; measured duration {item.duration_ms:.0f} ms.",
        timestamp=received_at, agentName=item.agent_name, responseId=item.response_id,
    ) for index, item in enumerate(answer.specialists))
    if answer.native_response is not None:
        return ClientPresentation(text=answer.native_response, execution_events=events)
    native = getattr(answer, "native_answers", ())
    if native and not answer.tables and answer.analysis is None and not answer.proposals:
        return ClientPresentation(text="\n\n".join(item.text for item in native), execution_events=events)

    def literal(value: object) -> str:
        text = "" if value is None else str(value)
        text = text.replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
        for character in "|`*_[]<>":
            text = text.replace(character, "\\" + character)
        return text

    analysis = getattr(answer, "analysis", None)
    sections: list[str] = [
        "## Fabric Data Agent answer\n\n" + item.text for item in native
    ]
    if native:
        sections.append("## Hydro workflow result")
    if analysis is not None:
        sections.append("## Answer\n\n" + literal(analysis.answer))
        if analysis.findings:
            sections.append(
                f"## {'What the data shows' if answer.tables else 'From this conversation'}\n\n"
                + "\n".join(f"- {literal(item)}" for item in analysis.findings)
            )
        if analysis.reasoning:
            sections.append("## Why\n\n" + "\n".join(f"- {literal(item)}" for item in analysis.reasoning))
        if analysis.probable_causes:
            lines = []
            for cause in analysis.probable_causes:
                subject = f"{literal(cause.equipment_id)}: " if cause.equipment_id else ""
                lines.append(
                    f"- **{subject}{literal(cause.cause)}** ({ANALYSIS_CATEGORY_LABELS[cause.category]}, "
                    f"{literal(cause.likelihood)} likelihood). Evidence: {literal(cause.evidence)} "
                    f"To confirm: {literal(cause.to_confirm)}"
                )
            sections.append("## Probable causes\n\n" + "\n".join(lines))
        if analysis.recommendations:
            lines = []
            for item in analysis.recommendations:
                subject = f" {literal(item.equipment_id)}" if item.equipment_id else ""
                priority = f" ({literal(item.priority)} priority, suggested)" if item.priority else ""
                lines.append(
                    f"- **{ANALYSIS_ACTION_LABELS[item.action]}**{subject}{priority}: {literal(item.title)}. "
                    f"{literal(item.justification)}"
                )
            sections.append("## Recommended actions\n\n" + "\n".join(lines))
        if analysis.caveats:
            sections.append("## Caveats\n\n" + "\n".join(f"- {literal(item)}" for item in analysis.caveats))
        if answer.tables:
            sections.append(
                "Recommendations are advice written from the verified evidence below. No work order exists until you "
                "approve a staged draft. " + literal(answer.summary)
            )
    else:
        sections.append(literal(answer.summary))
    native_receipts = {item.evidence_id for item in native}
    charted_tables = {chart.table_id for chart in answer.charts}
    for table in answer.tables:
        # Native result-file projections remain audit/analysis inputs, not a second rendering of the source answer.
        if table.rows and table.id not in charted_tables and all(
            row.source.evidence_id in native_receipts for row in table.rows
        ):
            continue
        keys = [column.key for column in table.columns]
        lines = [f"## {literal(table.title)}", "",
                 "| " + " | ".join(literal(column.label) for column in table.columns) + " |",
                 "| " + " | ".join("---" for _ in keys) + " |"]
        lines.extend("| " + " | ".join(literal(row.values[key]) for key in keys) + " |" for row in table.rows)
        sections.append("\n".join(lines))
    if answer.limitations:
        sections.append("## Source limitations\n\n" + "\n".join(f"- {literal(item)}" for item in answer.limitations))
    tables = {table.id: table for table in answer.tables}
    charts = []
    for chart in answer.charts:
        table = tables[chart.table_id]
        keys = [column.key for column in table.columns]
        stream = StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(keys)
        writer.writerows([row.values[key] for key in keys] for row in table.rows)
        charts.append(ClientVisualization(
            chartType=chart.kind, title=table.title, xColumn=chart.x_key, yColumns=chart.y_keys,
            xAxisTitle=next(column.label for column in table.columns if column.key == chart.x_key),
            inlineCsvData=stream.getvalue(),
        ))
    return ClientPresentation(text="\n\n".join(sections), visualizations=tuple(charts), execution_events=events)


class AnswerColumn(Contract):
    key: str
    label: str
    kind: Literal["text", "number", "timestamp"] = "text"


class AnswerRow(Contract):
    values: dict[str, StrictStr | StrictInt | StrictFloat | None]
    source: EvidenceReference


class AnswerTable(Contract):
    id: str
    title: str
    columns: tuple[AnswerColumn, ...]
    rows: tuple[AnswerRow, ...]

    @model_validator(mode="after")
    def matching_columns(self) -> "AnswerTable":
        keys = [column.key for column in self.columns]
        if not keys or len(keys) != len(set(keys)):
            raise ValueError("Answer columns require distinct keys.")
        if any(set(row.values) != set(keys) for row in self.rows):
            raise ValueError("Every answer row must match the declared column keys exactly.")
        for row in self.rows:
            for column in self.columns:
                value = row.values[column.key]
                if value is None:
                    continue
                if column.kind == "number":
                    if not isinstance(value, (int, float)):
                        raise ValueError("Numeric columns require actual source numbers.")
                elif not isinstance(value, str):
                    raise ValueError("Text and timestamp columns require actual source strings.")
                elif column.kind == "timestamp" and datetime.fromisoformat(value).tzinfo is None:
                    raise ValueError("Timestamp columns require an explicit source timezone.")
        return self


class OperatorAnswer(Contract):
    schema_version: Literal[1] = 1
    run_id: str
    state: Literal["waiting", "completed"]
    equipment_id: str
    conclusion: Literal["cause_undetermined"] = "cause_undetermined"
    work_decision: Literal["approval_required", "rejected", "validation_work_recorded", "no_draft", "needs_clarification"]
    production_write_executed: Literal[False] = False
    tables: tuple[AnswerTable, ...]
    missing_evidence: tuple[str, ...]
    missing_sources: tuple[str, ...]
    audit_url: str


def operator_answer(run: StoredRun) -> OperatorAnswer:
    if run.status not in ("waiting", "completed"):
        raise Conflict("A verified proposal or committed outcome is required before presenting an answer.")
    assessment = run.outcome.review.assessment if run.outcome and run.outcome.review else (
        run.proposal.assessment if run.proposal else None
    )
    if assessment is None or assessment.evidence.request != run.request:
        raise Conflict("The answer has no assessment matching its persisted request.")
    evidence = assessment.evidence
    receipt = evidence.telemetry_receipt()
    # Use the same serialized source fields as the actual specialist handoff.
    tables = [
        AnswerTable(
            id="measurements", title="Returned measurements",
            columns=(
                AnswerColumn(key="metric", label="Measurement"),
                AnswerColumn(key="value", label="Value", kind="number"),
                AnswerColumn(key="unit", label="Unit"),
                AnswerColumn(key="quality", label="Signal quality"),
                AnswerColumn(key="event_time", label="Measurement time (UTC)", kind="timestamp"),
            ),
            rows=tuple(
                AnswerRow(
                    values={
                        "metric": observation.metric, "value": observation.value, "unit": observation.unit,
                        "quality": observation.quality, "event_time": observation.event_time.isoformat(),
                    },
                    source=EvidenceReference(evidence_id=str(receipt["id"]), path=f"/rows/{index}"),
                ) for index, observation in enumerate(evidence.observations)
            ),
        ),
    ]
    if evidence.work_orders_read_at is not None:
        tables.append(AnswerTable(
            id="open-work", title="Returned open work-order numbers",
            columns=(AnswerColumn(key="workOrderNumber", label="Work order"),),
            rows=tuple(AnswerRow(
                values={"workOrderNumber": number},
                source=EvidenceReference(evidence_id=f"{run.request.run_id}:work-coverage",
                                         path=f"/open_work_numbers/{index}"),
            ) for index, number in enumerate(evidence.open_work_numbers)),
        ))
    state: Literal["waiting", "completed"] = run.status
    return OperatorAnswer(
        run_id=str(run.request.run_id), state=state, equipment_id=evidence.equipment_id,
        work_decision=run.outcome.status if run.outcome else "approval_required",
        tables=tuple(tables), missing_evidence=assessment.missing_evidence,
        missing_sources=evidence.missing_sources, audit_url=f"/runs/{run.request.run_id}/evidence",
    )
