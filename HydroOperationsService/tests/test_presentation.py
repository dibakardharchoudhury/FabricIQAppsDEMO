import tempfile
import csv
from io import StringIO
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError

from hydro_orchestrator.presentation import AnswerTable, client_presentation
from hydro_orchestrator.foundry_supervisor import ChatAnswer, PlannedChart, SpecialistResult
from hydro_orchestrator.service import create_app
from hydro_orchestrator.store import Store
from hydro_orchestrator.contracts import ReviewRequest, utc_now
from test_durable_service import BODY, HEADERS, SOURCE, TOKEN, TestAdapters, decision, wait_for


class PresentationTests(unittest.TestCase):
    def test_backend_presentation_uses_exact_source_columns_and_chart_values_without_inference(self):
        table = AnswerTable.model_validate({
            "id": "source", "title": "Returned readings",
            "columns": [{"key": "asset", "label": "Asset"}, {"key": "value", "label": "Value", "kind": "number"}],
            "rows": [
                {"values": {"asset": "A|[B]\n<C>", "value": 75.123456789},
                 "source": {"evidence_id": "read", "path": "/rows/0"}},
                {"values": {"asset": "Missing", "value": None},
                 "source": {"evidence_id": "read", "path": "/rows/1"}},
            ],
        })
        answer = ChatAnswer(
            run_id=uuid4(), source=SOURCE, requested_at=utc_now(), source_read_times=(),
            summary="Verified source rows.", tables=(table,), charts=(), limitations=("No physical diagnosis.",),
            cell_sources=(), specialists=(), audit_url="/invocations",
        )
        plain = client_presentation(answer)
        self.assertEqual(plain.visualizations, ())
        self.assertIn("A\\|\\[B\\]\\n\\<C\\>", plain.text)
        self.assertIn("75.123456789", plain.text)
        self.assertNotIn("0.0", plain.text)
        charted = client_presentation(answer.model_copy(update={
            "charts": (PlannedChart(table_id="source", kind="bar", x_key="asset", y_keys=("value",)),),
        }))
        chart = charted.visualizations[0]
        self.assertEqual((chart.xColumn, chart.yColumns), ("asset", ("value",)))
        rows = list(csv.reader(StringIO(chart.inlineCsvData)))
        self.assertEqual(rows, [["asset", "value"], ["A|[B]\n<C>", "75.123456789"], ["Missing", ""]])
        specialist = SpecialistResult(role="qa", agent_name="existing-agent-fixture", version="14",
                                      response_id="source-response-fixture", duration_ms=123.0,
                                      input_digest="a" * 64, text="Test execution receipt.")
        receipt = client_presentation(answer.model_copy(update={"specialists": (specialist,)})).execution_events[0]
        self.assertEqual(receipt.agentName, specialist.agent_name)
        self.assertEqual(receipt.responseId, specialist.response_id)
        self.assertEqual(receipt.role, specialist.role)
        self.assertIn("123 ms", receipt.detail)
        self.assertGreaterEqual(receipt.timestamp, int(answer.requested_at.timestamp() * 1000))

    def test_keyed_rows_reject_wrong_columns_missing_values_and_numeric_coercion(self):
        row = {"values": {"value": 0.0, "unit": "C"}, "source": {"evidence_id": "receipt", "path": "/rows/0"}}
        payload = {"id": "readings", "title": "Readings", "columns": [
            {"key": "value", "label": "Value", "kind": "number"}, {"key": "unit", "label": "Unit"},
        ], "rows": [row]}
        self.assertEqual(AnswerTable.model_validate(payload).rows[0].values["value"], 0.0)
        for values in (
            {"value": 0.0}, {"value": 0.0, "unit": "C", "invented": 3},
            {"value": float("nan"), "unit": "C"}, {"value": "0.0", "unit": "C"},
            {"value": "C", "unit": 0.0}, {"value": True, "unit": "C"},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                AnswerTable.model_validate({**payload, "rows": [{**row, "values": values}]})
        with self.assertRaises(ValidationError):
            AnswerTable.model_validate({**payload, "columns": [payload["columns"][0]] * 2})

    def test_answer_copies_values_from_evidence_and_every_citation_resolves(self):
        with tempfile.TemporaryDirectory() as root:
            adapters = TestAdapters()
            app = create_app(Path(root), TOKEN, adapters=adapters, source=SOURCE)
            with TestClient(app) as client:
                run_id = str(uuid4())
                client.post("/runs", json=BODY, headers={**HEADERS, "Idempotency-Key": run_id}).raise_for_status()
                run = wait_for(client, run_id, "waiting")
                answer = client.get(f"/runs/{run_id}/answer", headers=HEADERS)
                answer.raise_for_status()
                data = answer.json()
                self.assertEqual(data["work_decision"], "approval_required")
                self.assertEqual(data["conclusion"], "cause_undetermined")
                self.assertFalse(data["production_write_executed"])
                self.assertNotIn("charts", data)
                receipts = client.get(data["audit_url"], headers=HEADERS).json()
                evidence = run["proposal"]["assessment"]["evidence"]
                measurement = data["tables"][0]["rows"][0]["values"]
                self.assertEqual(measurement["metric"], evidence["observations"][0]["metric"])
                self.assertEqual(measurement["value"], evidence["observations"][0]["value"])
                self.assertEqual(measurement["unit"], "C")
                self.assertEqual(measurement["quality"], "BAD")
                self.assertEqual(data["tables"][1]["rows"][0]["values"], {"workOrderNumber": "TEST-WO-1"})
                by_id = {receipt["id"]: receipt["result"] for receipt in receipts}
                for table in data["tables"]:
                    for row in table["rows"]:
                        reference = row["source"]
                        value = by_id[reference["evidence_id"]]
                        for segment in reference["path"].split("/")[1:]:
                            value = value[int(segment)] if isinstance(value, list) else value[segment]
                        self.assertIsNotNone(value)
                client.post(f"/runs/{run_id}/approval", json=decision(run, False), headers=HEADERS).raise_for_status()
                wait_for(client, run_id, "completed")
                final = client.get(f"/runs/{run_id}/answer", headers=HEADERS).json()
                self.assertEqual(final["work_decision"], "rejected")
                self.assertEqual(final["tables"], data["tables"])
                self.assertEqual((adapters.reads, adapters.investigations, adapters.proposals), (1, 1, 1))
                self.assertEqual(client.get(f"/runs/{run_id}/answer").status_code, 401)

    def test_failed_or_queued_run_cannot_return_success_shaped_answer(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            request = ReviewRequest(**BODY, source=SOURCE, run_id=uuid4())
            store.create(request)
            app = create_app(Path(root), TOKEN)
            with TestClient(app) as client:
                self.assertEqual(client.get(f"/runs/{request.run_id}/answer", headers=HEADERS).status_code, 409)
                store.fail(request.run_id, "Source read failed")
                self.assertEqual(client.get(f"/runs/{request.run_id}/answer", headers=HEADERS).status_code, 409)
                self.assertEqual(client.get(f"/runs/{request.run_id}/evidence", headers=HEADERS).status_code, 409)
