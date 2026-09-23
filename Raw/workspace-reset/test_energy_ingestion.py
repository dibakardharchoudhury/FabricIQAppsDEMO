"""Offline GeoContext tests. Never imports/executes the Fabric execution cell.

Run: python3 -m unittest discover -s Raw/workspace-reset -p test_energy_ingestion.py -v
Regenerate the readable mirror from canonical source:
    python3 Raw/workspace-reset/test_energy_ingestion.py --generate-mirror
"""

import ast
import copy
import gzip
import hashlib
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import unquote, urlsplit
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK = ROOT / "Notebooks/Geo_001_ingest_energy_context.Notebook/notebook-content.py"
MIRROR = ROOT / "Raw/RTI_Notebooks/Geo_001_ingest_energy_context.ipynb"
PLATFORM = NOTEBOOK.with_name(".platform")
NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
RUN_ID = "offline-test-run"


class RequestFailure(OSError):
    pass


class RequestTimeout(RequestFailure):
    pass


class RequestConnectionError(RequestFailure):
    pass


def load_helpers():
    """AST-load only stdlib imports, constants, classes and function definitions."""
    tree = ast.parse(NOTEBOOK.read_text(encoding="utf-8"))
    allowed = {"gzip", "hashlib", "json", "math", "re", "tempfile", "time",
               "datetime", "email.utils", "pathlib", "urllib.parse", "uuid"}
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Import) and all(alias.name in allowed for alias in node.names):
            selected.append(node)
        elif isinstance(node, ast.ImportFrom) and node.module in allowed:
            selected.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            selected.append(node)
        elif isinstance(node, ast.Assign) and all(isinstance(target, ast.Name) and target.id.isupper() for target in node.targets):
            selected.append(node)
    namespace = {"requests": types.SimpleNamespace(
        Timeout=RequestTimeout, ConnectionError=RequestConnectionError, RequestException=RequestFailure,
    )}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(NOTEBOOK), "exec"), namespace)
    namespace["time"] = types.SimpleNamespace(sleep=Mock())
    return namespace


def canonical_to_ipynb(source):
    """Deterministic, source-only mirror: no outputs, credentials or runtime bindings."""
    cells, lines, kind, parameter = [], [], None, False

    def finish():
        if kind is None:
            return
        text = "".join(lines).strip("\n") + "\n"
        if not text.strip():
            return
        identifier = hashlib.sha256(f"{len(cells)}:{kind}:{text}".encode()).hexdigest()[:12]
        cell = {"cell_type": kind, "id": identifier,
                "metadata": {"tags": ["parameters"]} if parameter else {},
                "source": text.splitlines(keepends=True)}
        if kind == "code":
            cell.update({"execution_count": None, "outputs": []})
        cells.append(cell)

    for line in source.splitlines(keepends=True):
        if line.startswith(("# CELL *", "# PARAMETERS CELL *", "# MARKDOWN *", "# METADATA *")):
            finish()
            lines = []
            parameter = line.startswith("# PARAMETERS CELL")
            kind = None if line.startswith("# METADATA") else ("markdown" if line.startswith("# MARKDOWN") else "code")
        elif kind == "markdown":
            if line.startswith("# "):
                lines.append(line[2:])
            elif line.strip() == "#":
                lines.append("\n")
            elif line.strip():
                raise ValueError("Unexpected non-comment in a canonical markdown cell")
            else:
                lines.append(line)
        elif kind == "code":
            lines.append(line)
    finish()
    header = source.split("# MARKDOWN", 1)[0]
    metadata = json.loads("".join(line[len("# META "):] for line in header.splitlines(keepends=True) if line.startswith("# META ")))
    return {
        "cells": cells, "metadata": {
            "kernelspec": {"display_name": "synapse_pyspark", "name": "synapse_pyspark"},
            "language_info": {"name": "python"}, "dependencies": metadata["dependencies"],
        }, "nbformat": 4, "nbformat_minor": 5,
    }


def feature(oid, coordinates=(5.8, 58.5), **properties):
    return {"type": "Feature", "properties": {"objectid": oid, **properties},
            "geometry": {"type": "Point", "coordinates": list(coordinates)}}


def umm_message(identifier=1, version=1, **overrides):
    result = {
        "messageId": str(UUID(int=identifier)), "version": version, "isOutdated": False,
        "publicationDate": "2026-09-23T10:00:00Z", "eventStatus": 1, "messageType": 5,
        "eventStart": "2026-09-23T11:00:00Z", "eventStop": "2026-09-24T12:00:00Z",
        "areas": [{"name": "NO2", "code": "test-area-no2"}], "publisherName": "Offline fixture",
    }
    result.update(overrides)
    return result


class SequenceHTTP:
    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.calls = []

    def get_json(self, url, params=None, method="GET"):
        self.calls.append((url, params, method))
        if not self.bodies:
            raise AssertionError("Unexpected HTTP call")
        result = self.bodies.pop(0)
        if isinstance(result, Exception):
            raise result
        return copy.deepcopy(result)


class FakeResponse:
    def __init__(self, body=None, status=200, headers=None, text=None):
        self.text = json.dumps(body) if text is None else text
        self.status_code = status
        self.headers = headers or {}
        self.closed = False

    def close(self):
        self.closed = True


class MemoryFS:
    """Small offline bronze store; no network calls and no writes outside temp dirs."""
    def __init__(self):
        self.files = {}

    def mkdirs(self, path):
        return True

    def cp(self, source, target):
        self.files[target] = Path(unquote(urlsplit(source).path)).read_bytes()
        return True

    def put(self, path, text, overwrite):
        self.files[path] = text.encode()
        return True


class MemoryStore:
    def __init__(self, namespace, previous=None):
        self.namespace = namespace
        self.statuses = copy.deepcopy(previous or {})
        self.history = []
        self.partitions = {}
        self.published = []

    def get_status(self, layer):
        return self.statuses.get(layer)

    def set_status(self, row):
        self.history.append(copy.deepcopy(row))
        self.statuses[row["layer_id"]] = copy.deepcopy(row)

    def publish(self, spool):
        rows = []
        for file in sorted((spool.local / "normalized").glob("*.gz")):
            with gzip.open(file, "rt", encoding="utf-8") as handle:
                rows.extend(json.loads(line) for line in handle)
        self.namespace["validate_snapshot_metrics"]({
            "rows": len(rows), "identities": len({row["feature_id"] for row in rows}),
            "invalid": 0, "unmapped": sum(not row["geometry_json"] for row in rows),
        }, spool)
        self.partitions[spool.config["layer_id"]] = rows
        self.published.append(spool.config["layer_id"])


class HelpersTest(unittest.TestCase):
    def setUp(self):
        self.ns = load_helpers()
        self.error = self.ns["SourceError"]
        self.stamp = self.ns["utc_text"](NOW)

    def call(self, name, *args, **kwargs):
        return self.ns[name](*args, **kwargs)

    def config(self, layer):
        return next(config for config in self.ns["LAYER_CONFIGS"] if config["layer_id"] == layer)

    def properties(self, row):
        return json.loads(row["properties_json"])

    def make_row(self, config, identity="fixture:one", geometry=None):
        return self.call("make_feature", identity, config["source_id"], config["layer_id"],
                         "Test", geometry, {}, None, config["source_url"], RUN_ID, self.stamp)


class PrimitiveTests(HelpersTest):
    def test_formatted_numbers_and_missing_are_not_zero(self):
        for source, expected in [("12\u00a0345", 12345), ("−1\u202f234,5", -1234.5),
                                 ("1.234,50", 1234.5), ("1,234.50", 1234.5), ("0", 0.0),
                                 (42.5, 42.5), ("-0,05", -0.05)]:
            with self.subTest(source=source):
                self.assertEqual(self.call("number", source), expected)
        for source in (None, "", "-", "—", "n/a"):
            self.assertIsNone(self.call("number", source))

    def test_invalid_numeric_schema_fails(self):
        for value in (True, {}, [], "12 MW", "NaN", float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(self.error):
                self.call("number", value)

    def test_timestamps_preserve_offset_ms_and_reject_naive_times(self):
        self.assertEqual(self.call("observed", "2026-09-23T14:00:00.0000000+02:00", "date"), self.stamp)
        self.assertEqual(self.call("observed", int(NOW.timestamp() * 1000), "date", epoch_ms=True), self.stamp)
        self.assertEqual(self.call("observed", "2026-09-20", "date", date_only=True), "2026-09-20T00:00:00.000Z")
        for value in ("2026-09-23T12:00:00", "not-a-date", "2026-02-30", True):
            with self.subTest(value=value), self.assertRaises(self.error):
                self.call("observed", value, "date", required=True)

    def test_geometry_order_bounds_and_finite_numbers(self):
        geometry = {"type": "MultiLineString", "coordinates": [[[5.0, 58.0], [10.0, 60.0]], [[6.0, 57.0], [8.0, 65.0]]]}
        self.assertEqual(self.call("geometry_bounds", geometry, self.ns["NVE_EXTENT"]), (5.0, 57.0, 10.0, 65.0))
        for geometry in (
            {"type": "Point", "coordinates": [58.5, 5.8]},  # globally valid, wrong NVE order
            {"type": "Point", "coordinates": [200, 60]},
            {"type": "Point", "coordinates": [10, float("nan")]},
            {"type": "LineString", "coordinates": [[10, 60]]},
            {"type": "MultiLineString", "coordinates": []},
            {"type": "Point", "coordinates": ["10", 60]},
        ):
            with self.subTest(geometry=geometry), self.assertRaises(self.ns["GeometryError"]):
                self.call("geometry_bounds", geometry, self.ns["NVE_EXTENT"])

    def test_invalid_geometry_is_retained_unmapped_not_inferred(self):
        row = self.call("normalize_grid", feature(7, (58.5, 5.8)), self.config("masts"), RUN_ID, self.stamp)
        self.assertEqual(row["geometry_json"], "")
        self.assertEqual([row[name] for name in ("min_lon", "min_lat", "max_lon", "max_lat")], [None] * 4)
        self.assertEqual(self.properties(row)["geometry_status"], "rejected")
        self.assertIn("objectid", self.properties(row)["source_properties"])

    def test_grid_stable_identity_is_source_service_and_layer_qualified(self):
        guid = "{00000000-0000-4000-8000-000000000001}"
        identity, kind = self.call("grid_identity", {"objectid": 9, "globalid": guid, "nvenetbasid": 22}, 0)
        self.assertEqual(kind, "globalid")
        self.assertTrue(identity.startswith("nve-grid:Nettanlegg4:0:globalid:"))
        self.assertNotEqual(identity, self.call("grid_identity", {"objectid": 9, "globalid": guid}, 1)[0])
        self.assertIn(":nvenetbasid:22", self.call("grid_identity", {"objectid": 9, "nvenetbasid": 22}, 0)[0])
        self.assertEqual(self.call("grid_identity", {"objectid": 9, "nvenetbasid": None}, 4)[0], "nve-grid:Nettanlegg4:4:objectid:9")
        with self.assertRaises(self.error):
            self.call("grid_identity", {}, 0)

    def test_invalid_source_shape_does_not_produce_empty_success(self):
        for name, arguments in (
            ("normalize_grid", ({}, self.config("masts"), RUN_ID, self.stamp)),
            ("normalize_hydro", ({}, [], RUN_ID, self.stamp)),
            ("normalize_reservoir", ({}, RUN_ID, self.stamp)),
            ("normalize_balance", ({}, RUN_ID, self.stamp)),
            ("normalize_flow", ({}, RUN_ID, self.stamp)),
            ("normalize_umm", ({}, {}, NOW, {}, RUN_ID, self.stamp)),
        ):
            with self.subTest(normalizer=name), self.assertRaises(self.error):
                self.call(name, *arguments)


class SourceNormalizerTests(HelpersTest):
    def plant(self):
        return {"VannKraftverkID": 146, "Navn": "Fixture plant", "HovedEier": "Fixture owner",
                "MaksYtelse": 1.48, "MidProd_91_20": 7.309, "BruttoFallhoyde_M": 55.3,
                "Slukeevne": 3, "ElspotomraadeNummer": "2", "ErIDrift": True, "Kraftverkstatus": "Idrift"}

    def reservoir(self, **overrides):
        row = {"omrType": "EL", "omrnr": 2, "iso_aar": 2026, "iso_uke": 38,
               "dato_Id": "2026-09-20", "fyllingsgrad": 0.75, "kapasitet_TWh": 10,
               "fylling_TWh": 7.5, "endring_fyllingsgrad": -0.02}
        row.update(overrides)
        return row

    def test_hydro_join_and_annual_not_current_production(self):
        row = self.call("normalize_hydro", self.plant(), [feature(7, (5.836068882, 58.524009151), vannkraftverknr=146)], RUN_ID, self.stamp)
        self.assertEqual(row["feature_id"], "nve-hydro:Powerplant:146")
        self.assertAlmostEqual(row["min_lon"], 5.836068882)
        props = self.properties(row)
        self.assertEqual(props["mean_annual_production_gwh_1991_2020"], 7.309)
        self.assertEqual(props["installed_capacity_mw"], 1.48)
        self.assertIn("NOT current", props["production_basis"])
        self.assertIsNone(row["observed_at"])

    def test_missing_and_duplicate_hydro_coordinates_are_not_dropped_or_chosen(self):
        for candidates, status in [([], "no GIS match"), ([feature(1), feature(2)], "ambiguous GIS join")]:
            row = self.call("normalize_hydro", self.plant(), candidates, RUN_ID, self.stamp)
            self.assertEqual(row["geometry_json"], "")
            self.assertEqual(self.properties(row)["gis_join_state"], status)
            self.assertEqual(len(self.properties(row)["gis_properties"]), len(candidates))

    def test_reservoir_fractions_and_percentage_points(self):
        row = self.call("normalize_reservoir", self.reservoir(), RUN_ID, self.stamp)
        props = self.properties(row)
        self.assertEqual(props["fill_pct"], 75)
        self.assertEqual(props["change_percentage_points"], -2)
        self.assertEqual(props["capacity_twh"], 10)
        self.assertEqual(props["price_area"], "NO2")
        self.assertEqual(props["geographic_precision"], "area representative point")
        self.assertEqual(row["observed_at"], "2026-09-20T00:00:00.000Z")
        self.assertIn("not an exact", props["observed_at_precision"])

    def test_reservoir_national_and_vass_areas_are_all_preserved(self):
        for kind, mapped in [("EL", True), ("NO", True), ("VASS", False)]:
            row = self.call("normalize_reservoir", self.reservoir(omrType=kind), RUN_ID, self.stamp)
            self.assertEqual(bool(row["geometry_json"]), mapped)
            self.assertIn(f":{kind}:2", row["feature_id"])
        with self.assertRaisesRegex(self.error, "fractions"):
            self.call("normalize_reservoir", self.reservoir(fyllingsgrad=75), RUN_ID, self.stamp)
        with self.assertRaisesRegex(self.error, "ISO week"):
            self.call("normalize_reservoir", self.reservoir(iso_uke=99), RUN_ID, self.stamp)

    def balance(self):
        body = {"MeasuredAt": int(NOW.timestamp() * 1000)}
        for field in self.ns["BALANCE_METRICS"]:
            body[field] = [{"countryCode": None, "value": "Header, NOT a number"},
                           {"countryCode": "SE", "value": "900"},
                           {"countryCode": "NO", "value": "12\u00a0345", "measuredAt": "2026-09-23T11:45:00+00:00"}]
        return body

    def test_balance_skips_headers_preserves_each_time_and_missing(self):
        body = self.balance()
        body["ProductionData"][-1]["measuredAt"] = None
        body["NuclearData"][-1]["value"] = "-"
        row = self.call("normalize_balance", body, RUN_ID, self.stamp)
        props = self.properties(row)
        self.assertEqual(props["production_mw"], 12345)
        self.assertIsNone(props["nuclear_mw"])
        self.assertIsNone(props["production_observed_at"])
        self.assertEqual(props["consumption_observed_at"], "2026-09-23T11:45:00.000Z")
        self.assertEqual(row["observed_at"], self.stamp)
        self.assertEqual(props["country_code"], "NO")
        self.assertNotIn("price_area", props)

    def test_balance_rejects_duplicates_and_completely_missing_norwegian_values(self):
        body = self.balance()
        body["ProductionData"].append(body["ProductionData"][-1])
        with self.assertRaisesRegex(self.error, "duplicate"):
            self.call("normalize_balance", body, RUN_ID, self.stamp)
        body = self.balance()
        for field in self.ns["BALANCE_METRICS"]:
            body[field] = body[field][:2]
        with self.assertRaisesRegex(self.error, "no Norwegian"):
            self.call("normalize_balance", body, RUN_ID, self.stamp)

    def flow(self, **overrides):
        row = {"OutAreaElspotId": "NO1", "InAreaElspotId": "NO2", "Value": -123.5, "MeasureDate": NOW.timestamp() * 1000}
        row.update(overrides)
        return row

    def test_negative_flows_reverse_arrow_and_schematic_coordinates(self):
        row = self.call("normalize_flow", self.flow(), RUN_ID, self.stamp)
        props = self.properties(row)
        self.assertEqual((props["from_area"], props["to_area"]), ("NO2", "NO1"))
        self.assertEqual(props["flow_mw"], 123.5)
        self.assertEqual(props["signed_flow_mw"], -123.5)
        self.assertEqual(json.loads(row["geometry_json"])["coordinates"][0], [8.0, 58.15])
        self.assertIn("NOT cable", props["geographic_precision"])
        reversed_source = self.call("normalize_flow", self.flow(OutAreaElspotId="NO2", InAreaElspotId="NO1", Value=123.5), RUN_ID, self.stamp)
        self.assertEqual(row["feature_id"], reversed_source["feature_id"])

    def test_flows_keep_unlocated_endpoints_missing_value_and_cross_borders(self):
        row = self.call("normalize_flow", self.flow(InAreaElspotId="EN", Value="-"), RUN_ID, self.stamp)
        self.assertEqual(row["geometry_json"], "")
        self.assertIsNone(self.properties(row)["flow_mw"])
        self.assertFalse(self.properties(row)["direction_known"])
        self.assertEqual(self.properties(row)["unlocated_endpoints"], ["EN"])
        cross_border = self.call("normalize_flow", self.flow(InAreaElspotId="GB"), RUN_ID, self.stamp)
        self.assertTrue(cross_border["geometry_json"])
        self.assertIsNone(self.call("normalize_flow", self.flow(OutAreaElspotId="SE1", InAreaElspotId="FI"), RUN_ID, self.stamp))

    def test_frequency_uses_latest_source_timestamp_not_poll_time(self):
        latest = NOW - timedelta(seconds=4)
        body = {"Measurements": [[(NOW - timedelta(seconds=10)).timestamp() * 1000, 50.01],
                                 [latest.timestamp() * 1000, 49.99]]}
        row = self.call("normalize_frequency", body, NOW - timedelta(minutes=3), NOW, RUN_ID, self.stamp)
        self.assertEqual(row["observed_at"], self.call("utc_text", latest))
        self.assertEqual(self.properties(row)["frequency_hz"], 49.99)
        self.assertEqual(self.properties(row)["sample_count"], 2)

    def test_frequency_empty_and_out_of_window_are_errors(self):
        for body in ({"Measurements": []}, {"Measurements": [[(NOW - timedelta(minutes=4)).timestamp() * 1000, 50]]},
                     {"Measurements": [[NOW.timestamp() * 1000, "-"]]}):
            with self.subTest(body=body), self.assertRaises(self.error):
                self.call("normalize_frequency", body, NOW - timedelta(minutes=3), NOW, RUN_ID, self.stamp)


class UMMTests(HelpersTest):
    def normalize(self, message, crosswalk=None):
        return self.call("normalize_umm", message, crosswalk or {}, NOW, {"publication_horizon_days": 30}, RUN_ID, self.stamp)

    def test_all_affected_areas_and_both_border_endpoints_are_preserved(self):
        message = umm_message(areas=[{"name": "NO1", "code": "code-1"}],
                              productionUnits=[{"areaName": "NO2", "areaEic": "code-2"}],
                              transmissionUnits=[{"outAreaName": "NO5", "inAreaName": "GB"}])
        row = self.normalize(message)
        props = self.properties(row)
        self.assertEqual({area["area"] for area in props["affected_areas"]}, {"NO1", "NO2", "NO5", "GB"})
        geometry = json.loads(row["geometry_json"])
        self.assertEqual(geometry["type"], "MultiPoint")
        self.assertEqual(len(geometry["coordinates"]), 4)
        self.assertTrue(props["map_eligible"])

    def test_provider_eic_pairs_are_used_without_guessing_unknown_codes(self):
        crosswalk = {}
        self.call("update_area_crosswalk", crosswalk, umm_message())
        row = self.normalize(umm_message(areas=[{"code": "test-area-no2"}]), crosswalk)
        self.assertEqual(self.properties(row)["affected_areas"][0]["area"], "NO2")
        self.assertTrue(row["geometry_json"])
        unmapped = self.normalize(umm_message(areas=[{"code": "unverified-code"}]))
        self.assertEqual(unmapped["geometry_json"], "")
        self.assertEqual(self.properties(unmapped)["norwegian_relevance"], "unknown")
        self.assertFalse(self.properties(unmapped)["map_eligible"])
        with self.assertRaisesRegex(self.error, "conflicts"):
            self.call("update_area_crosswalk", crosswalk, umm_message(areas=[{"name": "NO3", "code": "test-area-no2"}]))

    def test_zero_area_notice_remains_in_list_unmapped_and_unranked(self):
        row = self.normalize(umm_message(areas=[]))
        props = self.properties(row)
        self.assertEqual(row["geometry_json"], "")
        self.assertEqual(props["affected_areas"], [])
        self.assertEqual(props["importance_bucket"], "unranked")
        self.assertIsNone(props["importance_score"])

    def test_outdated_ended_and_foreign_notices_do_not_enter_current_map(self):
        self.assertIsNone(self.normalize(umm_message(isOutdated=True)))
        self.assertIsNone(self.normalize(umm_message(eventStart="2026-09-20T12:00:00Z", eventStop="2026-09-21T12:00:00Z")))
        self.assertIsNone(self.normalize(umm_message(areas=[{"name": "SE1"}])))

    def test_cancellations_and_unknown_dates_are_retained_but_not_map_eligible(self):
        for message in (umm_message(eventStatus=3, cancellationReason="Withdrawn"),
                        umm_message(eventStart=None, eventStop=None)):
            row = self.normalize(message)
            props = self.properties(row)
            self.assertFalse(props["map_eligible"])
            self.assertEqual(props["importance_bucket"], "unranked")
        row = self.normalize(umm_message(version=7, eventStatus="Dismissed", cancellationReason="Wrong interval"))
        props = self.properties(row)
        self.assertTrue(props["is_cancelled"])
        self.assertEqual(props["version"], 7)
        self.assertEqual(props["cancellation_reason"], "Wrong interval")

    def test_ranking_distinguishes_low_unranked_and_explains_all_factors(self):
        high = self.call("importance_rules", 1, 1500, 30, "direct", True)
        self.assertEqual(high["importance_score"], 85)
        self.assertEqual(high["importance_bucket"], "high")
        self.assertEqual(high["importance_method"], "rules-v1")
        for text in ("Norwegian", "unplanned", "single-unit", "continuous", "Not an official"):
            self.assertIn(text, high["importance_reason"])
        low = self.call("importance_rules", "Planned", 0, 1, "direct", True)
        self.assertEqual(low["importance_bucket"], "low")
        self.assertEqual(low["importance_score"], 10)
        unranked = self.call("importance_rules", None, None, 300, "direct", True)
        self.assertEqual(unranked["importance_bucket"], "unranked")
        self.assertIsNone(unranked["importance_score"])

    def test_capacity_is_not_summed_and_root_envelope_does_not_fill_gaps(self):
        message = umm_message(unavailabilityType=1, eventStop="2027-01-01T00:00:00Z",
                              generationUnits=[{"areaName": "NO2", "timePeriods": [
                                  {"eventStart": "2026-09-23T13:00:00Z", "eventStop": "2026-09-23T15:00:00Z", "unavailableCapacity": 100},
                                  {"eventStart": "2026-09-24T13:00:00Z", "eventStop": "2026-09-24T15:00:00Z", "unavailableCapacity": 120},
                                  {"eventStart": "2026-09-20T00:00:00Z", "eventStop": "2026-09-22T00:00:00Z", "unavailableCapacity": 5000},
                              ]}])
        row = self.normalize(message)
        props = self.properties(row)
        self.assertEqual(props["unavailable_mw"], 120)
        self.assertEqual(props["duration_hours"], 2)
        self.assertEqual(props["event_phase"], "future")

    def test_reversed_dates_are_a_source_error(self):
        with self.assertRaisesRegex(self.error, "precedes"):
            self.normalize(umm_message(eventStart="2026-09-24T00:00:00Z", eventStop="2026-09-23T00:00:00Z"))

    def test_pagination_covers_fixed_window_and_selects_latest_revision(self):
        self.ns["UMM_PAGE_SIZE"] = 2
        newest = umm_message(version=3, publicationDate="2026-09-23T11:00:00Z")
        other = umm_message(identifier=2)
        old = umm_message(version=2, publicationDate="2026-09-22T12:00:00Z", isOutdated=True)
        http = SequenceHTTP([{"items": [newest, other], "total": 3},
                             {"items": [old], "total": 3}, {"items": [newest], "total": 3}])
        details = {}
        latest, crosswalk, coverage = self.call("fetch_umm_threads", http, NOW, details)
        self.assertEqual(latest[newest["messageId"]]["version"], 3)
        self.assertEqual(len(latest), 2)
        self.assertEqual([call[1]["skip"] for call in http.calls], [0, 2, 0])
        for _, parameters, _ in http.calls:
            self.assertEqual(parameters["IncludeOutdated"], "true")
            self.assertIn("publicationStartDate", parameters)
            self.assertIn("publicationStopDate", parameters)
            self.assertNotIn("areas", parameters)
        self.assertEqual(coverage["revision_count"], 3)
        self.assertIn("Older still-active", coverage["limitation"])
        self.assertTrue(details["reconciled"])
        self.assertEqual(crosswalk["TEST-AREA-NO2"], "NO2")

    def test_page_duplicates_truncation_drift_ignored_filters_and_budget_fail(self):
        self.ns["UMM_PAGE_SIZE"] = 2
        one, two = umm_message(), umm_message(identifier=2)
        cases = [
            [{"items": [one, one], "total": 2}],
            [{"items": [one], "total": 2}],
            [{"items": [one, two], "total": 3}, {"items": [umm_message(identifier=3)], "total": 4}],
            [{"items": [umm_message(publicationDate="2020-01-01T00:00:00Z")], "total": 1}],
            [{"items": [], "total": self.ns["UMM_MAX_REVISIONS"] + 1}],
            [{"items": [one], "total": 1}, {"items": [one], "total": 2}],
        ]
        for pages in cases:
            with self.subTest(pages=pages), self.assertRaises(self.error):
                self.call("fetch_umm_threads", SequenceHTTP(pages), NOW, {})

    def test_valid_empty_window_is_reconciled_not_confused_with_http_failure(self):
        details = {}
        latest, _, coverage = self.call("fetch_umm_threads", SequenceHTTP([
            {"items": [], "total": 0}, {"items": [], "total": 0},
        ]), NOW, details)
        self.assertEqual(latest, {})
        self.assertEqual(coverage["revision_count"], 0)
        self.assertTrue(details["reconciled"])


class HTTPAndArcGISTests(HelpersTest):
    def test_bounded_backoff_archives_responses_and_uses_read_only_post(self):
        spool = types.SimpleNamespace(record=Mock())
        responses = [FakeResponse({}, 429, {"Retry-After": "999999"}), FakeResponse({}, 503), FakeResponse({"count": 2})]
        session = Mock(request=Mock(side_effect=responses))
        http = self.ns["PublicHTTP"](session, spool)
        result = http.get_json(self.ns["GRID_SERVICE"] + "/0/query", {"returnCountOnly": "true"}, "POST")
        self.assertEqual(result, {"count": 2})
        self.assertEqual(session.request.call_count, 3)
        self.assertEqual(spool.record.call_count, 3)
        self.assertEqual([call.args[0] for call in self.ns["time"].sleep.call_args_list], [60.0, 2.0])
        self.assertEqual(session.request.call_args.kwargs["timeout"], (15, 90))
        self.assertEqual(session.request.call_args.kwargs["data"], {"returnCountOnly": "true"})
        self.assertFalse(session.request.call_args.kwargs["allow_redirects"])
        self.assertTrue(all(response.closed for response in responses))

    def test_forbidden_invalid_json_and_service_error_do_not_retry_or_succeed(self):
        for response in (FakeResponse({}, 403), FakeResponse(text="<html>maintenance</html>"),
                         FakeResponse(text='{"unexpected": NaN}'),
                         FakeResponse({"error": {"code": 400}})):
            with self.subTest(response=response):
                session = Mock(request=Mock(return_value=response))
                spool = types.SimpleNamespace(record=Mock())
                with self.assertRaises(self.error):
                    self.ns["PublicHTTP"](session, spool).get_json(self.ns["UMM_URL"])
                session.request.assert_called_once()
                self.assertEqual(spool.record.call_count, 1)

    def test_transport_retries_are_bounded_and_unapproved_methods_blocked(self):
        session = Mock(request=Mock(side_effect=RequestTimeout("offline timeout")))
        spool = types.SimpleNamespace(record=Mock())
        with self.assertRaisesRegex(self.error, "transport"):
            self.ns["PublicHTTP"](session, spool, attempts=3).get_json(self.ns["UMM_URL"])
        self.assertEqual(session.request.call_count, 3)
        for url, method in ((self.ns["UMM_URL"], "POST"), (self.ns["GRID_SERVICE"] + "/0/deleteFeatures", "POST"),
                            ("https://unapproved.invalid/", "GET"), (self.ns["FLOW_URL"], "DELETE")):
            with self.subTest(url=url), self.assertRaises(self.error):
                self.ns["PublicHTTP"](session, spool).get_json(url, method=method)
        self.assertEqual(session.request.call_count, 3)

    def arcgis_bodies(self, final_ids=None):
        return [
            {"copyrightText": "Fixture notice"},
            {"fields": [{"name": "objectid"}], "maxRecordCount": 2000},
            {"count": 3}, {"objectIdFieldName": "objectid", "objectIds": [3, 1, 2]},
            {"type": "FeatureCollection", "features": [feature(2), feature(1)]},
            {"type": "FeatureCollection", "features": [feature(3)]},
            {"count": 3}, {"objectIdFieldName": "objectid", "objectIds": final_ids or [1, 2, 3]},
        ]

    def test_arcgis_uses_id_chunks_geojson_4326_and_final_reconciliation(self):
        self.ns["ARCGIS_CHUNK_SIZE"] = 2
        http, details = SequenceHTTP(self.arcgis_bodies()), {}
        rows = list(self.call("iter_arcgis", http, self.ns["GRID_SERVICE"] + "/4", details))
        self.assertEqual(len(rows), 3)
        self.assertTrue(details["reconciled"])
        self.assertEqual(details["initial_count"], details["final_count"])
        page_calls = [call for call in http.calls if "objectIds" in (call[1] or {})]
        self.assertEqual([call[1]["objectIds"] for call in page_calls], ["1,2", "3"])
        self.assertTrue(all(call[2] == "POST" and call[1]["outSR"] == "4326" and call[1]["f"] == "geojson" for call in page_calls))

    def test_arcgis_changed_inventory_cannot_publish_partial_snapshot(self):
        self.ns["ARCGIS_CHUNK_SIZE"] = 2
        details = {}
        with self.assertRaisesRegex(self.error, "changed"):
            list(self.call("iter_arcgis", SequenceHTTP(self.arcgis_bodies([1, 2, 4])), self.ns["GRID_SERVICE"] + "/4", details))
        self.assertFalse(details["reconciled"])

    def test_arcgis_missing_duplicate_unexpected_and_truncated_ids_fail(self):
        for features, extra in [
            ([feature(1)], {}), ([feature(1), feature(1)], {}),
            ([feature(1), feature(3)], {}), ([feature(1), feature(2)], {"exceededTransferLimit": True}),
        ]:
            with self.subTest(features=features), self.assertRaises(self.error):
                self.call("arcgis_page_features", {"type": "FeatureCollection", "features": features, **extra}, [1, 2], "objectid")
        for ids, count in [([1, 1], 2), ([1], 2), ([], 0)]:
            with self.subTest(ids=ids), self.assertRaises(self.error):
                self.call("arcgis_inventory", SequenceHTTP([{"count": count}, {"objectIds": ids, "objectIdFieldName": "objectid"}]), self.ns["GRID_SERVICE"] + "/0")


class PublicationTests(HelpersTest):
    def old_status(self, config):
        return self.call("source_status", config, None, "2026-09-20T00:00:00Z", "ready", "old good",
                         "2026-09-20T00:00:00Z", {"row_count": 7, "unmapped_count": 2, "rejected_count": 1})

    def test_status_error_preserves_last_success_and_last_good_counts(self):
        config = self.config("masts")
        previous = self.old_status(config)
        status = self.call("source_status", config, previous, self.stamp, "error", "HTTP 503")
        self.assertEqual(status["last_attempt_at"], self.stamp)
        for field in ("last_success_at", "row_count", "unmapped_count", "rejected_count"):
            self.assertEqual(status[field], previous[field])

    def test_static_daily_cache_operational_skip_force_and_failed_snapshot_retry(self):
        config = self.config("masts")
        recent = self.old_status(config)
        recent["last_success_at"] = self.call("utc_text", NOW - timedelta(hours=1))
        self.assertFalse(self.call("refresh_decision", config, recent, "all", False, NOW)[0])
        self.assertTrue(self.call("refresh_decision", config, recent, "all", True, NOW)[0])
        self.assertFalse(self.call("refresh_decision", config, recent, "operational", True, NOW)[0])
        self.assertTrue(self.call("refresh_decision", config, {**recent, "state": "error"}, "all", False, NOW)[0])
        self.assertTrue(self.call("refresh_decision", config, {**recent, "last_success_at": "2026-09-22T12:00:00Z"}, "all", False, NOW)[0])
        self.assertTrue(self.call("refresh_decision", self.config("reservoirs"), recent, "operational", False, NOW)[0])

    def test_spooling_shards_and_raw_rejected_geometry_archive(self):
        self.ns["NORMALIZED_SHARD_ROWS"] = 2
        fs, config = MemoryFS(), self.config("masts")
        with tempfile.TemporaryDirectory() as root:
            spool = self.ns["LayerSpool"](root, config, RUN_ID, self.stamp)
            spool.record({"source": "fixture", "unaltered_geometry": [58.5, 5.8]})
            for oid in (1, 2, 3):
                spool.emit(self.call("normalize_grid", feature(oid, (58.5, 5.8)), config, RUN_ID, self.stamp))
            spool.archive(fs, "prepared", "offline fixture")
            self.assertEqual(len(list((spool.local / "normalized").glob("*.gz"))), 2)
            self.assertEqual(spool.row_count, 3)
            self.assertEqual(spool.unmapped_count, 3)
            self.assertEqual(spool.rejected_count, 3)
        raw_path = next(path for path in fs.files if path.endswith("responses.jsonl.gz"))
        raw = json.loads(gzip.decompress(fs.files[raw_path]).decode())
        self.assertEqual(raw["unaltered_geometry"], [58.5, 5.8])
        self.assertTrue(all(path.startswith(f"Files/bronze/geo_context/nve-grid/{RUN_ID}/masts/") for path in fs.files))

    def test_failed_layer_preserves_partition_archives_raw_and_other_sources_run(self):
        bad, good = self.config("masts"), self.config("reservoirs")
        old = self.old_status(bad)
        store, fs = MemoryStore(self.ns, {"masts": old}), MemoryFS()
        store.partitions["masts"] = ["last-good-feature"]

        def produce(spool, http, clock):
            current = store.get_status(spool.config["layer_id"])
            self.assertEqual(current["state"], "error")  # recorded before first request
            spool.record({"body": "complete raw evidence, even when normalization fails"})
            if spool.config["layer_id"] == "masts":
                spool.emit(self.make_row(bad))
                raise self.error("upstream IDs missing")
            spool.emit(self.make_row(good))

        with tempfile.TemporaryDirectory() as root, self.assertRaisesRegex(self.error, "nve-grid/masts"):
            self.call("run_layers", [bad, good], store, fs, root, RUN_ID, None, "all", True,
                      lambda: NOW, producer=produce)
        self.assertEqual(store.partitions["masts"], ["last-good-feature"])
        self.assertEqual(store.statuses["masts"]["last_success_at"], old["last_success_at"])
        self.assertEqual(store.statuses["masts"]["row_count"], 7)
        self.assertEqual(store.statuses["masts"]["state"], "error")
        self.assertEqual(store.statuses["reservoirs"]["state"], "ready")
        self.assertEqual(store.published, ["reservoirs"])
        self.assertTrue(any("/masts/responses.jsonl.gz" in path for path in fs.files))
        error_log = next(value for path, value in fs.files.items() if path.endswith("/masts/attempt.json"))
        self.assertEqual(json.loads(error_log)["state"], "error")

    def test_complete_snapshot_removes_disappearances_only_in_owned_partition(self):
        config = self.config("reservoirs")
        store, fs = MemoryStore(self.ns), MemoryFS()
        store.partitions.update({"reservoirs": ["removed-id", "retained-id"], "masts": ["untouched"]})

        def produce(spool, http, clock):
            spool.emit(self.make_row(config, "retained-id"))

        with tempfile.TemporaryDirectory() as root:
            self.call("run_layers", [config], store, fs, root, RUN_ID, None, "all", False, lambda: NOW, producer=produce)
        self.assertEqual([row["feature_id"] for row in store.partitions["reservoirs"]], ["retained-id"])
        self.assertEqual(store.partitions["masts"], ["untouched"])
        self.assertEqual(store.statuses["reservoirs"]["last_success_at"], self.stamp)

    def test_duplicate_stable_keys_cannot_replace_last_good_partition(self):
        config = self.config("reservoirs")
        store, fs = MemoryStore(self.ns), MemoryFS()
        store.partitions["reservoirs"] = ["last-good"]

        def produce(spool, http, clock):
            spool.emit(self.make_row(config, "same-id"))
            spool.emit(self.make_row(config, "same-id"))

        with tempfile.TemporaryDirectory() as root, self.assertRaisesRegex(self.error, "nve-reservoir/reservoirs"):
            self.call("run_layers", [config], store, fs, root, RUN_ID, None, "all", True, lambda: NOW, producer=produce)
        self.assertEqual(store.partitions["reservoirs"], ["last-good"])
        self.assertEqual(store.statuses["reservoirs"]["state"], "error")

    def test_programming_errors_are_not_blanket_swallowed(self):
        config = self.config("reservoirs")
        store, fs = MemoryStore(self.ns), MemoryFS()

        def bug(spool, http, clock):
            raise TypeError("programming error")

        with tempfile.TemporaryDirectory() as root, self.assertRaisesRegex(TypeError, "programming"):
            self.call("run_layers", [config], store, fs, root, RUN_ID, None, "all", True, lambda: NOW, producer=bug)
        self.assertEqual(store.statuses["reservoirs"]["state"], "error")
        self.assertFalse(store.published)

    def test_empty_source_gate_only_allows_reconciled_umm_window(self):
        for layer, reconciled, allowed in (("masts", True, False), ("umm", False, False), ("umm", True, True)):
            spool = types.SimpleNamespace(config=self.config(layer), row_count=0, unmapped_count=0, details={"reconciled": reconciled})
            metrics = {"rows": 0, "identities": 0, "invalid": 0, "unmapped": 0}
            if allowed:
                self.call("validate_snapshot_metrics", metrics, spool)
            else:
                with self.assertRaises(self.error):
                    self.call("validate_snapshot_metrics", metrics, spool)

    def test_empty_operational_run_leaves_static_layers_explicitly_uninitialized(self):
        config = self.config("masts")
        store, fs = MemoryStore(self.ns), MemoryFS()
        producer = Mock()
        with tempfile.TemporaryDirectory() as root:
            results = self.call("run_layers", [config], store, fs, root, RUN_ID, None, "operational", True,
                                lambda: NOW, producer=producer)
        producer.assert_not_called()
        self.assertEqual(results[0]["outcome"], "skipped")
        self.assertEqual(store.statuses["masts"]["state"], "error")
        self.assertIsNone(store.statuses["masts"]["last_success_at"])


class RepositoryContractTests(HelpersTest):
    def test_flat_delta_paths_match_external_table_alias_contract(self):
        base = f"abfss://{UUID(int=100)}@example.invalid/{UUID(int=101)}/Tables"
        locations = {
            table: self.call("delta_location", table, f"{base}/{table}/")
            for table in ("geo_map_features", "geo_source_status")
        }
        self.call("validate_table_locations", locations)
        self.assertEqual(locations["geo_map_features"]["relative_path"], "Tables/geo_map_features")
        self.assertEqual(locations["geo_source_status"]["relative_path"], "Tables/geo_source_status")
        self.assertEqual(locations["geo_map_features"]["location"], f"{base}/geo_map_features/")

    def test_schema_enabled_paths_fail_with_actual_locations_and_creation_guidance(self):
        base = f"abfss://{UUID(int=100)}@example.invalid/{UUID(int=101)}/Tables/dbo"
        locations = {
            table: self.call("delta_location", table, f"{base}/{table}")
            for table in ("geo_map_features", "geo_source_status")
        }
        with self.assertRaisesRegex(self.error, "enableSchemas: false") as raised:
            self.call("validate_table_locations", locations)
        for table in locations:
            self.assertIn(f"{base}/{table}", str(raised.exception))
        with self.assertRaisesRegex(self.error, "Lakehouse Tables path"):
            self.call("delta_location", "geo_map_features", "file:///tmp/unrelated/geo_map_features")
        with self.assertRaisesRegex(self.error, "incompatible layout"):
            self.call("validate_table_locations", {})

    def test_exact_feature_and_status_schema(self):
        self.assertEqual(self.ns["FEATURE_FIELDS"], (
            ("feature_id", "string"), ("source_id", "string"), ("layer_id", "string"),
            ("label", "string"), ("geometry_json", "string"), ("properties_json", "string"),
            ("min_lon", "double"), ("min_lat", "double"), ("max_lon", "double"), ("max_lat", "double"),
            ("observed_at", "string"), ("ingested_at", "string"), ("source_url", "string"), ("run_id", "string"),
        ))
        self.assertEqual(self.ns["STATUS_FIELDS"], (
            ("layer_id", "string"), ("source_id", "string"), ("state", "string"),
            ("last_attempt_at", "string"), ("last_success_at", "string"), ("row_count", "long"),
            ("unmapped_count", "long"), ("rejected_count", "long"), ("message", "string"), ("source_url", "string"),
        ))
        self.call("schema_matches", list(reversed(self.ns["FEATURE_FIELDS"])), self.ns["FEATURE_FIELDS"])
        with self.assertRaises(self.error):
            self.call("schema_matches", [("feature_id", "long")], self.ns["FEATURE_FIELDS"])

    def test_exact_layer_and_source_ids(self):
        configs = self.ns["LAYER_CONFIGS"]
        self.assertEqual({item["layer_id"] for item in configs}, {
            "transmission", "regional", "distribution", "sea-cables", "masts", "transformers",
            "hydro-plants", "reservoirs", "power-balance", "power-flows", "grid-frequency", "umm",
        })
        self.assertEqual({item["source_id"] for item in configs}, {"nve-grid", "nve-hydro", "nve-reservoir", "statnett", "nordpool"})
        self.assertEqual(len(configs), 12)

    def test_parameters_are_used_directly_and_wrong_lakehouse_is_rejected(self):
        workspace = str(UUID(int=100))
        context = {"currentWorkspaceId": workspace, "defaultLakehouseWorkspaceId": workspace,
                   "defaultLakehouseName": "Hydro_GeoContext_V6", "defaultLakehouseId": str(UUID(int=101))}
        self.assertEqual(self.call("validate_parameters", "", "V6", "all", "false", context), (workspace, False))
        for parameters in (
            ("", "V6", "invalid", False, context), ("", "V6", "all", "maybe", context),
            ("", "../unsafe", "all", False, context),
            ("", "V6", "all", False, {**context, "defaultLakehouseName": "Synthetic_STID"}),
            (str(UUID(int=102)), "V6", "all", False, context),
        ):
            with self.subTest(parameters=parameters), self.assertRaises(self.error):
                self.call("validate_parameters", *parameters)
        source = NOTEBOOK.read_text(encoding="utf-8")
        for line in ('workspace_id = ""', 'env_suffix = "V6"', 'refresh_mode = "all"', "force_refresh = False"):
            self.assertIn(line, source)
        self.assertNotIn("__main__", source)
        self.assertNotIn("SparkSession.builder", source)
        self.assertNotIn("credentials.get", source)
        self.assertNotIn("api.fabric.microsoft.com", source)

    def test_spark_publication_is_partition_scoped_and_not_per_page(self):
        source = NOTEBOOK.read_text(encoding="utf-8")
        tree = ast.parse(source)
        iterator = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "iter_arcgis")
        iterator_text = ast.get_source_segment(source, iterator)
        self.assertNotIn("spark", iterator_text)
        self.assertNotIn("saveAsTable", iterator_text)
        self.assertIn('"replaceWhere", f"layer_id = \'{spool.config[\'layer_id\']}\'"', source)
        self.assertIn('.partitionBy("layer_id").saveAsTable(FEATURE_TABLE)', source)
        self.assertIn('.option("mode", "FAILFAST")', source)
        self.assertIn("TemporaryDirectory", source)
        self.assertNotIn("except Exception", source)
        compile(source, str(NOTEBOOK), "exec")

    def test_logical_id_is_fresh_and_platform_format_matches(self):
        platform = json.loads(PLATFORM.read_text(encoding="utf-8"))
        identity = platform["config"]["logicalId"]
        self.assertEqual(str(UUID(identity)), "bfb66353-9e7a-4f01-8d67-4b67acbbc49b")
        self.assertEqual(platform["metadata"]["type"], "Notebook")
        self.assertEqual(platform["metadata"]["displayName"], "Geo_001_ingest_energy_context")
        others = [json.loads(path.read_text())["config"]["logicalId"] for path in (ROOT / "Notebooks").glob("*/.platform") if path != PLATFORM]
        self.assertNotIn(identity, others)

    def test_readable_mirror_is_generated_exactly_from_canonical(self):
        expected = canonical_to_ipynb(NOTEBOOK.read_text(encoding="utf-8"))
        actual = json.loads(MIRROR.read_text(encoding="utf-8"))
        self.assertEqual(actual, expected)
        self.assertEqual(len([cell for cell in actual["cells"] if cell["metadata"].get("tags") == ["parameters"]]), 1)
        for index, cell in enumerate(actual["cells"]):
            self.assertTrue(all(line.endswith("\n") for line in cell["source"]))
            if cell["cell_type"] == "code":
                compile("".join(cell["source"]), f"Geo mirror cell {index}", "exec")
                self.assertEqual(cell["outputs"], [])
                self.assertIsNone(cell["execution_count"])


if __name__ == "__main__":
    if sys.argv[1:] == ["--generate-mirror"]:
        MIRROR.write_text(json.dumps(canonical_to_ipynb(NOTEBOOK.read_text(encoding="utf-8")), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Generated {MIRROR.relative_to(ROOT)} from canonical source")
    else:
        unittest.main()
