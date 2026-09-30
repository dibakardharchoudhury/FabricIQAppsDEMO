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
MODES = ("ontology_data_agent_mode", "ontology_operations_agent_mode")


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
        self.assertEqual(activities["NB10_opsagent"]["dependencies"], ["NB09_dataagent"])
        for name in ("NB09_dataagent", "NB10_opsagent"):
            self.assertTrue(activities[name]["args"]["useRootDefaultLakehouse"])
        self.assertEqual(activities["NBW01_weather"]["dependencies"], [])
        self.assertGreater(namespace["setup_dag"]["concurrency"], 1)

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

    def test_capability_modes_are_injected_and_persisted(self):
        path = ROOT / "Orchestrator_Pipelines" / "01_Pipe_Setup.DataPipeline" / "pipeline-content.json"
        pipeline = json.loads(path.read_text(encoding="utf-8"))["properties"]
        stage_one = next(activity for activity in pipeline["activities"]
                         if activity["name"] == SETUP_NAMES[0])
        for mode in MODES:
            self.assertEqual(pipeline["parameters"][mode]["defaultValue"], "enabled")
            self.assertEqual(stage_one["typeProperties"]["parameters"][mode]["value"]["value"],
                             f"@pipeline().parameters.{mode}")
            for name in SETUP_NAMES:
                text = source(name)
                self.assertIn(f'{mode} = "enabled"', text)
                self.assertIn(f'"{mode}": {mode}', text)
                self.assertLess(text.index('must be auto, enabled, or disabled'),
                                text.index("lakehouse_id = ensure_lakehouse("))

    def test_required_capabilities_fail_closed_and_disabled_is_explicit(self):
        report = load_function(ORCHESTRATOR, "_report_agent_capabilities", {"json": json})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            report({
                "NB09_dataagent": {"exitVal": json.dumps({
                    "status": "published", "mode": "enabled", "generation": 2, "reason": "Source verified"})},
                "NB10_opsagent": {"exitVal": json.dumps({
                    "status": "configured", "mode": "auto", "generation": 2, "reason": "Definition verified"})},
            })
        self.assertIn("NB09_dataagent: published - Source verified", output.getvalue())
        self.assertIn("NB10_opsagent: configured - Definition verified", output.getvalue())
        for mode in ("auto", "enabled", None):
            for status in ("blocked", "failed", "skipped", None):
                with self.subTest(mode=mode, status=status), self.assertRaises(RuntimeError):
                    report({"NB09_dataagent": {"exitVal": {
                        "status": status, "mode": mode, "generation": 2}}, "NB10_opsagent": {}})
        with contextlib.redirect_stdout(output):
            report({name: {"exitVal": {"status": "skipped", "mode": "disabled", "generation": 2}}
                    for name in ("NB09_dataagent", "NB10_opsagent")})
        with self.assertRaises(RuntimeError):
            report({"NB09_dataagent": {"exitVal": "{}"}, "NB10_opsagent": {}})
        with self.assertRaises(RuntimeError):
            report({"NB09_dataagent": {}, "NB10_opsagent": {}})

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
