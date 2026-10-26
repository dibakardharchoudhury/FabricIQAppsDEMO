import asyncio
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from azure.core.credentials import AccessToken
from azure.identity.aio import AzureCliCredential
from fastapi.testclient import TestClient
from pydantic import ValidationError
from jsonschema import Draft202012Validator

from hydro_orchestrator.contracts import ReviewRequest, WorkOrderDraft, WorkOrderEdits, utc_now
from hydro_orchestrator.foundry_supervisor import (
    ChatRequest, OperatorAnswerContract, SourceCatalog, governed_datasets,
)
from hydro_orchestrator.live_sources import Discovery, FabricBackendTools, LiveSources, NodeSourceBridge, SourceFailure
from hydro_orchestrator.service import create_app
from test_durable_service import BODY, HEADERS, SOURCE, TOKEN, TestAdapters, wait_for


class GovernedBridgeCall(AsyncMock):
    async def _execute_mock_call(self, *args, **kwargs):
        response = await super()._execute_mock_call(*args, **kwargs)
        payload = args[0] if args and isinstance(args[0], dict) else {}
        if payload.get("action") != "tool_read" or not isinstance(response, dict):
            return response
        result = response.get("result")
        if not isinstance(result, dict) or "_evidence_datasets" in result:
            return response
        tool = payload.get("tool")
        arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
        scope = "focus" if arguments.get("equipment_id") or arguments.get("equipment_ids") else "population"
        mapping = {
            "query_telemetry": ("telemetry", "rows", ("opcua_node_id", "event_time")),
            "run_kql": ("telemetry", "rows", ("opcua_node_id", "event_time")),
            "query_station_power": ("station_power", "rows", ("Station",)),
            "query_signal_quality_snapshot": ("signal_quality", "rows", ("opcua_node_id", "event_time")),
            "query_turbine_temperature_snapshot": ("temperature", "rows", ("opcua_node_id", "event_time")),
            "propose_work_order": ("work_orders", "existing_work", ("workOrderNumber",)),
        }
        if tool == "query_assets":
            mapping[tool] = (str(arguments.get("entity")), "rows", (f"{str(arguments.get('entity')).removesuffix('s')}_id",))
        elif tool == "query_operations":
            entity = str(arguments.get("entity"))
            mapping[tool] = (entity, "rows", ("workOrderNumber" if entity == "work_orders" else "id",))
        descriptors = []
        if tool == "query_work_backlog":
            group_by = "facility_id" if arguments.get("group_by") == "facility" else "equipment_id"
            descriptors = [{
                "kind": "work_backlog", "field": "rows", "authority": "direct", "scope": "population",
                "complete": True, "identity_fields": [group_by], "group_by": group_by, "role": "primary",
                "limitations": [],
            }, {
                "kind": "work_orders", "field": "open_work_orders", "authority": "direct", "scope": "population",
                "complete": True, "identity_fields": ["workOrderNumber"], "group_by": None, "role": "primary",
                "limitations": [],
            }]
        elif tool in mapping:
            kind, field, identity = mapping[tool]
            descriptors = [{
                "kind": kind, "field": field, "authority": "direct", "scope": scope,
                "complete": result.get("truncated") is False or tool in {
                    "query_station_power", "query_signal_quality_snapshot",
                    "query_turbine_temperature_snapshot", "propose_work_order",
                },
                "identity_fields": list(identity), "group_by": None,
                "role": "audit" if tool == "propose_work_order" else "primary", "limitations": [],
            }]
        if descriptors:
            result["_evidence_datasets"] = descriptors
        return response


class FakeBridgeWorker:
    """Scripted stand-in for the Node worker process: stdin accepts request lines, stdout yields reply lines."""

    def __init__(self, replies):
        self.returncode = None
        self.killed = False
        self.requests = []
        self.stdin = self.stdout = self
        self._replies = replies
        self._lines = asyncio.Queue()

    def write(self, data):
        message = json.loads(data)
        self.requests.append(message)
        for line in self._replies(message):
            self._lines.put_nowait(line)

    async def drain(self):
        return None

    def is_closing(self):
        return self.returncode is not None

    def close(self):
        if self.returncode is None:
            self.returncode = 0
        self._lines.put_nowait(b"")

    async def readline(self):
        return await self._lines.get()

    def kill(self):
        self.killed = True
        self.returncode = -9
        self._lines.put_nowait(b"")

    async def wait(self):
        return self.returncode


def reader():
    bridge = AsyncMock()
    bridge.call = GovernedBridgeCall()
    credential = AsyncMock(spec=AzureCliCredential)
    credential.get_token.return_value = AccessToken("synthetic-token-never-log", 9999999999)
    discovery = Discovery(
        source=SOURCE.model_copy(update={"configuration_digest": "a" * 64}),
        cluster="https://test.z.kusto.fabric.microsoft.com", database="test",
    )
    return LiveSources(credential, bridge, discovery)


class LiveSourceTests(unittest.IsolatedAsyncioTestCase):
    def chat(self, sources):
        return ChatRequest(run_id=uuid4(), source=sources.discovery.source, question="Read real source records.",
                           deadline=utc_now() + timedelta(minutes=5))

    def snapshot(self, request):
        return {
            "source": request.source.model_dump(mode="json"), "completed_at": utc_now().isoformat(),
            "result": {
                "population": {"inventory_complete": True, "work_inventory_complete": True,
                               "work_coverage_equipment_ids": ["T1", "T2"], "signals_without_readings": []},
                "rows": [{"equipment_id": "T1", "instrument_id": "I1", "opcua_node_id": "node-1",
                          "value": 77.5, "unit": "degC", "quality": "BAD",
                          "open_work_orders": [{"workOrderNumber": "WO1", "status": "Approved",
                                                "relation": "equipment-level"}]}],
                "row_count": 1, "truncated": False, "unresolved_nodes": [],
            },
        }

    async def test_operations_use_power_bi_execute_token_for_rayfin_exchange(self):
        sources = reader()
        request = self.chat(sources)
        sources.bridge.call.return_value = {
            "source": request.source.model_dump(mode="json"),
            "completed_at": utc_now().isoformat(),
            "result": {"rows": [], "row_count": 0, "truncated": False},
        }
        await FabricBackendTools(sources).execute("query_operations", {"entity": "work_orders"}, request)
        self.assertEqual(
            {call.args[0] for call in sources.credential.get_token.await_args_list},
            {
                "https://api.fabric.microsoft.com/.default",
                "https://analysis.windows.net/powerbi/api/.default",
            },
        )
        self.assertEqual(set(sources.bridge.call.call_args.args[0]["tokens"]), {"fabric", "graphql"})

    async def test_semantic_scope_resolution_is_source_bound_and_uses_delegated_tokens(self):
        sources = reader()
        sources.bridge.call.return_value = {
            "source": sources.discovery.source.model_dump(mode="json"),
            "matches": [{
                "kind": "equipment",
                "mention": "t09",
                "canonical_id": "EQUIP_RTI_T009",
                "label": "T009",
                "facility_id": "FACILITY_RTI_002",
                "matched_by": "tag",
            }],
            "read_completed_at": utc_now().isoformat(),
        }
        scope = await sources.resolve_scope("Create a work order for t09.")
        self.assertEqual(scope["matches"][0]["canonical_id"], "EQUIP_RTI_T009")
        self.assertEqual(
            {call.args[0] for call in sources.credential.get_token.await_args_list},
            {
                "https://api.fabric.microsoft.com/.default",
                "https://analysis.windows.net/powerbi/api/.default",
            },
        )

    async def test_backend_snapshots_use_only_required_resources_and_attest_returned_source_identities(self):
        sources = reader()
        request = self.chat(sources)
        tools = FabricBackendTools(sources)
        for name in sorted(tools.snapshot_tools):
            with self.subTest(tool=name):
                sources.credential.reset_mock()
                sources.bridge.call.return_value = self.snapshot(request)
                evidence = await tools.execute(name, {"lookback": "30m"}, request)
                self.assertEqual(evidence.column_units, {"value": "degC"})
                self.assertEqual(evidence.resolved_equipment_ids, ("T1",))
                self.assertEqual(evidence.work_coverage_equipment_ids, ("T1",))
                self.assertEqual(evidence.limitations, ())
                self.assertEqual(evidence.result["rows"][0]["open_work_orders"][0]["status"], "Approved")
                self.assertEqual({call.args[0] for call in sources.credential.get_token.await_args_list}, {
                    "https://api.fabric.microsoft.com/.default",
                    "https://analysis.windows.net/powerbi/api/.default",
                    f"{sources.discovery.cluster}/.default",
                })
                self.assertEqual(set(sources.bridge.call.call_args.args[0]["tokens"]), {"fabric", "graphql", "kusto"})
                self.assertFalse(evidence.production_write_executed)
                self.assertNotIn("synthetic-token", evidence.model_dump_json())

    async def test_snapshot_evidence_never_infers_ids_or_column_units_from_scope_arguments(self):
        sources = reader()
        request = self.chat(sources)
        result = self.snapshot(request)
        result["result"]["rows"].append({
            "equipment_id": "T2", "instrument_id": "I2", "opcua_node_id": "node-2", "value": 2,
            "unit": "bar", "quality": "BAD", "open_work_orders": [],
        })
        result["result"]["row_count"] = 2
        result["result"]["population"]["signals_without_readings"] = ["no-reading"]
        result["result"]["unresolved_nodes"] = ["unmapped"]
        result["result"]["truncated"] = True
        sources.bridge.call.return_value = result
        evidence = await FabricBackendTools(sources).execute("query_signal_quality_snapshot", {}, request)
        self.assertEqual(evidence.column_units, {})
        self.assertEqual(evidence.resolved_equipment_ids, ("T1", "T2"))
        self.assertEqual(len(evidence.limitations), 3)
        result["result"]["rows"] = []
        result["result"]["row_count"] = 0
        evidence = await FabricBackendTools(sources).execute("query_turbine_temperature_snapshot",
                                                           {"equipment_ids": ["not-observed"]}, request)
        self.assertEqual(evidence.resolved_equipment_ids, ())
        self.assertEqual(evidence.work_coverage_equipment_ids, ())
        self.assertEqual(evidence.column_units, {})

    async def test_snapshots_fail_closed_on_incomplete_or_unsafe_evidence_and_sql_failure(self):
        sources = reader()
        request = self.chat(sources)
        tools = FabricBackendTools(sources)
        patches = (
            lambda result: result["population"].update(inventory_complete=False),
            lambda result: result["population"].update(work_inventory_complete=False),
            lambda result: result["population"].update(work_coverage_equipment_ids=[]),
            lambda result: result["population"].update(work_coverage_equipment_ids=["T1", "T1"]),
            lambda result: result.update(row_count=2),
            lambda result: result["rows"][0].update(unit=""),
            lambda result: result["rows"][0].update(value="77.5"),
            lambda result: result["rows"][0].update(value=True),
            lambda result: result["rows"][0].update(value=float("nan")),
            lambda result: result["rows"][0].update(instrument_id=None),
            lambda result: result["rows"][0].update(open_work_orders=None),
        )
        for change in patches:
            payload = self.snapshot(request)
            change(payload["result"])
            sources.bridge.call.return_value = payload
            with self.assertRaises(SourceFailure):
                await tools.execute("query_turbine_temperature_snapshot", {}, request)
        sources.bridge.call.side_effect = SourceFailure("EXCHANGE_NOT_ENABLED")
        for name in tools.snapshot_tools:
            with self.assertRaisesRegex(SourceFailure, "EXCHANGE_NOT_ENABLED"):
                await tools.execute(name, {}, request)

    async def test_snapshot_readback_remains_isolated_to_request_identity_and_clock(self):
        sources = reader()
        request = self.chat(sources)
        tools = FabricBackendTools(sources)
        for source, clock in ((SOURCE, utc_now()),
                              (request.source, request.requested_at - timedelta(seconds=1)),
                              (request.source, utc_now() + timedelta(minutes=1))):
            payload = self.snapshot(request)
            payload.update(source=source.model_dump(mode="json"), completed_at=clock.isoformat())
            sources.bridge.call.return_value = payload
            with self.assertRaises(SourceFailure):
                await tools.execute("query_turbine_temperature_snapshot", {}, request)
        sources.credential.reset_mock()
        with self.assertRaises(SourceFailure):
            await tools.execute("query_turbine_temperature_snapshot", {},
                                request.model_copy(update={"source": SOURCE}))
        sources.credential.get_token.assert_not_awaited()

    async def test_backend_catalog_uses_real_shared_contracts_and_rejects_wrong_column_aliases(self):
        sources = reader()
        sources.bridge = NodeSourceBridge()
        catalog = SourceCatalog.model_validate(await FabricBackendTools(sources).catalog(self.chat(sources)))
        datasets = governed_datasets(catalog.context)
        self.assertEqual(
            set(datasets),
            {
                "facilities", "equipment", "instruments", "telemetry", "signal_quality",
                "temperature", "station_power", "work_orders", "work_backlog", "inspections",
                "notifications", "spare_parts", "asset_models", "native_claims",
                "weather_locations", "weather_areas", "weather_forecasts", "weather_observations",
            },
        )
        self.assertTrue(datasets["station_power"].limitations)
        self.assertIn("grouped_by_facility_bar", datasets["station_power"].presentations)
        self.assertEqual({tool.name for tool in catalog.tools}, FabricBackendTools.supported_tools)
        self.assertEqual(len(datasets["weather_forecasts"].identity_fields), 5)
        weather_schema = next(tool.parameters for tool in catalog.tools if tool.name == "query_weather")
        weather_validator = Draft202012Validator(weather_schema)
        self.assertTrue(weather_validator.is_valid({"entity": "weather_forecasts", "columns": ["source_id", "temperature"]}))
        self.assertFalse(weather_validator.is_valid({"entity": "weather_forecasts", "columns": ["equipmentId"]}))
        schema = next(tool.parameters for tool in catalog.tools if tool.name == "query_operations")
        validator = Draft202012Validator(schema)
        self.assertTrue(validator.is_valid({"entity": "work_orders", "columns": ["equipmentId", "status"]}))
        for arguments in (
            {"entity": "work_orders", "columns": ["equipment_id"]},
            {"entity": "work_orders", "where": [{"column": "equipment_id", "op": "eq", "value": "T1"}]},
            {"entity": "work_orders", "columns": ["status", "status"]},
            {"entity": "work_orders", "limit": 0},
            {"entity": "work_orders", "limit": 501},
        ):
            with self.subTest(arguments=arguments):
                self.assertFalse(validator.is_valid(arguments))
        sources.credential.get_token.assert_not_awaited()

    async def test_backend_sql_read_uses_fabric_discovery_and_power_bi_exchange_tokens(self):
        sources = reader()
        request = self.chat(sources)
        arguments = {"entity": "work_orders", "columns": ["id", "equipmentId", "status"]}
        rows = [{"id": "WO1", "equipmentId": "T1", "status": "Approved"}]
        sources.bridge.call.return_value = {
            "source": request.source.model_dump(mode="json"), "completed_at": utc_now().isoformat(),
            "result": {"rows": rows, "row_count": 1, "truncated": False},
        }
        evidence = await FabricBackendTools(sources).execute("query_operations", arguments, request)
        self.assertEqual(evidence.result["rows"], rows)
        self.assertEqual(evidence.arguments, arguments)
        self.assertEqual(evidence.source, request.source)
        self.assertFalse(evidence.production_write_executed)
        self.assertEqual(
            {call.args[0] for call in sources.credential.get_token.await_args_list},
            {
                "https://api.fabric.microsoft.com/.default",
                "https://analysis.windows.net/powerbi/api/.default",
            },
        )
        self.assertEqual(
            sources.bridge.call.call_args.args[0]["tokens"],
            {"fabric": "synthetic-token-never-log", "graphql": "synthetic-token-never-log"},
        )
        self.assertNotIn("synthetic-token", evidence.model_dump_json())

    async def test_weather_read_preserves_composite_identity_units_and_delegated_tokens(self):
        sources = reader()
        request = self.chat(sources)
        arguments = {"entity": "weather_forecasts"}
        identity = ("source_id", "target_kind", "target_id", "reference_time_utc", "valid_time_utc")
        rows = [{
            "source_id": "aurora", "target_kind": "location", "target_id": "F001",
            "reference_time_utc": "2026-10-23T00:00:00Z",
            "valid_time_utc": "2026-10-23T06:00:00Z", "temperature": 8, "precipitation": 4,
        }]
        sources.bridge.call.return_value = {
            "source": request.source.model_dump(mode="json"), "completed_at": utc_now().isoformat(),
            "result": {"rows": rows, "row_count": 1, "truncated": False, "_evidence_datasets": [{
                "kind": "weather_forecasts", "field": "rows", "authority": "direct",
                "scope": "population", "complete": True, "identity_fields": identity,
                "group_by": None, "role": "primary", "limitations": [],
            }]},
        }
        evidence = await FabricBackendTools(sources).execute("query_weather", arguments, request)
        self.assertEqual(evidence.datasets[0].identity_fields, identity)
        self.assertEqual(evidence.column_units["temperature"], "degC")
        self.assertEqual(evidence.column_units["precipitation"], "mm")
        self.assertEqual(evidence.result["rows"], rows)
        self.assertFalse(evidence.production_write_executed)
        self.assertEqual(set(sources.bridge.call.call_args.args[0]["tokens"]), {"fabric", "graphql"})
        self.assertNotIn("synthetic-token", evidence.model_dump_json())

    async def test_complete_equipment_work_read_attests_coverage_without_claiming_filtered_reads(self):
        sources = reader()
        request = self.chat(sources)
        sources.bridge.call.return_value = {
            "source": request.source.model_dump(mode="json"), "completed_at": utc_now().isoformat(),
            "result": {"rows": [], "row_count": 0, "truncated": False},
        }
        tools = FabricBackendTools(sources)
        complete = {
            "entity": "work_orders",
            "where": [{"column": "equipmentId", "op": "eq", "value": "T1"}],
        }
        evidence = await tools.execute("query_operations", complete, request)
        self.assertEqual(evidence.work_coverage_equipment_ids, ("T1",))
        open_work = {**complete, "where": [
            *complete["where"],
            {"column": "status", "op": "neq", "value": "Completed"},
            {"column": "status", "op": "neq", "value": "Cancelled"},
        ]}
        evidence = await tools.execute("query_operations", open_work, request)
        self.assertEqual(evidence.work_coverage_equipment_ids, ("T1",))

        complete_population = {
            "entity": "work_orders",
            "where": [
                {"column": "equipmentId", "op": "in", "value": ["T1", "T2"]},
                {"column": "status", "op": "neq", "value": "Completed"},
                {"column": "status", "op": "neq", "value": "Cancelled"},
            ],
        }
        evidence = await tools.execute("query_operations", complete_population, request)
        self.assertEqual(evidence.work_coverage_equipment_ids, ("T1", "T2"))

        for arguments in (
            {**complete, "limit": 10},
            {"entity": "work_orders", "where": [
                {"column": "equipmentId", "op": "eq", "value": "T1"},
                {"column": "status", "op": "eq", "value": "Approved"},
            ]},
            {"entity": "work_orders", "where": [
                {"column": "equipmentId", "op": "in", "value": ["T1", "T2"]},
                {"column": "status", "op": "in", "value": ["Draft", "Approved"]},
            ]},
        ):
            with self.subTest(arguments=arguments):
                evidence = await tools.execute("query_operations", arguments, request)
                self.assertEqual(evidence.work_coverage_equipment_ids, ())

    async def test_facility_backlog_attests_complete_open_work_and_derived_count_units(self):
        sources = reader()
        request = self.chat(sources)
        result = {
            "group_by": "facility",
            "rows": [
                {"facility_id": "F1", "facility_name": "North",
                 "equipment_with_open_work": 1, "open_work_order_count": 2},
                {"facility_id": "F2", "facility_name": "South",
                 "equipment_with_open_work": 0, "open_work_order_count": 0},
            ],
            "open_work_orders": [
                {"workOrderNumber": "WO-1", "equipmentId": "E1", "status": "Draft"},
                {"workOrderNumber": "WO-2", "equipmentId": "E1", "status": "Approved"},
            ],
            "unmatched": [],
            "presentation": {
                "table_field": "rows",
                "chart": {"kind": "bar", "x_key": "facility_id", "y_keys": ["open_work_order_count"]},
            },
            "semantics": "Complete open work joined through authoritative identities.",
            "read_started_at_utc": "2026-10-10T00:59:59Z",
            "read_completed_at_utc": "2026-10-10T01:00:00Z",
        }
        sources.bridge.call.return_value = {
            "source": request.source.model_dump(mode="json"), "completed_at": utc_now().isoformat(),
            "result": result,
        }
        tools = FabricBackendTools(sources)
        evidence = await tools.execute("query_work_backlog", {"group_by": "facility"}, request)
        self.assertEqual(evidence.column_units, {
            "equipment_with_open_work": "equipment",
            "open_work_order_count": "work orders",
        })
        self.assertEqual(evidence.resolved_equipment_ids, ("E1",))
        self.assertEqual(evidence.work_coverage_equipment_ids, ("E1",))
        self.assertIn("authoritative identities", evidence.limitations[0])
        self.assertEqual(sources.credential.get_token.await_count, 2)
        self.assertIn("graphql", sources.bridge.call.call_args.args[0]["tokens"])
        equipment_result = {
            **result,
            "group_by": "equipment",
            "presentation": {
                "table_field": "rows",
                "chart": {"kind": "bar", "x_key": "equipment_id", "y_keys": ["open_work_order_count"]},
            },
            "rows": [
                {"equipment_id": "E1", "tag": "T1", "facility_id": "F1",
                 "facility_name": "North", "open_work_order_count": 2},
            ],
        }
        sources.bridge.call.return_value["result"] = equipment_result
        evidence = await tools.execute("query_work_backlog", {"group_by": "equipment"}, request)
        self.assertEqual(evidence.column_units, {"open_work_order_count": "work orders"})

        for invalid in (
            {**result, "rows": [{**result["rows"][0], "open_work_order_count": 1}]},
            {**result, "open_work_orders": [*result["open_work_orders"],
                                            {"workOrderNumber": "WO-3", "equipmentId": "E2",
                                             "status": "Completed"}]},
            {**result, "rows": [result["rows"][0], result["rows"][0]]},
        ):
            sources.bridge.call.return_value["result"] = invalid
            with self.assertRaises(SourceFailure):
                await tools.execute("query_work_backlog", {"group_by": "facility"}, request)

    async def test_station_power_uses_kusto_and_attests_only_converted_mean_units(self):
        sources = reader()
        request = self.chat(sources)
        row = {"Station": "North", "average_power_MW": 2.5, "samples": 3, "bad_samples": 1}
        sources.bridge.call.return_value = {
            "source": request.source.model_dump(mode="json"), "completed_at": utc_now().isoformat(),
            "result": {
                "rows": [row], "row_count": 1, "truncated": False,
                "semantics": "Sample-weighted mean; not total station output or energy.",
                "read_completed_at_utc": "2026-10-10T01:00:00Z",
            },
        }
        tools = FabricBackendTools(sources)
        evidence = await tools.execute("query_station_power", {"lookback": "6h"}, request)
        self.assertEqual(evidence.column_units, {"average_power_MW": "MW"})
        self.assertEqual(evidence.result["rows"], [row])
        self.assertEqual(evidence.work_coverage_equipment_ids, ())
        self.assertEqual(evidence.resolved_equipment_ids, ())
        self.assertIn("not total station output or energy", evidence.limitations[0])
        self.assertIn("2026-10-10T01:00:00Z", evidence.limitations[1])
        self.assertEqual(sources.credential.get_token.await_count, 2)
        self.assertNotIn("graphql", sources.bridge.call.call_args.args[0]["tokens"])
        for patch in ({"average_power_MW": True}, {"average_power_MW": float("inf")},
                      {"Station": ""}, {"samples": True}, {"samples": 0}, {"bad_samples": 4}):
            sources.bridge.call.return_value["result"]["rows"] = [{**row, **patch}]
            with self.assertRaises(SourceFailure):
                await tools.execute("query_station_power", {}, request)
        sources.bridge.call.return_value["result"] = {
            "rows": [row, row], "row_count": 2, "truncated": False,
            "semantics": "Sample-weighted mean.", "read_completed_at_utc": "2026-10-10T01:00:00Z",
        }
        with self.assertRaises(SourceFailure):
            await tools.execute("query_station_power", {}, request)
        sources.bridge.call.return_value["result"] = {
            "rows": [], "row_count": 0, "truncated": False,
            "semantics": "Sample-weighted mean.", "read_completed_at_utc": "2026-10-10T01:00:00Z",
        }
        empty = await tools.execute("query_station_power", {}, request)
        self.assertEqual(empty.column_units, {})
        sources.bridge.call.return_value["result"]["row_count"] = False
        with self.assertRaises(SourceFailure):
            await tools.execute("query_station_power", {}, request)
        sources.bridge.call.side_effect = SourceFailure("Station source unavailable")
        with self.assertRaisesRegex(SourceFailure, "unavailable"):
            await tools.execute("query_station_power", {}, request)

    async def test_telemetry_series_attests_a_unit_only_when_every_row_shares_one(self):
        sources = reader()
        request = self.chat(sources)
        tools = FabricBackendTools(sources)
        base = {"opcua_node_id": "ns=2;s=T005.power_output", "event_time": "2026-10-10T01:00:00Z", "value": 1.5}

        async def read(rows):
            sources.bridge.call.return_value = {
                "source": request.source.model_dump(mode="json"), "completed_at": utc_now().isoformat(),
                "result": {"rows": rows, "row_count": len(rows), "truncated": False},
            }
            return await tools.execute("query_telemetry", {"equipment_id": "T005"}, request)

        evidence = await read([{**base, "unit": "MW"}, {**base, "value": 2.0, "unit": "MW"}])
        self.assertEqual(evidence.column_units, {"value": "MW"})
        self.assertIn("graphql", sources.bridge.call.call_args.args[0]["tokens"])
        for rows in ([{**base, "unit": "MW"}, {**base, "unit": "bar"}], [{**base, "unit": "MW"}, base],
                     [base], [], [{**base, "unit": " "}]):
            self.assertEqual((await read(rows)).column_units, {})

    async def test_backend_source_mismatch_or_unimplemented_tool_blocks_before_authentication(self):
        sources = reader()
        request = self.chat(sources)
        tools = FabricBackendTools(sources)
        with self.assertRaises(SourceFailure):
            await tools.execute("visualize_dataset", {}, request)
        with self.assertRaises(SourceFailure):
            await tools.execute("query_operations", {}, request.model_copy(update={"source": SOURCE}))
        sources.credential.get_token.assert_not_awaited()
        sources.bridge.call.assert_not_awaited()

    async def test_proposals_are_source_bound_stable_and_operator_priority_controlled(self):
        sources = reader()
        request = self.chat(sources)
        arguments = {"equipment_id": "T1", "title": "Inspect signal",
                     "description": "Operator-requested inspection.", "priority": "Critical"}
        body = {"proposal": {"equipmentId": "T1", "title": arguments["title"],
                             "description": arguments["description"], "priority": "Medium"},
                "existing_work": [], "work_read_at": utc_now().isoformat(), "staged": True,
                "confirmation_required": True, "production_write_executed": False}
        sources.bridge.call.return_value = {
            "source": request.source.model_dump(mode="json"), "completed_at": utc_now().isoformat(),
            "result": body,
        }
        tools = FabricBackendTools(sources)
        first = await tools.execute("propose_work_order", arguments, request)
        second = await tools.execute("propose_work_order", arguments, request)
        self.assertEqual(first.result["proposal"], second.result["proposal"])
        self.assertEqual(first.result["proposal"]["priority"], "Medium")
        self.assertEqual(first.result["proposal"]["run_id"], str(request.run_id))
        self.assertEqual(first.resolved_equipment_ids, ("T1",))
        self.assertEqual(first.work_coverage_equipment_ids, ("T1",))
        self.assertNotIn("kusto", sources.bridge.call.call_args.args[0]["tokens"])
        self.assertNotIn("synthetic-token", first.model_dump_json())
        for change in (
            {"proposal": {**body["proposal"], "equipmentId": "T2"}},
            {"proposal": {**body["proposal"], "priority": "Critical"}},
            {"work_read_at": (request.requested_at - timedelta(seconds=1)).isoformat()},
            {"work_read_at": (datetime.fromisoformat(
                sources.bridge.call.return_value["completed_at"]) + timedelta(microseconds=1)).isoformat()},
            {"confirmation_required": False},
            {"production_write_executed": True},
            {"existing_work": [{"id": "W1", "equipmentId": "T2", "status": "Draft"}]},
        ):
            sources.bridge.call.return_value["result"] = {**body, **change}
            with self.assertRaises((SourceFailure, ValidationError)):
                await tools.execute("propose_work_order", arguments, request)

    async def test_approval_provider_is_separate_from_agent_tools_and_requires_exact_readback(self):
        sources = reader()
        request = self.chat(sources)
        principal, creation = uuid4(), uuid4()
        clock = utc_now()
        draft = WorkOrderDraft(id=uuid4(), run_id=request.run_id, source=request.source,
                               equipment_id="T1", title="Inspect", description="Operator inspection.",
                               priority="Medium", work_read_at=clock,
                               expires_at=clock + timedelta(minutes=15), existing_work=())
        edits = WorkOrderEdits(title="Reviewed inspection", description="Human-approved scope.", priority="High")
        record = {"id": str(creation), "workOrderNumber": f"WO-{creation}", "equipmentId": "T1",
                  "createdByOid": str(principal), "instrumentId": None, "opcuaNodeId": None,
                  "title": edits.title, "description": edits.description, "priority": "High", "status": "Draft"}
        response = {"source": request.source.model_dump(mode="json"), "completed_at": utc_now().isoformat(),
                    "result": {"record": record}}
        sources.bridge.call.return_value = response
        tools = FabricBackendTools(sources)
        self.assertEqual(await tools.approve(draft, edits, principal, creation, allow_create=True), record)
        self.assertNotIn("approve_work_order", tools.supported_tools)
        call = sources.bridge.call.call_args.args[0]
        self.assertTrue(call["human_approved"])
        self.assertTrue(call["allow_create"])
        self.assertEqual(call["principal_id"], str(principal))
        self.assertEqual(set(call["tokens"]), {"fabric", "graphql"})
        for key, invalid in (("id", str(uuid4())), ("equipmentId", "T2"), ("createdByOid", str(uuid4())),
                             ("title", "Model change"), ("priority", "Critical"), ("status", "Completed")):
            sources.bridge.call.return_value = {**response, "result": {"record": {**record, key: invalid}}}
            with self.assertRaises(SourceFailure):
                await tools.approve(draft, edits, principal, creation, allow_create=False)

    async def test_projected_private_identity_is_retained_for_validation_not_model_data(self):
        sources = reader()
        request = self.chat(sources)
        body = {"rows": [{"status": "Approved"}], "row_count": 1,
                "_row_identities": {"/rows/0": {"equipmentId": "T1"}}}
        sources.bridge.call.return_value = {
            "source": request.source.model_dump(mode="json"), "completed_at": utc_now().isoformat(),
            "result": body,
        }
        tools = FabricBackendTools(sources)
        evidence = await tools.execute("query_operations", {"entity": "work_orders", "columns": ["status"]}, request)
        self.assertEqual(evidence.row_identities, {"/rows/0": {"equipmentId": "T1"}})
        self.assertNotIn("_row_identities", evidence.result)
        self.assertNotIn("equipmentId", str(evidence.receipt()))
        self.assertIn("_row_identities", body)
        sources.bridge.call.return_value["result"] = {**body, "_row_identities": {"/rows/1": {"equipmentId": "T1"}}}
        with self.assertRaisesRegex(SourceFailure, "match the returned rows"):
            await tools.execute("query_operations", {"entity": "work_orders", "columns": ["status"]}, request)

    async def test_backend_invalid_source_readback_or_failure_never_becomes_empty_work(self):
        sources = reader()
        request = self.chat(sources)
        tools = FabricBackendTools(sources)
        for source, completed_at in (
            (SOURCE, utc_now()),
            (request.source, request.requested_at - timedelta(seconds=1)),
            (request.source, utc_now() + timedelta(minutes=1)),
        ):
            sources.bridge.call.return_value = {
                "source": source.model_dump(mode="json"), "completed_at": completed_at.isoformat(),
                "result": {"rows": [], "row_count": 0},
            }
            with self.assertRaises(SourceFailure):
                await tools.execute("query_operations", {"entity": "work_orders"}, request)
        sources.bridge.call.side_effect = SourceFailure("EXCHANGE_NOT_ENABLED")
        with self.assertRaisesRegex(SourceFailure, "EXCHANGE_NOT_ENABLED"):
            await tools.execute("query_operations", {"entity": "work_orders"}, request)

    async def test_changed_configuration_blocks_reads_before_auth_or_io(self):
        sources = reader()
        request = ReviewRequest(**BODY, source=SOURCE, run_id=uuid4())
        with self.assertRaisesRegex(SourceFailure, "does not match"):
            await sources.read(request)
        sources.credential.get_token.assert_not_awaited()
        sources.bridge.call.assert_not_awaited()

    async def test_exchange_failure_does_not_become_empty_work(self):
        sources = reader()
        sources.bridge.call.side_effect = SourceFailure("EXCHANGE_NOT_ENABLED")
        request = ReviewRequest(**BODY, source=sources.discovery.source, run_id=uuid4())
        with self.assertRaisesRegex(SourceFailure, "EXCHANGE_NOT_ENABLED"):
            await sources.read(request)
        self.assertEqual(sources.credential.get_token.await_count, 3)
        self.assertEqual(sources.bridge.call.call_args.args[0]["action"], "read")

    async def test_live_evidence_remains_bound_to_request(self):
        sources = reader()
        request = ReviewRequest(**BODY, source=sources.discovery.source, run_id=uuid4())
        evidence = await TestAdapters().read(request)
        payload = evidence.model_dump(mode="json", exclude={"request"})
        sources.bridge.call.return_value = payload
        self.assertEqual(await sources.read(request), evidence)
        payload["equipment_id"] = "another-asset"
        with self.assertRaises(ValidationError):
            await sources.read(request)

    async def test_probe_cannot_claim_readiness_over_failed_or_missing_sources(self):
        sources = reader()
        payload = {
            "source": sources.discovery.source.model_dump(mode="json"), "ready": False,
            "checks": [{"source": "stid_telemetry", "status": "passed"}, {"source": "work_orders", "status": "failed"}],
            "failures": [{"source": "work_orders", "message": "EXCHANGE_NOT_ENABLED"}],
        }
        sources.bridge.call.return_value = payload
        self.assertFalse((await sources.probe(BODY["equipment_id"])).ready)
        payload["ready"] = True
        with self.assertRaisesRegex(SourceFailure, "contradicts"):
            await sources.probe(BODY["equipment_id"])
        payload["ready"] = False
        payload["failures"] = []
        with self.assertRaisesRegex(SourceFailure, "do not match"):
            await sources.probe(BODY["equipment_id"])

    async def test_bridge_suppresses_invalid_child_output(self):
        worker = FakeBridgeWorker(lambda message: [b"__HYDRO_RESULT__=private child output\n"])
        with patch("asyncio.create_subprocess_exec", return_value=worker):
            bridge = NodeSourceBridge()
            with self.assertRaisesRegex(SourceFailure, "output suppressed") as caught:
                await bridge.call({"action": "configuration"})
            await bridge.aclose()
        self.assertNotIn("private", str(caught.exception))
        self.assertTrue(worker.killed or worker.returncode is not None)

    async def test_bridge_compiles_prompt_one_evidence_before_chief_correction(self):
        question = (
            "Which turbines have latest signal quality BAD today (UTC), and what work is already open on each? "
            "Select the turbine with the most BAD signals, breaking ties by equipment ID. Investigate it using "
            "available telemetry, inspections and existing work, separating facts from hypotheses. Prepare an "
            "editable inspection draft only for an evidence-backed gap not already covered by open work. Finally, "
            "independently check the selected equipment/signal identity and existing-work coverage, and summarize "
            "any changes needed to the draft. Show a findings table and distinguish stale readings from current "
            "conditions. Do not save a work order."
        )
        bridge = NodeSourceBridge()
        intent = await bridge.call({"action": "chat_intent", "question": question})
        compiled = OperatorAnswerContract.model_validate(await bridge.call({
            "action": "compile_answer_contract",
            "question": question,
            "charts_requested": intent["charts_requested"],
            "required_evidence_kinds": intent["required_evidence_kinds"],
            "answer_contract": {
                "version": 3,
                "requirements": [
                    {
                        "evidence": "signal_quality_population_direct",
                        "purpose": "facts",
                    },
                    {
                        "evidence": "telemetry_focus_direct",
                        "purpose": "facts",
                    },
                ],
                "views": [
                    {
                        "id": "quality", "title": "Signal quality", "kind": "signal_quality",
                        "scope": "population", "presentation": "direct_records_table",
                    },
                    {
                        "id": "selected-telemetry", "title": "Selected telemetry", "kind": "telemetry",
                        "scope": "focus", "presentation": "direct_records_line",
                    },
                ],
            },
        }))
        requirement_kinds = {item.kind for item in compiled.requirements}
        view_kinds = {item.kind for item in compiled.views}
        self.assertTrue(set(intent["required_evidence_kinds"]).issubset(requirement_kinds))
        self.assertTrue(set(intent["required_evidence_kinds"]).issubset(view_kinds))
        selected_telemetry = next(item for item in compiled.views if item.id == "selected-telemetry")
        self.assertEqual(selected_telemetry.presentation, "direct_records_table")

    async def test_bridge_reads_only_bounded_final_envelope_after_library_output(self):
        def replies(message):
            return [
                b"library output that must not be parsed or returned\n",
                b"__HYDRO_RESULT__=" + json.dumps(
                    {"id": message["id"], "ok": True, "result": {"status": "ready"}}).encode() + b"\n",
            ]

        worker = FakeBridgeWorker(replies)
        with patch("asyncio.create_subprocess_exec", return_value=worker):
            bridge = NodeSourceBridge()
            result = await bridge.call({"action": "configuration"})
            await bridge.aclose()
        self.assertEqual(result, {"status": "ready"})

    async def test_bridge_cancellation_leaves_the_shared_worker_and_close_terminates_only_it(self):
        worker = FakeBridgeWorker(lambda message: [])
        with patch("asyncio.create_subprocess_exec", return_value=worker):
            bridge = NodeSourceBridge()
            task = asyncio.create_task(bridge.call({"action": "configuration"}))
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(bridge._pending, {})
            self.assertIsNone(worker.returncode)
            self.assertFalse(worker.killed)
            await bridge.aclose()
        self.assertEqual(worker.returncode, 0)

    async def test_bridge_never_replays_a_call_after_worker_exit_and_restarts_cleanly(self):
        workers = []

        def create_worker(*_args, **_kwargs):
            if not workers:
                worker = FakeBridgeWorker(lambda message: [b""])
            else:
                worker = FakeBridgeWorker(lambda message: [
                    b"__HYDRO_RESULT__=" + json.dumps(
                        {"id": message["id"], "ok": True, "result": {"attempt": len(workers)}}).encode() + b"\n",
                ])
            workers.append(worker)
            return worker

        with patch("asyncio.create_subprocess_exec", side_effect=create_worker):
            bridge = NodeSourceBridge()
            with self.assertRaisesRegex(SourceFailure, "exited before returning"):
                await bridge.call({"action": "approve_work_order"})
            self.assertEqual(len(workers[0].requests), 1)
            self.assertEqual(await bridge.call({"action": "configuration"}), {"attempt": 2})
            self.assertEqual(len(workers[0].requests), 1)
            await bridge.aclose()

    async def test_bridge_multiplexes_concurrent_calls_over_one_reused_worker(self):
        bridge = NodeSourceBridge()
        try:
            questions = {
                "work_backlog_group": [
                    ("open work by facility", "facility"), ("open work by equipment", "equipment"),
                    ("show a table", None),
                ],
            }
            calls = [bridge.call({"action": "work_backlog_group", "question": question})
                     for question, _expected in questions["work_backlog_group"] for _ in range(4)]
            results = await asyncio.gather(*calls, bridge.call({"action": "agent_contracts"}))
            worker = bridge._process
            self.assertIsNotNone(worker)
            for index, (_question, expected) in enumerate(
                    [item for item in questions["work_backlog_group"] for _ in range(4)]):
                self.assertEqual(results[index], {"group_by": expected})
            self.assertTrue(results[-1]["names"])
            await bridge.call({"action": "agent_contracts"})
            self.assertIs(bridge._process, worker)
        finally:
            await bridge.aclose()

    async def test_bridge_replaces_a_crashed_worker_and_follows_source_configuration_changes(self):
        def configured(key):
            identities = {name: str(uuid4()) for name in (
                "tenant_id", "workspace_id", "ontology_id", "eventhouse_id",
                "database_id", "lakehouse_id", "appbackend_id",
                "operational_sql_database_id",
            )}
            return json.dumps({**identities, "api_url": f"https://{'b' * 32}.pbidedicated.windows.net/workload",
                               "publishable_key": key})

        bridge = NodeSourceBridge()
        try:
            with patch.dict(os.environ, {"HYDRO_FABRIC_SOURCE_CONFIG": configured("pk-first")}):
                first = await bridge.call({"action": "configuration"})
                first_worker = bridge._process
                assert first_worker is not None
                first_worker.kill()
                await first_worker.wait()
                again = await bridge.call({"action": "configuration"})
                self.assertIsNot(bridge._process, first_worker)
                self.assertEqual(again["configuration_digest"], first["configuration_digest"])
            with patch.dict(os.environ, {"HYDRO_FABRIC_SOURCE_CONFIG": configured("pk-second")}):
                changed = await bridge.call({"action": "configuration"})
            self.assertNotEqual(changed["configuration_digest"], first["configuration_digest"])
        finally:
            await bridge.aclose()


class LiveFailureWorkflowTests(unittest.TestCase):
    def test_source_failure_cannot_reach_investigation_or_approval(self):
        sources = reader()
        sources.bridge.call.side_effect = SourceFailure("EXCHANGE_NOT_ENABLED")

        class Adapters(TestAdapters):
            async def read(self, request):
                return await sources.read(request)

        adapters = Adapters()
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Path(directory), TOKEN, adapters=adapters, source=sources.discovery.source)
            with TestClient(app) as client:
                run_id = str(uuid4())
                client.post("/runs", json=BODY, headers={**HEADERS, "Idempotency-Key": run_id}).raise_for_status()
                run = wait_for(client, run_id, "failed")
                self.assertIn("EXCHANGE_NOT_ENABLED", run["error"])
                self.assertNotIn("synthetic-token", run["error"])
                self.assertIsNone(run["proposal"])
                self.assertEqual((adapters.investigations, adapters.proposals), (0, 0))
                result = client.post(f"/runs/{run_id}/approval", json={
                    "approved": True, "proposal_digest": "a" * 64,
                }, headers=HEADERS)
                self.assertEqual(result.status_code, 409)


class ContainerPackagingTests(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]
    runtime_files = (
        "HydroOperationsApp/scripts/local-fabric-sources.mjs",
        "HydroOperationsApp/src/services/kustoResult.ts",
        "HydroOperationsApp/src/services/lakehouseConnector.ts",
        "HydroOperationsApp/src/services/operationalConnector.ts",
        *(f"HydroOperationsApp/src/services/copilot/{name}.ts" for name in (
            "query", "catalog", "domainContract", "rcaEvidence", "agentDefinitions", "answerPresentation",
            "orchestration",
        )),
    )

    def test_container_context_is_deny_by_default_and_copies_only_runtime_inputs(self):
        service = self.root / "HydroOperationsService"
        patterns = (service / "Dockerfile.dockerignore").read_text().splitlines()
        self.assertEqual(patterns[0], "**")
        self.assertTrue(all(line.startswith("!") and ".." not in line for line in patterns[1:]))

        def allowed(path):
            return any(
                len(path.split("/")) == len(pattern[1:].rstrip("/").split("/"))
                and all(fnmatch.fnmatchcase(part, rule) for part, rule in
                        zip(path.split("/"), pattern[1:].rstrip("/").split("/")))
                for pattern in patterns[1:]
            )

        for path in (*self.runtime_files, "HydroOperationsService/pyproject.toml",
                     "HydroOperationsService/requirements.lock", "HydroOperationsApp/package.json",
                     "HydroOperationsApp/package-lock.json", "HydroOperationsApp/.npmrc",
                     "HydroOperationsService/src/hydro_orchestrator/service.py"):
            self.assertTrue(allowed(path), path)
        for path in (
            ".git/config", ".env", ".copilot/session-state/state.json",
            "HydroOperationsApp/rayfin/.env", "HydroOperationsApp/rayfin/.env.local",
            "HydroOperationsApp/rayfin/.deployments.json", "HydroOperationsApp/dist/index.html",
            "HydroOperationsApp/node_modules/papaparse/package.json",
            "HydroOperationsApp/src/services/fabric.ts", "HydroOperationsApp/src/services/assistantStream.ts",
            "HydroOperationsService/.venv/pyvenv.cfg", "HydroOperationsService/tests/test_live_sources.py",
            "HydroOperationsService/src/hydro_orchestrator/.env",
            "HydroOperationsService/src/hydro_orchestrator/__pycache__/service.pyc",
            "HydroOperationsService/runtime/state.json", "HydroOperationsService/debug.log",
        ):
            self.assertFalse(allowed(path), path)
        dockerfile = (service / "Dockerfile").read_text()
        copied_files = set()
        for line in dockerfile.splitlines():
            if line.startswith("COPY ") and "--from=" not in line:
                for source in line.split()[1:-1]:
                    matches = list(self.root.glob(source))
                    self.assertTrue(matches, source)
                    self.assertTrue(all(path.is_file() for path in matches), source)
                    for path in matches:
                        relative = path.relative_to(self.root).as_posix()
                        self.assertTrue(allowed(relative), relative)
                        copied_files.add(relative)
        expected = set(self.runtime_files) | {
            "HydroOperationsApp/package.json", "HydroOperationsApp/package-lock.json", "HydroOperationsApp/.npmrc",
            "HydroOperationsService/pyproject.toml", "HydroOperationsService/requirements.lock",
            *(path.relative_to(self.root).as_posix() for path in
              (service / "src" / "hydro_orchestrator").glob("*.py")),
            *(path.relative_to(self.root).as_posix() for path in
              (service / "skills").glob("*/SKILL.md")),
        }
        self.assertEqual(copied_files, expected)
        self.assertIn('CMD ["python", "-m", "hydro_orchestrator", "--serve-delegated"]', dockerfile)
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("HOME=/home/session", dockerfile)
        self.assertIn("mkdir -p /var/lib/hydro /home/session/hydro", dockerfile)
        self.assertIn("chown hydro:hydro /var/lib/hydro /home/session /home/session/hydro", dockerfile)
        self.assertIn("chmod 700 /var/lib/hydro /home/session /home/session/hydro", dockerfile)
        self.assertIn("lock.acquire(timeout=0)", dockerfile)
        self.assertIn("HYDRO_RUNTIME_USER=hydro", dockerfile)
        self.assertIn("prepare_runtime_user(state)", dockerfile)
        self.assertIn("assert not os.getgroups()", dockerfile)
        self.assertIn("HYDRO_RUNTIME_STATE_DIR=/var/lib/hydro", dockerfile)
        self.assertIn("HYDRO_RUNTIME_HOST=0.0.0.0", dockerfile)
        self.assertNotRegex(dockerfile, r"(?m)^(?:ARG|ENV) .*?(?:TOKEN|KEY|CONFIG)=")
        self.assertNotIn("pbidedicated.windows.net", dockerfile)
        self.assertIn("npm ci --ignore-scripts", dockerfile)
        self.assertNotIn("--omit=dev", dockerfile)
        self.assertIn("/opt/bridge-dependencies/node_modules", dockerfile)
        self.assertIn("--only-binary=:all:", dockerfile)
        skill_names = {
            "condition-triage", "source-reconciliation", "root-cause-evidence",
            "maintenance-planning", "work-order-review", "grounded-presentation",
        }
        self.assertEqual({path.parent.name for path in (service / "skills").glob("*/SKILL.md")}, skill_names)
        for name in skill_names:
            manifest = (service / "skills" / name / "SKILL.md").read_text()
            self.assertTrue(manifest.startswith(f"---\nname: {name}\n"))
            self.assertNotIn("scripts/", manifest)

    def test_isolated_node24_runtime_closure_serves_contracts_and_explicit_configuration(self):
        node = os.environ.get("HYDRO_LOCAL_NODE", "node")
        version = subprocess.run([node, "--version"], check=True, capture_output=True, text=True, timeout=10)
        self.assertRegex(version.stdout.strip(), r"^v24\.")
        app = self.root / "HydroOperationsApp"
        lock = json.loads((app / "package-lock.json").read_text())["packages"]
        # Copy only the actual runtime npm import closure; no link back to the app tree.
        with tempfile.TemporaryDirectory(prefix=".container-imports-", dir=self.root) as directory:
            packaged = Path(directory)
            for relative in (*self.runtime_files, "HydroOperationsApp/package.json"):
                target = packaged / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.root / relative, target)
            dockerfile = (self.root / "HydroOperationsService" / "Dockerfile").read_text()
            dependency_copy = dockerfile.split("RUN node --input-type=commonjs -e '", 1)[1].split(
                "' - /opt/bridge-dependencies", 1,
            )[0].replace("\\\n", "\n")
            subprocess.run(
                [node, "--input-type=commonjs", "-", str(packaged / "HydroOperationsApp")],
                input=dependency_copy, cwd=app, check=True, capture_output=True, text=True, timeout=30,
            )
            for metadata_path in (packaged / "HydroOperationsApp" / "node_modules").rglob("package.json"):
                relative = metadata_path.parent.relative_to(packaged / "HydroOperationsApp").as_posix()
                if relative in lock:
                    self.assertEqual(json.loads(metadata_path.read_text())["version"], lock[relative]["version"])
            for browser_package in ("react", "react-dom", "@google/model-viewer", "@microsoft/fabric-embed"):
                self.assertFalse((packaged / "HydroOperationsApp" / "node_modules" / browser_package).exists())
            self.assertTrue(lock["node_modules/typescript"]["dev"])
            self.assertTrue((packaged / "HydroOperationsApp" / "node_modules" / "typescript").is_dir())
            config = {key: str(uuid4()) for key in (
                "tenant_id", "workspace_id", "ontology_id", "eventhouse_id",
                "database_id", "lakehouse_id", "appbackend_id", "operational_sql_database_id",
            )}
            config.update(api_url=f"https://{'a' * 32}.pbidedicated.windows.net/workload",
                          publishable_key="pk-synthetic-packaging-contract")
            env = {**os.environ, "HYDRO_FABRIC_SOURCE_CONFIG": json.dumps(config)}
            script = packaged / "HydroOperationsApp" / "scripts" / "local-fabric-sources.mjs"
            results = {}
            for action in ("source_contracts", "agent_contracts", "configuration"):
                completed = subprocess.run(
                    [node, str(script)], input=json.dumps({"action": action}), env=env, cwd=packaged,
                    check=True, capture_output=True, text=True, timeout=30,
                )
                _prefix, marker, payload = completed.stdout.rpartition("\n__HYDRO_RESULT__=")
                self.assertTrue(marker)
                envelope = json.loads(payload)
                self.assertTrue(envelope["ok"])
                results[action] = envelope["result"]
            self.assertTrue(results["source_contracts"]["tools"])
            self.assertTrue(results["agent_contracts"])
            for key, value in config.items():
                if key in ("api_url", "publishable_key"):
                    self.assertNotIn(key, results["configuration"])
                    continue
                self.assertEqual(results["configuration"][key], value)
            digest = hashlib.sha256(json.dumps(config, separators=(",", ":")).encode()).hexdigest()
            self.assertEqual(results["configuration"]["configuration_digest"], digest)
            self.assertFalse((packaged / "HydroOperationsApp" / "rayfin").exists())
            self.assertFalse((packaged / "HydroOperationsApp" / "src" / "services" / "fabric.ts").exists())
            # Node's own type stripping excludes type-only browser imports from the closure.
            closure_check = r"""
import { readFileSync, existsSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import { dirname, resolve } from 'node:path';
const files = JSON.parse(process.argv[1]);
for (const file of files) {
  const raw = readFileSync(file, 'utf8');
  const code = file.endsWith('.ts') ? stripTypeScriptTypes(raw) : raw;
  for (const match of code.matchAll(/(?:import|export)\s+[^;]*?\sfrom\s+['"]([^'"]+)['"]/g)) {
    if (match[1].startsWith('.') && !existsSync(resolve(dirname(file), match[1]))) {
      throw new Error(`Missing runtime import ${match[1]} in ${file}`);
    }
  }
}
"""
            subprocess.run([node, "--input-type=module", "-e", closure_check,
                            json.dumps([str(packaged / path) for path in self.runtime_files])],
                           cwd=packaged, check=True, capture_output=True, text=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
