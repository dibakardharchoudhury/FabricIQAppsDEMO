"""Offline capability-policy contracts; execute isolated AST nodes, never Spark."""

import ast
import base64
import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[2]
NAMES = {
    "009": "RTI_009_build_data_agent",
    "010": "RTI_010_build_operations_agent",
    "011": "RTI_011_seed_sql_wire_graphql_agent",
}


def source(number):
    return (ROOT / "Notebooks" / (NAMES[number] + ".Notebook") / "notebook-content.py").read_text(
        encoding="utf-8"
    )


def functions(number, *names, **overrides):
    namespace = {"Optional": Optional, "json": json, "base64": base64}
    namespace.update(overrides)
    tree = ast.parse(source(number))
    included = set(names) | {"definition_parts"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in included]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), number, "exec"), namespace)
    return namespace


def assign_to(node, name):
    return isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == name for target in node.targets
    )


class NotebookExit(Exception):
    pass


def ontology_part(stage="draft", ontology_id="ontology", workspace="ws"):
    return {
        "path": f"Files/Config/{stage}/ontology-source/datasource.json",
        "payloadType": "InlineBase64",
        "payload": base64.b64encode(json.dumps({
            "type": "ontology", "artifactId": ontology_id, "workspaceId": workspace,
        }).encode("utf-8")).decode("ascii"),
    }


def json_part(path, value):
    return {
        "path": path, "payloadType": "InlineBase64",
        "payload": base64.b64encode(json.dumps(value).encode("utf-8")).decode("ascii"),
    }


class CapabilityTests(unittest.TestCase):
    def test_generation_mode_matrix(self):
        for number in NAMES:
            ns = functions(number, "validate_agent_mode", "agent_capability_policy")
            for generation in (1, 2):
                for mode in ("auto", "enabled", "disabled"):
                    expected = "allowed"
                    if generation == 1:
                        expected = "blocked"
                    elif mode == "disabled":
                        expected = "skipped"
                    elif generation == 2 and (number == "010" or mode == "auto"):
                        expected = "blocked"
                    with self.subTest(number=number, generation=generation, mode=mode):
                        result = ns["agent_capability_policy"](generation, mode)
                        self.assertEqual(result["status"], expected)
                        if expected != "allowed":
                            self.assertTrue(result["reason"])
            for invalid in ("", "enable", "maybe", None):
                with self.assertRaises(ValueError):
                    ns["validate_agent_mode"](invalid)
            with self.assertRaises(ValueError):
                ns["agent_capability_policy"](3, "enabled")

    def test_generation_is_read_from_live_ontology_api(self):
        for number in NAMES:
            for value in (1, 2, None, 0, 3, "2", True):
                response = Mock()
                response.json.return_value = {"properties": {"generation": value}}
                api = Mock(return_value=response)
                ns = functions(
                    number, "get_ontology_generation", api_request=api,
                    FABRIC_API_BASE="https://api.fabric.microsoft.com", workspace_id="workspace",
                )
                if type(value) is int and value in (1, 2):
                    self.assertEqual(ns["get_ontology_generation"]("ontology"), value)
                else:
                    with self.assertRaisesRegex(RuntimeError, "properties.generation"):
                        ns["get_ontology_generation"]("ontology")
                api.assert_called_once_with(
                    "GET", "https://api.fabric.microsoft.com/v1/workspaces/workspace/ontologies/ontology"
                )
                response.raise_for_status.assert_called_once()

    def test_policy_guard_exits_before_any_agent_or_pipeline_writes(self):
        for number in ("009", "010"):
            for generation, mode in ((2, "auto"), (2, "disabled")):
                status = Mock()
                exit_mock = Mock(side_effect=NotebookExit)
                ns = functions(
                    number, "validate_agent_mode", "agent_capability_policy", "check_agent_capability",
                    first_setting=lambda *args, **kwargs: mode,
                    resolve_ontology_id=Mock(return_value="live-id"),
                    get_ontology_generation=Mock(return_value=generation),
                    ops_agent_ontology_datasource_id="",
                    persist_agent_status=status,
                    notebookutils=SimpleNamespace(notebook=SimpleNamespace(exit=exit_mock)),
                )
                ns.update(create_data_agent=Mock(), create_operations_agent=Mock(), create_data_pipeline=Mock())
                tree = ast.parse(source(number))
                start_name = "data_agent_item_id" if number == "009" else "result"
                start = next(i for i, node in enumerate(tree.body) if assign_to(node, start_name))
                with self.assertRaises(NotebookExit):
                    exec(compile(ast.Module(body=tree.body[start:], type_ignores=[]), number, "exec"), ns)
                ns["create_data_agent"].assert_not_called()
                ns["create_operations_agent"].assert_not_called()
                ns["create_data_pipeline"].assert_not_called()
                payload = json.loads(exit_mock.call_args.args[0])
                self.assertEqual(payload["generation"], generation)
                self.assertEqual(payload["status"], status.call_args.args[0])
                self.assertEqual(payload["capability"], "data_agent" if number == "009" else "operations_agent")
                self.assertTrue(payload["reason"])

    def test_enabled_v2_operations_is_actionable_failure_before_writes(self):
        status = Mock()
        ns = functions(
            "010", "validate_agent_mode", "agent_capability_policy", "check_agent_capability",
            first_setting=lambda *args, **kwargs: "enabled",
            resolve_ontology_id=lambda: "live-id",
            get_ontology_generation=lambda _: 2,
            ops_agent_ontology_datasource_id="",
            persist_agent_status=status,
        )
        with self.assertRaisesRegex(RuntimeError, "1970"):
            ns["check_agent_capability"]()
        self.assertEqual(status.call_args.args[0], "blocked")

    def test_final_deployment_exit_reports_capability_not_readiness(self):
        for number, capability in (("009", "data_agent"), ("010", "operations_agent")):
            tree = ast.parse(source(number))
            exit_node = next(
                node for node in reversed(tree.body) if isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and ast.unparse(node.value.func) == "notebookutils.notebook.exit"
            )
            exit_mock = Mock()
            ns = {
                "json": json,
                "notebookutils": SimpleNamespace(notebook=SimpleNamespace(exit=exit_mock)),
                "ontology_generation": 2,
                "validate_agent_mode": lambda value: value,
                "first_setting": lambda *args, **kwargs: "auto",
                "agent_deployment_result": {
                    "status": "published" if number == "009" else "blocked",
                    "reason": "Runtime behavior has not been verified.",
                },
            }
            ns["result"] = {"capability": capability, **ns["agent_deployment_result"]}
            exec(compile(ast.Module(body=[exit_node], type_ignores=[]), "exit", "exec"), ns)
            payload = json.loads(exit_mock.call_args.args[0])
            self.assertEqual(payload["capability"], capability)
            self.assertEqual(payload["status"], ns["agent_deployment_result"]["status"])
            self.assertEqual(payload["reason"], ns["agent_deployment_result"]["reason"])
            self.assertNotEqual(payload["status"], "ready")

    def test_no_legacy_operations_payload_or_provisioning_path_remains(self):
        code = source("010")
        for forbidden in (
            "EMBEDDED_OPS_CONFIG_B64", "EMBEDDED_PIPELINE", "OperationsAgentV1",
            "create_operations_agent", "create_data_pipeline", "updateDefinition",
            "build_configurations", "RuleDefinitions",
        ):
            self.assertNotIn(forbidden, code)
        ns = functions("010", "api_request")
        for method in ("POST", "PATCH", "DELETE"):
            with self.assertRaisesRegex(ValueError, "read-only"):
                ns["api_request"](method, "https://example.invalid")

    def test_generation1_is_blocked_before_agent_writes_in_every_mode(self):
        for number in ("009", "010"):
            for mode in ("auto", "enabled", "disabled"):
                status = Mock()
                ns = functions(
                    number, "validate_agent_mode", "agent_capability_policy", "check_agent_capability",
                    first_setting=lambda *args, **kwargs: mode,
                    resolve_ontology_id=Mock(return_value="legacy-id"),
                    get_ontology_generation=Mock(return_value=1),
                    persist_agent_status=status,
                    create_data_agent=Mock(), create_operations_agent=Mock(),
                    create_data_pipeline=Mock(), update_item_definition=Mock(),
                )
                tree = ast.parse(source(number))
                start_name = "data_agent_item_id" if number == "009" else "result"
                start = next(i for i, node in enumerate(tree.body) if assign_to(node, start_name))
                with self.assertRaisesRegex(RuntimeError, "v2-only"):
                    exec(compile(ast.Module(body=tree.body[start:], type_ignores=[]), number, "exec"), ns)
                for name in ("create_data_agent", "create_operations_agent", "create_data_pipeline", "update_item_definition"):
                    ns[name].assert_not_called()
                self.assertEqual(status.call_args.args[0], "blocked")

    def test_failed_generation_check_clears_old_success_status(self):
        for number in ("009", "010"):
            status = Mock()
            ns = functions(
                number, "validate_agent_mode", "check_agent_capability",
                first_setting=lambda *args, **kwargs: "enabled",
                resolve_ontology_id=Mock(return_value="ontology"),
                get_ontology_generation=Mock(side_effect=PermissionError("denied")),
                persist_agent_status=status,
            )
            with self.assertRaises(PermissionError):
                ns["check_agent_capability"]()
            self.assertEqual(status.call_args_list[0].args[0], "checking")
            self.assertEqual(status.call_args.args, ("failed", "denied"))

    def test_enabled_v2_data_agent_failures_are_not_success(self):
        tree = ast.parse(source("009"))
        deploy = next(
            node for node in tree.body if isinstance(node, ast.Try)
            and "create_data_agent" in ast.unparse(node)
        )
        for fail_at in (
            "create_data_agent", "validate_agent_ontology_sources", "update_item_definition",
            "verify_agent_source_readback", "publish_data_agent",
        ):
            status = Mock()
            ns = functions("009", "upsert_part", "encode_payload")
            ns.update({
                "ontology_generation": 2, "ontology_id": "ontology", "data_agent_item_id": None,
                "data_agent_name": "agent", "DATA_AGENT_DESCRIPTION": "description",
                "DRAFT_STAGE_CONFIG_PATH": "stage", "DATASOURCE_PATH": "datasource",
                "ONTOLOGY_ELEMENTS": [], "FABRIC_API_BASE": "base", "workspace_id": "ws",
                "get_spn_access_token_for_fabric": Mock(),
                "create_data_agent": Mock(return_value={"id": "agent-id", "_created_this_run": True}),
                "get_item_definition": Mock(return_value={"definition": {"parts": []}}),
                "validate_agent_ontology_sources": Mock(),
                "verify_agent_source_readback": Mock(),
                "build_stage_obj": Mock(return_value={}), "build_datasource_obj": Mock(return_value={}),
                "update_item_definition": Mock(), "enable_preview_runtime": Mock(),
                "publish_data_agent": Mock(), "persist_agent_status": status,
            })
            ns[fail_at].side_effect = RuntimeError("real failure")
            with self.assertRaisesRegex(RuntimeError, "real failure"):
                exec(compile(ast.Module(body=[deploy], type_ignores=[]), "deploy", "exec"), ns)
            self.assertEqual(status.call_args.args[0], "failed")
            if fail_at == "validate_agent_ontology_sources":
                ns["update_item_definition"].assert_not_called()
                ns["enable_preview_runtime"].assert_not_called()
                ns["publish_data_agent"].assert_not_called()

    def test_agent_sources_must_all_be_live_v2_including_published_parts(self):
        for number in ("009", "011"):
            ns = functions(
                number, "require_v2_ontology", "validate_agent_ontology_sources",
                workspace_id="ws", get_ontology_generation=Mock(return_value=2),
            )
            parts = [ontology_part(), ontology_part("published")]
            ns["validate_agent_ontology_sources"](parts, "ontology", require_draft=True, require_published=True)
            self.assertEqual(ns["get_ontology_generation"].call_count, 2)
            for generations in ((1, 2), (2, 1), (1, 1)):
                ns["get_ontology_generation"] = Mock(side_effect=generations)
                with self.assertRaisesRegex(RuntimeError, "v2-only"):
                    ns["validate_agent_ontology_sources"](parts, "ontology", require_draft=True)

    def test_unverifiable_or_missing_ontology_sources_fail_closed(self):
        for number in ("009", "011"):
            ns = functions(
                number, "require_v2_ontology", "validate_agent_ontology_sources",
                workspace_id="ws", get_ontology_generation=Mock(return_value=2),
            )
            for parts in (
                [], [ontology_part("published")], [ontology_part(workspace="other-workspace")],
                [ontology_part(ontology_id="")],
            ):
                with self.assertRaises(RuntimeError):
                    ns["validate_agent_ontology_sources"](parts, "ontology", require_draft=True)
            malformed = ontology_part()
            malformed["payload"] = base64.b64encode(b"{}").decode("ascii")
            with self.assertRaisesRegex(RuntimeError, "missing source type"):
                ns["validate_agent_ontology_sources"]([malformed], "ontology")

    def test_sql_extension_rejects_legacy_agent_before_any_source_writes(self):
        ns = functions(
            "011", "extend_data_agent_sql_source", "validate_agent_ontology_sources", "require_v2_ontology",
            workspace_id="ws", get_ontology_generation=Mock(return_value=1),
            get_item_definition=Mock(return_value={"definition": {"parts": [ontology_part()]}}),
            update_item_definition=Mock(), enable_preview_runtime=Mock(), publish_data_agent=Mock(),
            fetch_sql_table_schema=Mock(),
        )
        with self.assertRaisesRegex(RuntimeError, "v2-only"):
            ns["extend_data_agent_sql_source"]("agent", "ontology")
        for name in ("update_item_definition", "enable_preview_runtime", "publish_data_agent", "fetch_sql_table_schema"):
            ns[name].assert_not_called()

    def test_selected_v2_ontology_does_not_authorize_unrelated_or_legacy_agent(self):
        for source_id, source_generation in (("unrelated-v2", 2), ("legacy", 1)):
            ns = functions(
                "011", "extend_data_agent_sql_source", "validate_agent_ontology_sources", "require_v2_ontology",
                workspace_id="ws",
                get_ontology_generation=Mock(side_effect=lambda item_id: (
                    2 if item_id == "selected-v2" else source_generation
                )),
                get_item_definition=Mock(return_value={
                    "definition": {"parts": [ontology_part(ontology_id=source_id)]},
                }),
                update_item_definition=Mock(), enable_preview_runtime=Mock(), publish_data_agent=Mock(),
                fetch_sql_table_schema=Mock(),
            )
            with self.assertRaisesRegex(RuntimeError, "does not match selected ontology"):
                ns["extend_data_agent_sql_source"]("agent", "selected-v2")
            for name in ("update_item_definition", "enable_preview_runtime", "publish_data_agent", "fetch_sql_table_schema"):
                ns[name].assert_not_called()

    def test_both_notebooks_reject_unrelated_published_sources_even_if_v2(self):
        for number in ("009", "011"):
            ns = functions(
                number, "require_v2_ontology", "validate_agent_ontology_sources",
                workspace_id="ws", get_ontology_generation=Mock(return_value=2),
            )
            parts = [ontology_part(), ontology_part("published", "unrelated-v2")]
            with self.assertRaisesRegex(RuntimeError, "does not match selected ontology"):
                ns["validate_agent_ontology_sources"](parts, "ontology", require_draft=True)
            self.assertEqual(len(parts), 2)

    def test_submitted_and_readback_identity_gate_published_success(self):
        tree = ast.parse(source("009"))
        deploy = next(
            node for node in tree.body if isinstance(node, ast.Try)
            and "create_data_agent" in ast.unparse(node)
        )
        for mismatch_at in ("submission", "before_publish", "after_publish", "draft_only", "none"):
            matching = {"definition": {"parts": [ontology_part()]}}
            published = {"definition": {"parts": [ontology_part(), ontology_part("published")]}}
            unrelated = {"definition": {"parts": [ontology_part(ontology_id="unrelated-v2")]}}
            responses = [{"definition": {"parts": []}}]
            if mismatch_at == "before_publish":
                responses += [unrelated]
            elif mismatch_at == "after_publish":
                responses += [matching, unrelated]
            elif mismatch_at == "draft_only":
                responses += [matching, matching]
            elif mismatch_at == "none":
                responses += [matching, published]
            ns = functions(
                "009", "upsert_part", "encode_payload", "validate_agent_ontology_sources",
                "require_v2_ontology", "verify_agent_source_readback",
                workspace_id="ws", get_ontology_generation=Mock(return_value=2),
                ontology_generation=2, ontology_id="ontology", data_agent_item_id=None,
                data_agent_name="agent", DATA_AGENT_DESCRIPTION="description",
                DRAFT_STAGE_CONFIG_PATH="Files/Config/draft/stage_config.json",
                DATASOURCE_PATH="Files/Config/draft/ontology-source/datasource.json",
                ONTOLOGY_ELEMENTS=[], FABRIC_API_BASE="base",
                get_spn_access_token_for_fabric=Mock(),
                create_data_agent=Mock(return_value={"id": "agent-id", "_created_this_run": True}),
                get_item_definition=Mock(side_effect=responses),
                build_stage_obj=Mock(return_value={}),
                build_datasource_obj=Mock(return_value={
                    "artifactId": "unrelated-v2" if mismatch_at == "submission" else "ontology",
                    "workspaceId": "ws", "type": "ontology",
                }),
                update_item_definition=Mock(), enable_preview_runtime=Mock(),
                publish_data_agent=Mock(), persist_agent_status=Mock(),
            )
            if mismatch_at == "none":
                exec(compile(ast.Module(body=[deploy], type_ignores=[]), "readback", "exec"), ns)
                self.assertEqual(ns["persist_agent_status"].call_args.args[0], "published")
                self.assertEqual(ns["get_item_definition"].call_count, 3)
                continue
            message = "No verified published" if mismatch_at == "draft_only" else "does not match selected ontology"
            with self.assertRaisesRegex(RuntimeError, message):
                exec(compile(ast.Module(body=[deploy], type_ignores=[]), "readback", "exec"), ns)
            self.assertEqual(ns["persist_agent_status"].call_args.args[0], "failed")
            self.assertNotIn("published", [call.args[0] for call in ns["persist_agent_status"].call_args_list])
            if mismatch_at in ("submission", "before_publish"):
                ns["publish_data_agent"].assert_not_called()
            else:
                ns["publish_data_agent"].assert_called_once()
            if mismatch_at == "submission":
                ns["update_item_definition"].assert_not_called()

    def test_data_source_builder_rejects_generation1(self):
        ns = functions(
            "009", "build_datasource_obj", "require_v2_ontology",
            get_ontology_generation=Mock(return_value=1),
        )
        with self.assertRaisesRegex(RuntimeError, "v2-only"):
            ns["build_datasource_obj"]({}, "legacy")

    def test_malformed_existing_agent_baseline_never_updates_or_publishes(self):
        tree = ast.parse(source("009"))
        deploy = next(
            node for node in tree.body if isinstance(node, ast.Try)
            and "create_data_agent" in ast.unparse(node)
        )
        malformed = (
            None, {}, {"definition": None}, {"definition": {}},
            {"definition": {"parts": None}}, {"definition": {"parts": {}}},
            {"definition": {"parts": []}}, {"definition": {"parts": [{}]}},
            {"definition": {"parts": [ontology_part(), ontology_part()]}},
            {"definition": {"parts": [{
                "path": "bad", "payloadType": "InlineBase64", "payload": "!not-base64!",
            }]}},
            "empty-http-body",
        )
        for envelope in malformed:
            response = Mock(status_code=200)
            if envelope == "empty-http-body":
                response.content = b""
                response.json.side_effect = ValueError("Empty response is not JSON")
            else:
                response.json.return_value = envelope
            ns = functions(
                "009", "get_item_definition",
                ontology_generation=2, ontology_id="ontology", data_agent_item_id=None,
                data_agent_name="agent", DATA_AGENT_DESCRIPTION="description",
                get_spn_access_token_for_fabric=Mock(),
                create_data_agent=Mock(return_value={"id": "existing", "_created_this_run": False}),
                api_request=Mock(return_value=response), FABRIC_API_BASE="base", workspace_id="ws",
                update_item_definition=Mock(), publish_data_agent=Mock(), persist_agent_status=Mock(),
            )
            with self.subTest(envelope=envelope), self.assertRaises((RuntimeError, ValueError)):
                exec(compile(ast.Module(body=[deploy], type_ignores=[]), "baseline", "exec"), ns)
            ns["update_item_definition"].assert_not_called()
            ns["publish_data_agent"].assert_not_called()
            self.assertEqual(ns["persist_agent_status"].call_args.args[0], "failed")

    def test_definition_getter_lro_also_requires_explicit_parts_list(self):
        for number in ("009", "011"):
            accepted = Mock(status_code=202, headers={"Location": "operation"})
            completed = Mock(status_code=200)
            completed.json.return_value = {"definition": {}}
            ns = functions(
                number, "get_item_definition",
                api_request=Mock(side_effect=[accepted, completed]),
                wait_for_lro=Mock(), FABRIC_API_BASE="base", workspace_id="ws",
            )
            with self.assertRaisesRegex(RuntimeError, "explicit list"):
                ns["get_item_definition"]("agent")

    def test_only_new_agent_provenance_allows_explicit_empty_baseline(self):
        for existing in (None, {"id": "existing"}):
            response = Mock(status_code=201, content=b"json")
            response.json.return_value = {"id": "new"}
            ns = functions(
                "009", "create_data_agent", find_item_by_name=Mock(return_value=existing),
                api_request=Mock(return_value=response), DATA_AGENT_ITEM_TYPE="DataAgent",
                FABRIC_API_BASE="base", workspace_id="ws", target_folder_id="folder",
            )
            item = ns["create_data_agent"]("agent")
            self.assertEqual(item["_created_this_run"], existing is None)
            if existing is None:
                self.assertEqual(ns["definition_parts"](
                    {"definition": {"parts": []}}, allow_empty=item["_created_this_run"],
                ), [])
            else:
                ns["api_request"].assert_not_called()
                with self.assertRaisesRegex(RuntimeError, "Empty existing"):
                    ns["definition_parts"](
                        {"definition": {"parts": []}}, allow_empty=item["_created_this_run"],
                    )

    def test_async_creation_requires_authoritative_created_item_id(self):
        for body in ({}, {"id": "created"}):
            accepted = Mock(status_code=202, headers={"Location": "operation"})
            result = Mock()
            result.json.return_value = body
            ns = functions(
                "009", "create_data_agent", find_item_by_name=Mock(return_value=None),
                api_request=Mock(side_effect=[accepted, result]), wait_for_lro=Mock(),
                DATA_AGENT_ITEM_TYPE="DataAgent", FABRIC_API_BASE="base",
                workspace_id="ws", target_folder_id="folder",
            )
            if body:
                self.assertEqual(ns["create_data_agent"]("agent"), {**body, "_created_this_run": True})
            else:
                with self.assertRaisesRegex(RuntimeError, "provenance is unverified"):
                    ns["create_data_agent"]("agent")
            ns["find_item_by_name"].assert_called_once()

    def test_configured_folder_never_falls_back_to_other_folder(self):
        outside = {"id": "outside", "displayName": "ontology", "type": "Ontology", "folderId": "other"}
        for number in ("009", "010"):
            response = Mock()
            response.json.return_value = {"value": [outside]}
            ns = functions(
                number, "check_agent_capability", "validate_agent_mode", "resolve_ontology_id",
                first_setting=lambda *args, **kwargs: "enabled", ontology_name="ontology",
                target_folder_id="target", workspace_id="ws", FABRIC_API_BASE="base",
                api_request=Mock(return_value=response), persist_agent_status=Mock(),
                get_ontology_generation=Mock(return_value=2),
                create_data_agent=Mock(), update_item_definition=Mock(), publish_data_agent=Mock(),
            )
            tree = ast.parse(source(number))
            name = "data_agent_item_id" if number == "009" else "result"
            start = next(i for i, node in enumerate(tree.body) if assign_to(node, name))
            with self.assertRaisesRegex(RuntimeError, "target folder"):
                exec(compile(ast.Module(body=tree.body[start:], type_ignores=[]), "folder", "exec"), ns)
            ns["get_ontology_generation"].assert_not_called()
            ns["create_data_agent"].assert_not_called()
            ns["update_item_definition"].assert_not_called()
            ns["publish_data_agent"].assert_not_called()

        ns = self.extension_namespace(mode="enabled")
        ns["list_items_of_type"].side_effect = lambda kind: (
            [{"id": "agent-id", "displayName": "agent"}] if kind == "DataAgent" else [outside]
        )
        ns.update(
            extend_data_agent_sql_source=Mock(), persist_sql_extension_status=Mock(),
            step_results={"data_agent_sql_source": {"status": "failed", "reason": "not completed"}},
        )
        attempt = next(
            node for node in ast.parse(source("011")).body
            if isinstance(node, ast.Try) and node.finalbody and "resolve_agent_extension_target" in ast.unparse(node)
        )
        with self.assertRaisesRegex(RuntimeError, "target folder"):
            exec(compile(ast.Module(body=[attempt], type_ignores=[]), "folder-extension", "exec"), ns)
        ns["get_ontology_generation"].assert_not_called()
        ns["extend_data_agent_sql_source"].assert_not_called()
        self.assertEqual(ns["persist_sql_extension_status"].call_args.args[0]["status"], "failed")

    def test_folder_uniqueness_checks_all_ontology_pages(self):
        for number in ("009", "010"):
            first = Mock()
            second = Mock()
            item = {"id": "one", "displayName": "ontology", "type": "Ontology", "folderId": "folder"}
            first.json.return_value = {"value": [item], "continuationUri": "next-page"}
            second.json.return_value = {"value": [{**item, "id": "two"}]}
            ns = functions(
                number, "resolve_ontology_id", api_request=Mock(side_effect=[first, second]),
                FABRIC_API_BASE="base", workspace_id="ws", target_folder_id="folder", ontology_name="ontology",
            )
            with self.assertRaisesRegex(RuntimeError, "target folder"):
                ns["resolve_ontology_id"]()
            self.assertEqual(ns["api_request"].call_count, 2)

    def test_sql_extension_requires_draft_and_published_ontology_and_sql_evidence(self):
        sql_path = "Files/Config/draft/sql-database-sql/datasource.json"
        published_path = sql_path.replace("/draft/", "/published/")
        submitted = {
            "artifactId": "sql-id", "workspaceId": "ws", "type": "sql_database",
            "elements": [{"id": "schema", "children": [{"id": "table"}]}],
        }
        draft_sql = json_part(sql_path, submitted)
        published_sql = json_part(published_path, submitted)
        for scenario in (
            "draft_wrong_sql", "draft_missing_ontology", "published_wrong_sql",
            "published_missing_sql", "published_missing_ontology", "published_changed_schema", "valid",
        ):
            draft = [ontology_part(), draft_sql]
            published = [ontology_part(), draft_sql, ontology_part("published"), published_sql]
            if scenario == "draft_wrong_sql":
                draft = [ontology_part(), json_part(sql_path, {**submitted, "artifactId": "other"})]
            elif scenario == "draft_missing_ontology":
                draft = [draft_sql]
            elif scenario == "published_wrong_sql":
                published[-1] = json_part(published_path, {**submitted, "artifactId": "other"})
            elif scenario == "published_missing_sql":
                published = published[:-1]
            elif scenario == "published_missing_ontology":
                published = [ontology_part(), draft_sql, published_sql]
            elif scenario == "published_changed_schema":
                published[-1] = json_part(published_path, {**submitted, "elements": []})
            ns = functions(
                "011", "extend_data_agent_sql_source", "verify_agent_source_readback",
                "validate_agent_ontology_sources", "require_v2_ontology",
                "decode_payload", "encode_payload", "upsert_part",
                get_ontology_generation=Mock(return_value=2), workspace_id="ws",
                get_item_definition=Mock(side_effect=[
                    {"definition": {"parts": [ontology_part()]}},
                    {"definition": {"parts": draft}}, {"definition": {"parts": published}},
                ]),
                sql_db_item_id="sql-id", sql_server="server", sql_database="database",
                SQL_DATASOURCE_PATH=sql_path, SQL_TABLES=[("table", "description")],
                DRAFT_STAGE_CONFIG_PATH="Files/Config/draft/stage_config.json",
                fetch_sql_table_schema=Mock(return_value={"table": [("id", "int")]}),
                build_sql_datasource_obj=Mock(return_value=submitted),
                build_stage_obj=Mock(return_value={}),
                update_item_definition=Mock(), enable_preview_runtime=Mock(), publish_data_agent=Mock(),
            )
            if scenario == "valid":
                self.assertEqual(ns["extend_data_agent_sql_source"]("agent", "ontology")["status"], "published")
                self.assertEqual(ns["get_item_definition"].call_count, 3)
            else:
                with self.subTest(scenario=scenario), self.assertRaises(RuntimeError):
                    ns["extend_data_agent_sql_source"]("agent", "ontology")
                if scenario.startswith("draft_"):
                    ns["publish_data_agent"].assert_not_called()
                else:
                    ns["publish_data_agent"].assert_called_once()

    def extension_namespace(self, mode="auto", prior="", agents=None, generation=2):
        values = {
            "ontology_data_agent_mode": mode, "data_agent_deployment_status": prior,
            "ontology_name": "ontology",
        }
        return functions(
            "011", "validate_agent_mode", "agent_capability_policy",
            "resolve_data_agent_id", "resolve_agent_extension_target",
            first_setting=lambda *names, default=None, **kwargs: next(
                (values[name] for name in names if name in values), default
            ),
            data_agent_id=None, data_agent_name="agent", target_folder_id="folder",
            DATA_AGENT_ITEM_TYPE="DataAgent",
            list_items_of_type=Mock(side_effect=lambda kind: (
                agents or [] if kind == "DataAgent" else [{
                    "id": "ontology-id", "displayName": "ontology", "folderId": "folder",
                }]
            )),
            get_ontology_generation=Mock(return_value=generation),
        )

    def test_sql_graphql_scaffolding_runs_without_agent(self):
        for mode, prior, generation, agents in (
            ("auto", "", 1, []),
            ("disabled", "", 1, [{"id": "agent-id", "displayName": "agent"}]),
            ("auto", "blocked", 2, []),
            ("auto", "", 2, [{"id": "agent-id", "displayName": "agent"}]),
            ("enabled", "", 1, [{"id": "agent-id", "displayName": "agent"}]),
        ):
            ns = self.extension_namespace(mode, prior, agents, generation)
            ns.update({
                "resolve_sql_database": Mock(return_value=("sql-id", "server", "database")),
                "sql_db_item_name": "sql", "seed_sql_database": Mock(),
                "create_graphql_api": Mock(return_value={"id": "graphql-id"}),
                "graphql_api_name": "graphql", "lakehouse_name": "lakehouse", "lakehouse_id": "lh-id",
                "resolve_sql_analytics_endpoint_id": Mock(return_value="endpoint"),
                "get_item_definition": Mock(return_value={"definition": {"parts": []}}),
                "upsert_part": Mock(return_value=[]), "build_graphql_definition": Mock(return_value={}),
                "GRAPHQL_DEFINITION_PATH": "graphql", "GRAPHQL_OBJECTS": [],
                "update_item_definition": Mock(), "extend_data_agent_sql_source": Mock(),
                "persist_sql_extension_status": Mock(),
            })
            tree = ast.parse(source("011"))
            start = next(i for i, node in enumerate(tree.body) if assign_to(node, "step_results"))
            end = next(i for i, node in enumerate(tree.body) if assign_to(node, "persist"))
            nodes = [node for node in tree.body[start:end] if not isinstance(node, ast.FunctionDef)]
            exec(compile(ast.Module(body=nodes, type_ignores=[]), "scaffolding", "exec"), ns)
            ns["seed_sql_database"].assert_called_once()
            ns["create_graphql_api"].assert_called_once()
            ns["update_item_definition"].assert_called_once_with("graphql-id", {"parts": []})
            ns["extend_data_agent_sql_source"].assert_not_called()
            self.assertIn(ns["step_results"]["data_agent_sql_source"]["status"], ("skipped", "blocked"))

    def test_sql_extension_failure_is_persisted_and_reraised(self):
        tree = ast.parse(source("011"))
        attempt = next(
            node for node in tree.body if isinstance(node, ast.Try)
            and node.finalbody and "resolve_agent_extension_target" in ast.unparse(node)
        )
        for failing_call in ("resolve_agent_extension_target", "extend_data_agent_sql_source"):
            ns = {
                "resolve_agent_extension_target": Mock(return_value=(
                    "agent", {"status": "allowed", "ontology_id": "ontology"},
                )),
                "extend_data_agent_sql_source": Mock(),
                "persist_sql_extension_status": Mock(),
                "step_results": {"data_agent_sql_source": {"status": "failed", "reason": "failure"}},
                "json": json,
            }
            ns[failing_call].side_effect = PermissionError("forbidden")
            with self.assertRaises(PermissionError):
                exec(compile(ast.Module(body=[attempt], type_ignores=[]), "extension", "exec"), ns)
            ns["persist_sql_extension_status"].assert_called_once_with(
                {"status": "failed", "reason": "failure"}
            )

    def test_extension_auth_and_unknown_generation_errors_surface(self):
        ns = self.extension_namespace(agents=[{"id": "agent-id", "displayName": "agent"}])
        ns["list_items_of_type"].side_effect = PermissionError("denied")
        with self.assertRaises(PermissionError):
            ns["resolve_agent_extension_target"]()
        ns = self.extension_namespace(agents=[{"id": "agent-id", "displayName": "agent"}])
        ns["get_ontology_generation"].side_effect = RuntimeError("unknown generation")
        with self.assertRaisesRegex(RuntimeError, "unknown generation"):
            ns["resolve_agent_extension_target"]()

    def test_enabled_v2_sql_extension_requires_live_agent(self):
        ns = self.extension_namespace(
            mode="enabled", generation=2, agents=[{"id": "agent-id", "displayName": "agent"}]
        )
        agent_id, policy = ns["resolve_agent_extension_target"]()
        self.assertEqual((agent_id, policy["status"]), ("agent-id", "allowed"))
        self.assertEqual(policy["ontology_id"], "ontology-id")
        ns["get_ontology_generation"].assert_called_once_with("ontology-id")

    def test_raw_distributions_match_canonical_cells_and_preserve_metadata(self):
        for number, name in NAMES.items():
            raw = json.loads((ROOT / "Raw" / "RTI_Notebooks" / (name + ".ipynb")).read_text(encoding="utf-8"))
            canonical_cells = []
            for kind, body in re.findall(
                r"# (MARKDOWN|CELL|PARAMETERS CELL) \*+\n(.*?)(?=\n# (?:MARKDOWN|CELL|PARAMETERS CELL) \*+|\Z)",
                source(number), re.S,
            ):
                body = body.split("# METADATA ********************")[0].strip("\n")
                if kind == "MARKDOWN":
                    body = "\n".join(
                        line[2:] if line.startswith("# ") else "" if line == "#" else line
                        for line in body.splitlines()
                    )
                canonical_cells.append(body)
            self.assertEqual(len(raw["cells"]), len(canonical_cells))
            for cell, expected in zip(raw["cells"], canonical_cells):
                self.assertEqual("".join(cell["source"]).strip("\n"), expected)
                if cell["cell_type"] == "code":
                    ast.parse("".join(cell["source"]))
            self.assertEqual(raw["metadata"]["language_info"]["name"], "python")
            if number == "011":
                self.assertEqual(raw["cells"][1]["metadata"]["tags"], ["parameters"])


if __name__ == "__main__":
    unittest.main()
