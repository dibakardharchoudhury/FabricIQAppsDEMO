"""Offline capability-policy contracts; execute isolated AST nodes, never Spark."""

import ast
import base64
import copy
import json
import re
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import Mock, patch


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
                    with self.subTest(number=number, generation=generation, mode=mode):
                        if number == "011" and generation == 1:
                            with self.assertRaisesRegex(RuntimeError, "v2-only"):
                                ns["agent_capability_policy"](generation, mode)
                            continue
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
            for generation, mode in ((2, "disabled"),):
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

    def test_required_v2_operations_attempts_instead_of_static_product_gate(self):
        status = Mock()
        ns = functions(
            "010", "validate_agent_mode", "agent_capability_policy", "check_agent_capability",
            first_setting=lambda *args, **kwargs: "enabled",
            resolve_ontology_id=lambda: "live-id",
            get_ontology_generation=lambda _: 2,
            ops_agent_ontology_datasource_id="",
            persist_agent_status=status,
        )
        result = ns["check_agent_capability"]()
        self.assertEqual(result["status"], "allowed")
        self.assertEqual(result["ontology_id"], "live-id")
        self.assertNotIn("1970", source("010"))

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

    def operations_namespace(self):
        ns = functions("010", "build_configurations")
        names = {"EMBEDDED_OPS_CONFIG_B64", "ACTION_PARAMETERS"}
        nodes = [node for node in ast.parse(source("010")).body
                 if isinstance(node, ast.Assign) and any(assign_to(node, name) for name in names)]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "operations-template", "exec"), ns)
        ns.update(workspace_id="workspace", ops_agent_should_run=False, ops_agent_copy_playbook=True)
        return ns

    def test_full_operations_configuration_preserves_business_capabilities(self):
        ns = self.operations_namespace()
        config = ns["build_configurations"](
            datasource_id="v2-ontology", pipeline_id="email-pipeline", team_id="team", channel_id="channel")
        self.assertFalse(config["shouldRun"])
        conf = config["configuration"]
        self.assertEqual(conf["dataSources"], {
            "v2-ontology": {"id": "v2-ontology", "type": "Ontology", "workspaceId": "workspace"},
        })
        self.assertEqual(conf["messageDestination"], {
            "kind": "TeamsChannel", "teamId": "team", "channelId": "channel",
        })
        action = next(iter(conf["actions"].values()))
        self.assertEqual(action["connection"]["jobArtifactId"], "email-pipeline")
        self.assertEqual(action["connection"]["jobWorkspaceId"], "workspace")
        self.assertEqual(action["connection"]["itemType"], "Pipeline")
        self.assertEqual(action["connection"]["jobType"], "Pipeline")
        parameters = {item["name"] for item in action["parameters"]}
        self.assertEqual(parameters, {"equipment_id", "facility_id", "quality", "value", "unit", "event_time"})
        rules = config["playbook"]["RuleDefinitions"]
        self.assertEqual(len(rules), 2)
        self.assertTrue(any("BAD" in rule["Name"] for rule in rules.values()))
        self.assertTrue(any("UNCERTAIN" in rule["Name"] for rule in rules.values()))
        for rule in rules.values():
            for binding in rule["ActionBinding"]["ActionBindings"]:
                self.assertTrue(parameters.issubset({item["Name"] for item in binding["ParameterBindings"]}))
        with self.assertRaisesRegex(RuntimeError, "playbook"):
            ns["build_configurations"](copy_playbook=False)

    def test_operations_auth_selects_notebook_pbi_token(self):
        get_token = Mock(return_value="offline-token")
        ns = functions(
            "010", "get_access_token_for_fabric", "get_headers",
            notebookutils=SimpleNamespace(credentials=SimpleNamespace(getToken=get_token)))
        self.assertEqual(ns["get_headers"]()["Authorization"], "Bearer offline-token")
        get_token.assert_called_once_with("pbi")

    def test_operations_update_never_drops_components_on_rejection(self):
        for status in (400, 401, 403, 404, 500, 202):
            response = Mock(status_code=status, text="product error", headers={})
            ns = functions("010", "update_operations_agent_definition", "encode_payload",
                           workspace_id="workspace", FABRIC_API_BASE="base",
                           OPS_AGENT_DEFINITION_FORMAT="OperationsAgentV1",
                           api_request=Mock(return_value=response), wait_for_lro=Mock())
            with self.assertRaises(RuntimeError):
                ns["update_operations_agent_definition"]("agent", {"configuration": {}, "playbook": {}})
            ns["api_request"].assert_called_once()
            ns["wait_for_lro"].assert_not_called()

    def test_operations_readback_requires_all_components_and_exact_source(self):
        ns = self.operations_namespace()
        expected = ns["build_configurations"](
            datasource_id="ontology", pipeline_id="pipeline", team_id="team", channel_id="channel")
        for changed in ("source", "playbook", "actions", "teams", "none"):
            actual = json.loads(json.dumps(expected))
            if changed == "source":
                actual["configuration"]["dataSources"]["ontology"]["workspaceId"] = "other"
            elif changed == "playbook":
                actual["playbook"] = {}
            elif changed == "actions":
                actual["configuration"]["actions"] = {}
            elif changed == "teams":
                actual["configuration"].pop("messageDestination")
            check = functions(
                "010", "verify_operations_readback", "require_retained", "read_json_part",
                get_ontology_generation=Mock(return_value=2),
                get_definition_parts=Mock(return_value=[json_part("Configurations.json", actual)]))
            if changed == "none":
                check["verify_operations_readback"]("agent", "ontology", expected)
            else:
                with self.assertRaises(RuntimeError):
                    check["verify_operations_readback"]("agent", "ontology", expected)

    def test_operations_provisioning_attempts_all_business_components_and_raises_actual_failure(self):
        for outcome in ("success", "product_rejection", "missing_mailbox"):
            ns = functions(
                "010", "deploy_operations_agent", ops_agent_ontology_datasource_id="",
                get_ontology_generation=Mock(return_value=2),
                ops_agent_teams_team_id="team", ops_agent_teams_channel_id="channel",
                ops_agent_copy_playbook=True, ops_agent_should_run=False,
                ops_agent_run_as_user="", ops_agent_name="agent", OPS_AGENT_DESCRIPTION="description",
                get_access_token_for_fabric=Mock(), check_run_as=Mock(),
                resolve_email_connection_id=Mock(return_value=None if outcome == "missing_mailbox" else "mailbox"),
                ALERT_EMAIL_TO="operations@example.test", get_signed_in_upn=Mock(),
                PIPELINE_NAME="Pipe_SendEmailAlert", PIPELINE_DESCRIPTION="alerts",
                build_email_pipeline_content=Mock(return_value={"properties": {}}),
                create_data_pipeline=Mock(return_value={"id": "pipeline"}),
                get_definition_parts=Mock(), read_json_part=Mock(return_value={"properties": {}}),
                require_retained=Mock(), create_operations_agent=Mock(return_value={"id": "agent"}),
                build_configurations=Mock(return_value={"configuration": {}, "playbook": {"rules": "full"}}),
                update_operations_agent_definition=Mock(), verify_operations_readback=Mock())
            if outcome == "product_rejection":
                ns["update_operations_agent_definition"].side_effect = RuntimeError("actual product failure")
            if outcome == "success":
                result = ns["deploy_operations_agent"]("ontology")
                self.assertEqual(result["email_pipeline_id"], "pipeline")
                self.assertEqual(result["ops_agent_id"], "agent")
            else:
                with self.assertRaisesRegex(RuntimeError, "actual product failure|OAuth2 connection is missing"):
                    ns["deploy_operations_agent"]("ontology")
            ns["create_data_pipeline"].assert_called_once()
            ns["create_operations_agent"].assert_called_once()
            ns["build_configurations"].assert_called_once_with(
                should_run=False, copy_playbook=True, team_id="team", channel_id="channel",
                datasource_id="ontology", pipeline_id="pipeline")
            ns["update_operations_agent_definition"].assert_called_once()
            if outcome == "product_rejection":
                ns["verify_operations_readback"].assert_not_called()
            else:
                ns["verify_operations_readback"].assert_called_once()

    def test_operations_outer_failure_is_persisted_and_reraised(self):
        attempt = next(node for node in ast.parse(source("010")).body
                       if isinstance(node, ast.Try) and "deploy_operations_agent" in ast.unparse(node))
        failure = RuntimeError("actual product rejection")
        ns = {
            "deploy_operations_agent": Mock(side_effect=failure),
            "persist_agent_status": Mock(), "result": {"ontology_id": "ontology"},
        }
        with self.assertRaisesRegex(RuntimeError, "actual product rejection"):
            exec(compile(ast.Module(body=[attempt], type_ignores=[]), "operations-deploy", "exec"), ns)
        ns["persist_agent_status"].assert_called_once_with("failed", "actual product rejection")

    def test_operations_generation_failure_prevents_pipeline_and_agent_writes(self):
        ns = functions("010", "deploy_operations_agent", get_ontology_generation=Mock(return_value=1),
                       create_data_pipeline=Mock(), create_operations_agent=Mock())
        with self.assertRaisesRegex(RuntimeError, "live v2"):
            ns["deploy_operations_agent"]("legacy")
        ns["create_data_pipeline"].assert_not_called()
        ns["create_operations_agent"].assert_not_called()

    def test_forced_email_connection_must_be_a_real_oauth2_mailbox(self):
        for credential in ("ServicePrincipal", "OAuth2"):
            response = Mock()
            response.json.return_value = {
                "connectionDetails": {"type": "MicrosoftOutlook"},
                "credentialDetails": {"credentialType": credential},
            }
            ns = functions(
                "010", "resolve_email_connection_id", "_is_office365_connection", "_office365_cred_type",
                first_setting=lambda *args, **kwargs: "mailbox", api_request=Mock(return_value=response),
                FABRIC_API_BASE="base")
            if credential == "OAuth2":
                self.assertEqual(ns["resolve_email_connection_id"](), "mailbox")
            else:
                with self.assertRaisesRegex(RuntimeError, "OAuth2"):
                    ns["resolve_email_connection_id"]()

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

    def test_publication_is_preserved_but_inconclusive_runtime_fails(self):
        tree = ast.parse(source("009"))
        deploy = next(
            node for node in tree.body if isinstance(node, ast.Try)
            and "create_data_agent" in ast.unparse(node)
        )
        for mismatch_at in ("submission", "before_publish", "after_publish", "draft_only", "none", "healthy"):
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
            elif mismatch_at in ("none", "healthy"):
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
                probe_data_agent_ontology=Mock(return_value={
                    "status": "verified" if mismatch_at == "healthy" else "inconclusive",
                    "reason": "Functional probe result", "evidence": {},
                }),
            )
            if mismatch_at == "healthy":
                exec(compile(ast.Module(body=[deploy], type_ignores=[]), "readback", "exec"), ns)
                final = ns["persist_agent_status"].call_args
                self.assertEqual(final.args[0], "ready")
                self.assertEqual(final.kwargs["runtime_status"], "verified")
                self.assertEqual(final.kwargs["publication_status"], "published")
                continue
            if mismatch_at == "none":
                with self.assertRaisesRegex(RuntimeError, "runtime inconclusive"):
                    exec(compile(ast.Module(body=[deploy], type_ignores=[]), "readback", "exec"), ns)
                calls = ns["persist_agent_status"].call_args_list
                published_calls = [call for call in calls if call.args[0] == "published"]
                self.assertEqual(len(published_calls), 1)
                self.assertEqual(published_calls[0].kwargs["publication_status"], "published")
                self.assertEqual(published_calls[0].kwargs["id"], "agent-id")
                self.assertEqual(published_calls[0].kwargs["name"], "agent")
                runtime_calls = [call for call in calls if "runtime_evidence" in call.kwargs]
                self.assertEqual(runtime_calls[0].kwargs["runtime_status"], "inconclusive")
                self.assertEqual(runtime_calls[0].kwargs["publication_status"], "published")
                self.assertEqual(calls[-1].args[0], "failed")
                self.assertEqual(ns["get_item_definition"].call_count, 3)
                continue
            message = "No verified published" if mismatch_at == "draft_only" else "does not match selected ontology"
            with self.assertRaisesRegex(RuntimeError, message):
                exec(compile(ast.Module(body=[deploy], type_ignores=[]), "readback", "exec"), ns)
            self.assertEqual(ns["persist_agent_status"].call_args.args[0], "failed")
            ns["probe_data_agent_ontology"].assert_not_called()
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

    def test_nb09_failed_runtime_rerun_preserves_sql_and_custom_capabilities(self):
        sql_tables = ast.literal_eval(next(node.value for node in ast.parse(source("011")).body
                                          if assign_to(node, "SQL_TABLES")))
        sql = {
            "type": "sql_database", "artifactId": "sql-id", "workspaceId": "ws",
            "dataSourceInstructions": "Keep operational SQL guidance",
            "elements": [{"type": "sql_database.table", "display_name": name, "is_selected": True,
                          "children": [{"id": "custom-column", "is_selected": True}]} for name, _ in sql_tables],
            "customSqlOptions": {"preserve": True},
        }
        stage = {
            "$schema": "existing-stage-schema",
            "aiInstructions": "Original ontology instructions\nOperational SQL guidance\nUser custom rules",
            "customOptions": {"preserve": True},
        }
        ontology = {
            "type": "ontology", "artifactId": "ontology", "workspaceId": "ws",
            "displayName": "Custom ontology label", "dataSourceInstructions": "Custom ontology guidance",
            "elements": [
                {"id": "ui-id", "type": "ontology.entity", "display_name": "facilities",
                 "is_selected": False, "description": "custom description",
                 "children": [{"id": "custom-property", "is_selected": True}]},
                {"id": "extra-entity", "type": "ontology.entity", "display_name": "custom_entity",
                 "is_selected": True, "children": []},
            ],
        }
        baseline = []
        for version in ("draft", "published"):
            for path, value in (
                ("stage_config.json", stage), ("ontology-source/datasource.json", ontology),
                ("sql-source/datasource.json", sql), ("custom_queries.json", {"queries": ["custom query"]}),
            ):
                baseline.append(json_part(f"Files/Config/{version}/{path}", value))
        custom_part = {
            "path": "Files/custom/document.txt", "payloadType": "InlineBase64",
            "payload": base64.b64encode(b"custom agent knowledge").decode(), "customPartMetadata": "preserve",
        }
        baseline.append(custom_part)
        state = {"parts": copy.deepcopy(baseline)}

        def update(_agent, definition):
            state["parts"] = copy.deepcopy(definition["parts"])

        def publish(*_args):
            by_path = {part["path"]: copy.deepcopy(part) for part in state["parts"]}
            for part in state["parts"]:
                if part["path"].startswith("Files/Config/draft/"):
                    path = part["path"].replace("/draft/", "/published/", 1)
                    by_path[path] = {**copy.deepcopy(part), "path": path}
            state["parts"] = list(by_path.values())

        ns = functions(
            "009", "upsert_part", "encode_payload", "decode_payload", "validate_agent_ontology_sources",
            "require_v2_ontology", "verify_agent_source_readback", "build_stage_obj", "build_datasource_obj",
            workspace_id="ws", ontology_name="ontology-name", ontology_id="ontology",
            get_ontology_generation=Mock(return_value=2), data_agent_name="agent",
            DATA_AGENT_DESCRIPTION="description", FABRIC_API_BASE="base",
            DRAFT_STAGE_CONFIG_PATH="Files/Config/draft/stage_config.json",
            DATASOURCE_PATH="Files/Config/draft/ontology-source/datasource.json",
            STAGE_CONFIG_SCHEMA_URL="default-stage-schema", DATASOURCE_SCHEMA_URL="default-source-schema",
            DATASOURCE_TYPE="ontology", AI_INSTRUCTIONS="default ontology instructions",
            ONTOLOGY_ELEMENTS=[("facilities", "facility_id,facility_name"), ("equipment", "equipment_id")],
            get_spn_access_token_for_fabric=Mock(),
            create_data_agent=Mock(return_value={"id": "agent-id", "_created_this_run": False}),
            get_item_definition=Mock(side_effect=lambda _id: {"definition": {"parts": copy.deepcopy(state["parts"])}}),
            update_item_definition=Mock(side_effect=update), publish_data_agent=Mock(side_effect=publish),
            enable_preview_runtime=Mock(), persist_agent_status=Mock(),
            probe_data_agent_ontology=Mock(return_value={
                "status": "failed", "reason": "Ontology source unavailable", "evidence": {},
            }),
        )
        deployment = next(node for node in ast.parse(source("009")).body
                          if isinstance(node, ast.Try) and "create_data_agent" in ast.unparse(node))
        with self.assertRaisesRegex(RuntimeError, "runtime failed"):
            exec(compile(ast.Module(body=[deployment], type_ignores=[]), "preserving-rerun", "exec"), ns)
        actual = {part["path"]: part for part in state["parts"]}
        for version in ("draft", "published"):
            prefix = f"Files/Config/{version}/"
            self.assertEqual(ns["decode_payload"](actual[prefix + "stage_config.json"]["payload"]), stage)
            retained_sql = ns["decode_payload"](actual[prefix + "sql-source/datasource.json"]["payload"])
            self.assertEqual(retained_sql, sql)
            self.assertEqual(len(retained_sql["elements"]), 5)
            retained_ontology = ns["decode_payload"](actual[prefix + "ontology-source/datasource.json"]["payload"])
            self.assertEqual(retained_ontology["elements"][:2], ontology["elements"])
            self.assertEqual(retained_ontology["displayName"], "Custom ontology label")
            self.assertEqual(retained_ontology["dataSourceInstructions"], "Custom ontology guidance")
            self.assertEqual(len(retained_ontology["elements"]), 3)
            self.assertEqual(ns["build_datasource_obj"](retained_ontology, "ontology"), retained_ontology)
            self.assertEqual(ns["decode_payload"](actual[prefix + "custom_queries.json"]["payload"]),
                             {"queries": ["custom query"]})
        self.assertEqual(actual[custom_part["path"]], custom_part)
        ns["update_item_definition"].assert_called_once()
        ns["publish_data_agent"].assert_called_once()
        self.assertEqual(ns["persist_agent_status"].call_args.args[0], "failed")
        self.assertEqual(ns["build_stage_obj"]({})["aiInstructions"], "default ontology instructions")
        with self.assertRaisesRegex(RuntimeError, "not a string"):
            ns["build_stage_obj"]({"aiInstructions": {"invalid": True}})
        with self.assertRaisesRegex(RuntimeError, "malformed"):
            ns["build_datasource_obj"]({"elements": None}, "ontology")

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
            step_errors=[],
        )
        attempt = next(
            node for node in ast.parse(source("011")).body
            if isinstance(node, ast.Try) and node.finalbody and "resolve_agent_extension_target" in ast.unparse(node)
        )
        exec(compile(ast.Module(body=[attempt], type_ignores=[]), "folder-extension", "exec"), ns)
        self.assertIn("target folder", str(ns["step_errors"][0]))
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
            "published_missing_sql", "published_missing_ontology", "published_changed_schema",
            "ontology_changed", "valid", "valid_multi_source_evidence",
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
            elif scenario == "ontology_changed":
                published[-2] = json_part("Files/Config/published/ontology-source/datasource.json", {
                    "artifactId": "ontology", "workspaceId": "ws", "type": "ontology", "elements": ["changed"],
                })
            ontology_source = {"artifactId": "ontology", "workspaceId": "ws", "type": "ontology"}
            proof = {
                "verification": "ontology_facilities_smoke_v1", "agent_id": "agent",
                "ontology_id": "ontology", "workspace_id": "ws",
                "published_sources": {"Files/Config/published/ontology-source/datasource.json": ontology_source},
                "expected_facilities": [{"facility_id": "id", "facility_name": "name"}],
                "returned_facilities": [{"facility_id": "id", "facility_name": "name"}],
            }
            baseline = [ontology_part(), ontology_part("published")]
            if scenario == "valid_multi_source_evidence":
                baseline += [draft_sql, published_sql]
                proof["published_sources"][published_path] = submitted
            ns = functions(
                "011", "extend_data_agent_sql_source", "verify_agent_source_readback",
                "validate_agent_ontology_sources", "require_v2_ontology", "published_agent_sources",
                "decode_payload", "encode_payload", "upsert_part",
                get_ontology_generation=Mock(return_value=2), workspace_id="ws",
                get_item_definition=Mock(side_effect=[
                    {"definition": {"parts": baseline}},
                    {"definition": {"parts": draft}}, {"definition": {"parts": published}},
                ]),
                sql_db_item_id="sql-id", sql_server="server", sql_database="database",
                SQL_DATASOURCE_PATH=sql_path, SQL_TABLES=[("table", "description")],
                DRAFT_STAGE_CONFIG_PATH="Files/Config/draft/stage_config.json",
                fetch_sql_table_schema=Mock(return_value={"table": [("id", "int")]}),
                build_sql_datasource_obj=Mock(return_value=submitted),
                build_stage_obj=Mock(return_value={}),
                update_item_definition=Mock(), enable_preview_runtime=Mock(), publish_data_agent=Mock(),
                first_setting=Mock(return_value=json.dumps(proof)),
            )
            if scenario in ("valid", "valid_multi_source_evidence", "ontology_changed"):
                outcome = ns["extend_data_agent_sql_source"]("agent", "ontology")
                self.assertEqual(outcome["status"], "published")
                self.assertEqual(outcome["runtime_status"], "inconclusive" if scenario == "ontology_changed" else "verified")
                self.assertEqual(ns["get_item_definition"].call_count, 3)
            else:
                with self.subTest(scenario=scenario), self.assertRaises(RuntimeError):
                    ns["extend_data_agent_sql_source"]("agent", "ontology")
                if scenario.startswith("draft_"):
                    ns["publish_data_agent"].assert_not_called()
                else:
                    ns["publish_data_agent"].assert_called_once()

    def extension_namespace(self, mode="auto", prior="ready", agents=None, generation=2,
                            runtime="verified", publication="published"):
        values = {
            "ontology_data_agent_mode": mode, "data_agent_deployment_status": prior,
            "ontology_name": "ontology",
            "data_agent_runtime_status": runtime, "data_agent_publication_status": publication,
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

    def test_sql_graphql_scaffolding_runs_but_required_agent_failure_is_recorded(self):
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
            result = ns["step_results"]["data_agent_sql_source"]
            self.assertEqual(result["status"], "skipped" if mode == "disabled" else "failed")
            self.assertEqual(bool(ns["step_errors"]), mode != "disabled")

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
                "step_errors": [],
                "json": json,
            }
            ns[failing_call].side_effect = PermissionError("forbidden")
            exec(compile(ast.Module(body=[attempt], type_ignores=[]), "extension", "exec"), ns)
            terminal = next(node for node in tree.body if isinstance(node, ast.If)
                            and isinstance(node.test, ast.Name) and node.test.id == "step_errors")
            with self.assertRaisesRegex(RuntimeError, "Operational setup failed"):
                exec(compile(ast.Module(body=[terminal], type_ignores=[]), "terminal", "exec"), ns)
            ns["persist_sql_extension_status"].assert_called_once_with(
                {"status": "failed", "reason": "forbidden"}
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

    def test_auto_and_enabled_sql_extension_fail_on_unavailable_agent(self):
        for mode in ("auto", "enabled"):
            for prior, agents in (("published", []), ("blocked", []), ("failed", []), ("", [])):
                with self.subTest(mode=mode, prior=prior):
                    ns = self.extension_namespace(mode=mode, prior=prior, agents=agents)
                    with self.assertRaises(RuntimeError):
                        ns["resolve_agent_extension_target"]()
            ns = self.extension_namespace(
                mode=mode, generation=1, agents=[{"id": "agent-id", "displayName": "agent"}])
            with self.assertRaisesRegex(RuntimeError, "v2-only"):
                ns["resolve_agent_extension_target"]()

    def test_sql_extension_rejects_publication_without_runtime_evidence(self):
        for runtime in ("failed", "blocked", "inconclusive", "checking", ""):
            with self.subTest(runtime=runtime):
                ns = self.extension_namespace(
                    runtime=runtime, agents=[{"id": "agent-id", "displayName": "agent"}])
                with self.assertRaisesRegex(RuntimeError, "Publication alone is insufficient"):
                    ns["resolve_agent_extension_target"]()
                ns["get_ontology_generation"].assert_not_called()

    def test_sql_republication_does_not_reuse_stale_runtime_proof(self):
        tree = ast.parse(source("011"))
        attempt = next(
            node for node in tree.body if isinstance(node, ast.Try)
            and node.finalbody and "resolve_agent_extension_target" in ast.unparse(node)
        )
        result = {"status": "published", "runtime_status": "inconclusive", "reason": "Fresh probe required"}
        ns = {
            "resolve_agent_extension_target": Mock(return_value=(
                "agent", {"status": "allowed", "ontology_id": "ontology"})),
            "extend_data_agent_sql_source": Mock(return_value=result),
            "persist_sql_extension_status": Mock(), "step_results": {}, "step_errors": [], "json": json,
        }
        exec(compile(ast.Module(body=[attempt], type_ignores=[]), "extension", "exec"), ns)
        self.assertEqual(ns["step_results"]["data_agent_sql_source"], result)
        ns["persist_sql_extension_status"].assert_called_once_with(result)
        self.assertEqual(len(ns["step_errors"]), 1)
        terminal = next(node for node in tree.body if isinstance(node, ast.If)
                        and isinstance(node.test, ast.Name) and node.test.id == "step_errors")
        with self.assertRaisesRegex(RuntimeError, "Operational setup failed") as raised:
            exec(compile(ast.Module(body=[terminal], type_ignores=[]), "terminal", "exec"), ns)
        self.assertEqual(str(raised.exception.__cause__), "Fresh probe required")
        result["runtime_status"] = "verified"
        result["reason"] = "Unchanged ontology source proof retained; SQL runtime unverified"
        ns["step_errors"] = []
        exec(compile(ast.Module(body=[attempt], type_ignores=[]), "extension", "exec"), ns)
        self.assertEqual(ns["step_errors"], [])

    def test_sql_publication_preserves_verified_scoped_evidence(self):
        spark = SimpleNamespace(createDataFrame=Mock())
        delta = SimpleNamespace(DeltaTable=SimpleNamespace(forName=Mock()))
        ns = functions("011", "persist_sql_extension_status", spark=spark,
                       F=SimpleNamespace(current_timestamp=Mock()), settings_table_name="settings")
        with patch.dict("sys.modules", {"delta": SimpleNamespace(tables=delta), "delta.tables": delta}):
            ns["persist_sql_extension_status"]({
                "status": "published", "runtime_status": "verified", "reason": "Ontology proof retained"})
        written = {row["setting_name"]: row["setting_value"]
                   for row in spark.createDataFrame.call_args.args[0]}
        self.assertEqual(written["data_agent_sql_source_status"], "published")
        self.assertNotIn("data_agent_runtime_evidence", written)
        self.assertNotIn("data_agent_runtime_status", written)
        self.assertNotIn("data_agent_deployment_status", written)

    def test_runtime_failure_status_preserves_published_identity_in_delta(self):
        spark = SimpleNamespace(createDataFrame=Mock())
        delta = SimpleNamespace(DeltaTable=SimpleNamespace(forName=Mock()))
        ns = functions("009", "persist_agent_status", spark=spark,
                       F=SimpleNamespace(current_timestamp=Mock()), settings_table_name="settings")
        with patch.dict("sys.modules", {"delta": SimpleNamespace(tables=delta), "delta.tables": delta}):
            ns["persist_agent_status"](
                "published", "identity readback verified", publication_status="published",
                id="agent-id", name="agent", runtime_status="checking")
            ns["persist_agent_status"](
                "failed", "unsupported ontology API", runtime_status="failed",
                runtime_reason="unsupported ontology API", runtime_evidence={"ontology_id": "ontology"})
            ns["persist_agent_status"]("failed", "Data Agent runtime failed")
        saved = {}
        for call in spark.createDataFrame.call_args_list:
            saved.update({row["setting_name"]: row["setting_value"] for row in call.args[0]})
        self.assertEqual(saved["data_agent_publication_status"], "published")
        self.assertEqual(saved["data_agent_id"], "agent-id")
        self.assertEqual(saved["data_agent_name"], "agent")
        self.assertEqual(saved["data_agent_deployment_status"], "failed")
        self.assertEqual(saved["data_agent_runtime_status"], "failed")
        self.assertEqual(json.loads(saved["data_agent_runtime_evidence"]), {"ontology_id": "ontology"})

    def runtime_namespace(self, result, delegated=True):
        def response(request_id, value, status=200, headers=None):
            return Mock(
                status_code=status, headers=headers or {"Content-Type": "application/json"},
                json=Mock(return_value={"jsonrpc": "2.0", "id": request_id, "result": value}),
            )

        claims = {"scp": "DataAgent.Execute.All"} if delegated else {"idtyp": "app"}
        token = "header." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode() + ".signature"
        replies = [
            response(1, {"protocolVersion": "2025-03-26"},
                     headers={"Mcp-Session-Id": "session", "Content-Type": "application/json"}),
            response(None, {}, status=202),
            response(2, {"tools": [{
                "name": "DataAgent_agent", "inputSchema": {
                    "type": "object", "properties": {"userQuestion": {"type": "string"}},
                    "required": ["userQuestion"],
                },
            }]}),
            response(3, result),
        ]
        request = SimpleNamespace(post=Mock(side_effect=replies), RequestException=ConnectionError)
        ns = functions(
            "009", "mcp_response_result", "mcp_semantic_error", "probe_data_agent_ontology",
            "published_agent_sources", "normalized_facility_rows", "facility_rows_from_mcp",
            "validate_agent_ontology_sources", "require_v2_ontology",
            re=re, time=time, uuid=uuid, requests=request,
            FABRIC_API_BASE="https://api.fabric.microsoft.com", workspace_id="ws", data_agent_name="agent",
            lakehouse_id="lakehouse", first_setting=Mock(return_value="silver_facilities"),
            get_item_definition=Mock(return_value={"definition": {"parts": [ontology_part("published")]}}),
            get_ontology_generation=Mock(return_value=2),
            facility_probe_reference=Mock(return_value=[
                {"facility_id": "FACILITY_RTI_001", "facility_name": "Sloy Power Station"},
                {"facility_id": "FACILITY_RTI_002", "facility_name": "Foyers Power Station"},
                {"facility_id": "FACILITY_RTI_003", "facility_name": "Pitlochry Power Station"},
            ]),
            notebookutils=SimpleNamespace(credentials=SimpleNamespace(getToken=Mock(return_value=token))),
        )
        return ns, replies

    def test_runtime_probe_rejects_actual_semantic_error_with_iserror_false(self):
        error = ("Ontology source unavailable: The analyze_ontology tool call failed with "
                 "The request is invalid. This API version is not supported for the specified Ontology item.")
        ns, _ = self.runtime_namespace({
            "isError": False, "content": [{"type": "text", "text": json.dumps({"error": error})}],
        })
        outcome = ns["probe_data_agent_ontology"]("agent-id", "ontology")
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["reason"], error)
        self.assertEqual(outcome["evidence"]["ontology_id"], "ontology")
        self.assertNotIn("Bearer", json.dumps(outcome))
        self.assertNotIn(".signature", json.dumps(outcome))
        calls = ns["requests"].post.call_args_list
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[-1].kwargs["json"]["method"], "tools/call")
        self.assertEqual(calls[-1].kwargs["json"]["params"]["name"], "DataAgent_agent")
        self.assertIn("Use ONLY the Ontology item ontology", calls[-1].kwargs["json"]["params"]["arguments"]["userQuestion"])
        self.assertEqual(calls[-1].kwargs["headers"]["Mcp-Session-Id"], "session")
        self.assertEqual(calls[-1].kwargs["headers"]["MCP-Protocol-Version"], "2025-03-26")
        ns["notebookutils"].credentials.getToken.assert_called_once_with("pbi")

    def test_runtime_probe_fails_closed_for_unverifiable_answers(self):
        for result in (
            {"isError": False, "content": []},
            {"isError": False, "content": [{"type": "text", "text": "There are 5 facilities."}]},
            {"isError": False, "content": [{"type": "text", "text": json.dumps({
                "ontology_id": "ontology", "workspace_id": "ws", "count": 5, "verified": True,
            })}]},
            {"structuredContent": {"source": "ontology", "executed": True, "count": 5}},
        ):
            with self.subTest(result=result):
                ns, _ = self.runtime_namespace(result)
                outcome = ns["probe_data_agent_ontology"]("agent-id", "ontology")
                self.assertEqual(outcome["status"], "inconclusive")
                self.assertTrue(outcome["reason"])

    def test_runtime_probe_has_positive_exact_content_path_without_receipts(self):
        for shape in ("text", "fenced", "structured", "data_error_word"):
            with self.subTest(shape=shape):
                ns, replies = self.runtime_namespace({})
                expected = ns["facility_probe_reference"].return_value
                if shape == "data_error_word":
                    expected[0]["facility_name"] = "Error Creek Station"
                text = json.dumps(list(reversed(expected)))
                result = ({"structuredContent": {"facilities": expected}} if shape == "structured" else {
                    "isError": False, "content": [{
                        "type": "text", "text": "```json\n" + text + "\n```" if shape == "fenced" else text,
                    }],
                })
                replies[-1].json.return_value["result"] = result
                outcome = ns["probe_data_agent_ontology"]("agent-id", "ontology")
                self.assertEqual(outcome["status"], "verified")
                self.assertEqual(outcome["evidence"]["expected_facilities"], expected)
                self.assertEqual(outcome["evidence"]["returned_facilities"], expected)
                self.assertEqual(outcome["evidence"]["verification"], "ontology_facilities_smoke_v1")
                question = outcome["evidence"]["question"]
                for row in expected:
                    self.assertNotIn(row["facility_id"], question)
                    self.assertNotIn(row["facility_name"], question)

    def test_runtime_probe_rejects_wrong_name_or_missing_rows(self):
        for actual in (
            [{"facility_id": "FACILITY_RTI_001", "facility_name": "RTI Demo Hydropower Plant"}],
            [{"facility_id": "FACILITY_RTI_001", "facility_name": "Sloy Power Station"}],
        ):
            ns, _ = self.runtime_namespace({"content": [{"type": "text", "text": json.dumps(actual)}]})
            outcome = ns["probe_data_agent_ontology"]("agent-id", "ontology")
            self.assertEqual(outcome["status"], "failed")
            self.assertIn("do not match", outcome["reason"])

    def test_healthy_multi_source_rerun_needs_no_cached_proof_and_still_rejects_semantic_errors(self):
        ns, replies = self.runtime_namespace({})
        ns["settings"] = {}

        def fresh_setting(name, **_kwargs):
            if name != "silver_facilities_table":
                raise AssertionError(f"No cached proof/settings available for {name}")
            return "silver_facilities"

        ns["first_setting"].side_effect = fresh_setting
        parts = ns["get_item_definition"].return_value["definition"]["parts"]
        parts.append(json_part("Files/Config/published/sql/datasource.json", {
            "type": "sql_database", "artifactId": "sql", "workspaceId": "ws",
        }))
        baseline = copy.deepcopy(parts)
        replies[-1].json.return_value["result"] = {
            "content": [{"type": "text", "text": json.dumps(ns["facility_probe_reference"].return_value)}]}
        outcome = ns["probe_data_agent_ontology"]("agent-id", "ontology")
        self.assertEqual(outcome["status"], "verified")
        self.assertIn("functional smoke test", outcome["evidence"]["scope"])
        self.assertIn("not attested", outcome["evidence"]["scope"])
        self.assertIn("Use ONLY the Ontology item ontology", outcome["evidence"]["question"])
        self.assertEqual(parts, baseline)
        unsupported = ("The request is invalid. This API version is not supported for the specified Ontology item. "
                       "RAID: 5082e3f9-6b1e-45a5-b675-acfe7d8660e2")
        replies[-1].json.return_value["result"] = {
            "isError": False, "content": [{"type": "text", "text": json.dumps({"error": unsupported})}]}
        ns["requests"].post.side_effect = replies
        outcome = ns["probe_data_agent_ontology"]("agent-id", "ontology")
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["reason"], unsupported)
        self.assertEqual(parts, baseline)

    def test_facility_reference_reads_configured_lakehouse_not_prompt_constants(self):
        context = {"defaultLakehouseId": "lh", "defaultLakehouseWorkspaceId": "ws"}
        spark = SimpleNamespace(read=SimpleNamespace(table=Mock()))
        rows = [{"facility_id": "live-id", "facility_name": "Live lakehouse name"}]
        frame = spark.read.table.return_value
        frame.select.return_value.orderBy.return_value.limit.return_value.collect.return_value = [
            SimpleNamespace(asDict=lambda: rows[0])]
        ns = functions("009", "facility_probe_reference", "normalized_facility_rows",
                       notebookutils=SimpleNamespace(runtime=SimpleNamespace(context=context)),
                       lakehouse_id="lh", workspace_id="ws", re=re, spark=spark,
                       first_setting=Mock(return_value="silver_facilities"))
        self.assertEqual(ns["facility_probe_reference"](), rows)
        spark.read.table.assert_called_once_with("silver_facilities")
        frame.select.assert_called_once_with("facility_id", "facility_name")
        frame.select.return_value.orderBy.return_value.limit.assert_called_once_with(5)
        context["defaultLakehouseId"] = "wrong-lakehouse"
        with self.assertRaisesRegex(RuntimeError, "configured Lakehouse"):
            ns["facility_probe_reference"]()

    def test_runtime_probe_recognizes_error_shapes(self):
        for result in (
            {"isError": True, "content": [{"type": "text", "text": "bad"}]},
            {"content": [{"type": "resource", "resource": {"text": '{"error":"Unavailable"}'}}]},
            {"structuredContent": {"status": "failed", "message": "bad"}},
            {"content": [{"type": "text", "text": "```json\n{\"error\":\"Unavailable\"}\n```"}]},
            {"content": [{"type": "text", "text": "This API version is not supported for this item."}]},
            {"content": [{"type": "text", "text": '{"error":'}]},
        ):
            with self.subTest(result=result):
                ns, _ = self.runtime_namespace(result)
                self.assertEqual(ns["probe_data_agent_ontology"]("agent-id", "ontology")["status"], "failed")

    def test_runtime_probe_rejects_app_identity_before_network(self):
        ns, _ = self.runtime_namespace({}, delegated=False)
        outcome = ns["probe_data_agent_ontology"]("agent-id", "ontology")
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("delegated", outcome["reason"])
        ns["requests"].post.assert_not_called()

    def test_runtime_probe_transport_failures_do_not_become_ready(self):
        for failure in ("http", "jsonrpc", "id", "tool", "schema", "pagination"):
            with self.subTest(failure=failure):
                ns, replies = self.runtime_namespace({})
                if failure == "http":
                    replies[0].raise_for_status.side_effect = ConnectionError("HTTP 403")
                elif failure == "jsonrpc":
                    replies[0].json.return_value = {"jsonrpc": "2.0", "id": 1, "error": {"message": "denied"}}
                elif failure == "id":
                    replies[0].json.return_value["id"] = 999
                elif failure == "tool":
                    replies[2].json.return_value["result"]["tools"][0]["name"] = "unrelated_agent"
                elif failure == "schema":
                    replies[2].json.return_value["result"]["tools"][0]["inputSchema"]["required"] = ["other"]
                else:
                    replies[2].json.return_value["result"]["nextCursor"] = "more"
                outcome = ns["probe_data_agent_ontology"]("agent-id", "ontology")
                self.assertEqual(outcome["status"], "failed")
                self.assertTrue(outcome["reason"])
                self.assertLess(ns["requests"].post.call_count, 4)

    def test_mcp_sse_matches_request_id_and_rejects_duplicate_results(self):
        ns, _ = self.runtime_namespace({})
        message = json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"content": []}})
        response = Mock(
            status_code=200, headers={"Content-Type": "text/event-stream"},
            text='data: {"jsonrpc":"2.0","method":"notifications/progress"}\n\ndata: ' + message + "\n\n",
        )
        self.assertEqual(ns["mcp_response_result"](response, 3), {"content": []})
        response.text += "data: " + message + "\n\n"
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            ns["mcp_response_result"](response, 3)

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
