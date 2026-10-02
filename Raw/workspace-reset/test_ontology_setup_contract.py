import ast
import contextlib
import io
import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[2]
SETUP_NAMES = (
    "RTI_001_create_lakehouse_SelfContained",
    "RTI_001_create_lakehouse_shortcut",
)
ORCHESTRATOR = "RTI_Orchestrator_Setup"


def source(name):
    return (ROOT / "Notebooks" / f"{name}.Notebook" / "notebook-content.py").read_text(encoding="utf-8")


def load_function(name, function, namespace):
    node = next(
        node for node in ast.parse(source(name)).body
        if isinstance(node, ast.FunctionDef) and node.name == function
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), name, "exec"), namespace)
    return namespace[function]


def code_cells(text):
    sections = re.split(r"(?m)^# (?:PARAMETERS CELL|CELL|MARKDOWN) \*+\s*$", text)[1:]
    kinds = re.findall(r"(?m)^# (PARAMETERS CELL|CELL|MARKDOWN) \*+\s*$", text)
    return [
        section.split("# METADATA ********************", 1)[0].strip()
        for kind, section in zip(kinds, sections) if kind != "MARKDOWN"
    ]


class OntologySetupContractTests(unittest.TestCase):
    def test_fabric_metadata_sections_contain_only_meta_json(self):
        notebooks = sorted((ROOT / "Notebooks").glob("*.Notebook/notebook-content.py"))
        self.assertTrue(notebooks)
        pattern = (
            r"(?ms)^# METADATA \*+[^\S\n]*\n(.*?)"
            r"(?=^# (?:METADATA|CELL|PARAMETERS CELL|MARKDOWN) \*+[^\S\n]*$|\Z)"
        )
        for path in notebooks:
            text = path.read_text(encoding="utf-8")
            sections = list(re.finditer(pattern, text))
            with self.subTest(notebook=path.parent.name):
                self.assertTrue(sections, "Missing Fabric metadata section")
            for section in sections:
                line_number = text.count("\n", 0, section.start()) + 1
                with self.subTest(notebook=path.parent.name, metadata_line=line_number):
                    lines = [line for line in section[1].splitlines() if line.strip()]
                    self.assertTrue(lines, "Empty Fabric metadata section")
                    invalid = [line for line in lines if not line.startswith("# META ")]
                    self.assertEqual(
                        invalid, [],
                        "Fabric metadata accepts only # META JSON; put prose inside a cell",
                    )
                    metadata = json.loads("\n".join(line[len("# META "):] for line in lines))
                    self.assertIsInstance(metadata, dict)

    def test_agent_status_writes_are_serialized_after_complete_binding(self):
        tree = ast.parse(source(ORCHESTRATOR))
        assignment = next(
            node for node in tree.body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "setup_dag" for target in node.targets)
        )
        namespace = {"_lh": {"useRootDefaultLakehouse": True}, "per_notebook_timeout_secs": 3600}
        exec(compile(ast.Module(body=[assignment], type_ignores=[]), ORCHESTRATOR, "exec"), namespace)
        activities = {activity["name"]: activity for activity in namespace["setup_dag"]["activities"]}
        self.assertEqual(activities["NB09_dataagent"]["dependencies"], ["NB06_tsbind"])
        self.assertEqual(activities["NB10_opsagent"]["dependencies"], ["NB06_tsbind"])
        for name in ("NB09_dataagent", "NB10_opsagent"):
            self.assertTrue(activities[name]["args"]["useRootDefaultLakehouse"])
        self.assertEqual(activities["NBW01_weather"]["dependencies"], [])
        self.assertGreater(namespace["setup_dag"]["concurrency"], 1)

    def test_graph_definition_update_observes_automatic_refresh_without_duplicate_submit(self):
        tree = ast.parse(source("RTI_006_TimeSeriesBinding_RTI_signal"))
        branch = next(
            node for node in tree.body
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "graph_changed"
        )
        changed_calls = [
            node.func.id for node in ast.walk(ast.Module(body=branch.body, type_ignores=[]))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        unchanged_calls = [
            node.func.id for node in ast.walk(ast.Module(body=branch.orelse, type_ignores=[]))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        self.assertIn("_wait_for_automatic_graph_refresh", changed_calls)
        self.assertNotIn("_refresh_graph", changed_calls)
        self.assertIn("_refresh_graph", unchanged_calls)
        refresh = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_refresh_graph"
        )
        self.assertIn("JobInstanceStatusDeduped", ast.unparse(refresh))

    def test_rebinding_replaces_all_foreign_lakehouse_references(self):
        for name in SETUP_NAMES:
            with self.subTest(name=name):
                notebook = Mock()
                notebook.getDefinition.return_value = json.dumps({
                    "metadata": {"dependencies": {"lakehouse": {
                        "default_lakehouse": "foreign",
                        "known_lakehouses": [{"id": "foreign"}, {"id": "older"}],
                    }}},
                    "cells": [],
                })
                notebook.updateDefinition.return_value = True
                namespace = {
                    "json": json, "notebookutils": SimpleNamespace(notebook=notebook),
                    "lakehouse_id": "current", "lakehouse_name": "Current Lakehouse",
                    "workspace_id": "dev-workspace", "weather_environment_name": "Weather",
                    "weather_environment_id": "weather",
                }
                rebind = load_function(name, "_rebind_lakehouse", namespace)
                self.assertTrue(rebind("RTI_004_build_ontology_mapping_rti_structured")[1])
                written = json.loads(notebook.updateDefinition.call_args.kwargs["content"])
                self.assertEqual(written["metadata"]["dependencies"]["lakehouse"], {
                    "default_lakehouse": "current", "default_lakehouse_name": "Current Lakehouse",
                    "default_lakehouse_workspace_id": "dev-workspace",
                    "known_lakehouses": [{"id": "current"}],
                })

    def test_any_required_notebook_rebinding_failure_aborts_setup(self):
        for name in SETUP_NAMES:
            with self.subTest(name=name):
                node = next(
                    node for node in ast.parse(source(name)).body
                    if isinstance(node, ast.If) and isinstance(node.test, ast.Name)
                    and node.test.id == "binding_failures"
                )
                with self.assertRaisesRegex(RuntimeError, "RTI_004"):
                    exec(compile(ast.Module(body=[node], type_ignores=[]), name, "exec"), {
                        "binding_failures": [("RTI_004", "access denied")],
                    })

    def test_agent_mode_flags_are_absent(self):
        path = ROOT / "Orchestrator_Pipelines" / "01_Pipe_Setup.DataPipeline" / "pipeline-content.json"
        pipeline = json.loads(path.read_text(encoding="utf-8"))["properties"]
        stage_one = next(activity for activity in pipeline["activities"]
                         if activity["name"] == SETUP_NAMES[0])
        for mode in ("ontology_data_agent_mode", "ontology_operations_agent_mode"):
            self.assertNotIn(mode, pipeline["parameters"])
            self.assertNotIn(mode, stage_one["typeProperties"]["parameters"])
            for name in SETUP_NAMES:
                self.assertNotIn(mode, source(name))

    def test_alert_recipient_is_injected_and_persisted_separately(self):
        path = ROOT / "Orchestrator_Pipelines" / "01_Pipe_Setup.DataPipeline" / "pipeline-content.json"
        pipeline = json.loads(path.read_text(encoding="utf-8"))["properties"]
        stage_one = next(activity for activity in pipeline["activities"]
                         if activity["name"] == SETUP_NAMES[0])
        self.assertEqual(pipeline["parameters"]["alert_email_to"]["defaultValue"], "")
        self.assertEqual(
            stage_one["typeProperties"]["parameters"]["alert_email_to"]["value"]["value"],
            "@pipeline().parameters.alert_email_to",
        )
        for name in SETUP_NAMES:
            text = source(name)
            self.assertIn('alert_email_to = ""', text)
            self.assertIn('"alert_email_to": alert_email_to', text)
            self.assertIn('"alert_email_to": alert_email_to,', text)

    def test_pipeline_restores_demo_operations_agent_defaults(self):
        path = ROOT / "Orchestrator_Pipelines" / "01_Pipe_Setup.DataPipeline" / "pipeline-content.json"
        parameters = json.loads(path.read_text(encoding="utf-8"))["properties"]["parameters"]
        self.assertEqual(
            parameters["ops_agent_teams_team_id"]["defaultValue"],
            "c480320e-9204-474b-9b2c-54a53e94f220",
        )
        self.assertEqual(
            parameters["ops_agent_teams_channel_id"]["defaultValue"],
            "19:1-SLGOg6PFivKoyqZrKeH-PG-5JGjwATvoVAEyAr8jA1@thread.tacv2",
        )
        self.assertEqual(
            parameters["ops_agent_run_as_user"]["defaultValue"],
            "admin@mngenvmcap218279.onmicrosoft.com",
        )

    def test_orchestrator_uses_notebook_outcomes_without_agent_contract_parser(self):
        text = source(ORCHESTRATOR)
        self.assertNotIn("_report_agent_capabilities", text)
        self.assertIn("_require_successful_dag(results)\n_activate_weather_schedule()", text)

    def test_raw_notebook_code_matches_canonical_sources(self):
        for name in (*SETUP_NAMES, ORCHESTRATOR):
            with self.subTest(name=name):
                raw = json.loads((ROOT / "Raw" / "RTI_Notebooks" / f"{name}.ipynb").read_text(encoding="utf-8"))
                actual = ["".join(cell["source"]).strip() for cell in raw["cells"] if cell["cell_type"] == "code"]
                self.assertEqual(actual, code_cells(source(name)))
                for index, cell in enumerate(actual):
                    compile(cell, f"{name} cell {index}", "exec")


if __name__ == "__main__":
    unittest.main()
