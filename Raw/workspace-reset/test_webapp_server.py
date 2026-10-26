import importlib.util
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import run_pipeline


MODULE_PATH = Path(__file__).with_name("webapp") / "server.py"
SPEC = importlib.util.spec_from_file_location("fabric_demo_server", MODULE_PATH)
assert SPEC and SPEC.loader
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)
PIPELINE_PARAMS = {
    "key_vault_uri": "https://vault.vault.azure.net/",
    "alert_email_to": "operations@example.test",
    "ops_agent_teams_team_id": "team",
    "ops_agent_teams_channel_id": "channel",
}


class WorkspaceActionTests(unittest.TestCase):
    def setUp(self):
        self.client = SERVER.app.test_client()

    def test_setup_modules_are_separate_from_app_publication(self):
        target = {"tenant": "tenant.example", "workspace": "Demo"}
        for endpoint, stage in (
            ("/api/setup-energy", "energy"), ("/api/setup-foundry", "foundry"), ("/api/deploy-app", "app"),
        ):
            with self.subTest(stage=stage), patch.object(SERVER, "_start", return_value="job") as start:
                response = self.client.post(endpoint, json=target)
                self.assertEqual(response.status_code, 200, response.get_json())
                argv = start.call_args.args[0]
                self.assertEqual(argv[argv.index("--stage") + 1], stage)
                self.assertNotIn("--push-config", argv)
        with patch.object(SERVER, "_start") as start:
            response = self.client.post("/api/deploy-app", json={**target, "createFoundry": True})
            self.assertEqual(response.status_code, 400)
            self.assertIn("Foundry multi-agent setup module", response.get_json()["error"])
            start.assert_not_called()

    def test_full_workflow_prepares_energy_and_foundry_before_final_app(self):
        target = {
            "tenant": "tenant.example", "workspace": "Demo", "repository": "owner/repo", "connectionId": "yes",
            "parameters": PIPELINE_PARAMS,
            "energyMap": "enabled", "createFoundry": False,
        }
        with patch.object(SERVER, "_start_workflow", return_value="job") as start:
            response = self.client.post("/api/full-workflow", json=target)
        self.assertEqual(response.status_code, 200, response.get_json())
        steps = start.call_args.args[0]
        self.assertEqual([step["name"] for step in steps], [
            "GitHub sync", "Setup pipeline", "Energy setup", "Foundry multi-agent setup", "Fabric app deploy",
        ])
        self.assertEqual(steps[3]["env"], {"HYDRO_FOUNDRY_CREATE": "false"})
        self.assertIsNone(steps[4]["env"])
        for step, stage in zip(steps[2:], ("energy", "foundry", "app")):
            self.assertEqual(step["argv"][step["argv"].index("--stage") + 1], stage)
        self.assertEqual([step["phase_offset"] for step in steps],
                         sorted({step["phase_offset"] for step in steps}))

    def test_subscription_discovery_returns_only_enabled_metadata_without_mutating_azure(self):
        accounts = [
            {"id": "sub-a", "name": "Demo", "tenantId": "tenant-a", "state": "Enabled", "user": {"name": "private"}},
            {"id": "sub-b", "tenantId": "tenant-b", "state": "Disabled"},
        ]
        with patch.object(SERVER.subprocess, "run", return_value=Mock(
            returncode=0, stdout=json.dumps(accounts),
        )) as run:
            response = self.client.get("/api/subscriptions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"subscriptions": [
            {"id": "sub-a", "name": "Demo", "tenantId": "tenant-a"},
        ]})
        command = run.call_args.args[0]
        self.assertIn("account list", command if isinstance(command, str) else " ".join(command))
        self.assertEqual(run.call_args.kwargs["timeout"], 40)

    def test_subscription_discovery_reports_cli_and_shape_errors(self):
        for result, expected in (
            (Mock(returncode=1, stderr="Please sign in", stdout=""), 503),
            (Mock(returncode=0, stdout="not-json"), 502),
            (Mock(returncode=0, stdout="{}"), 502),
            (Mock(returncode=0, stdout="[1]"), 502),
        ):
            with self.subTest(expected=expected), patch.object(SERVER.subprocess, "run", return_value=result):
                response = self.client.get("/api/subscriptions")
                self.assertEqual(response.status_code, expected)
                self.assertTrue(response.get_json()["error"])
        with patch.object(SERVER.subprocess, "run", side_effect=subprocess.TimeoutExpired("az", 40)):
            self.assertEqual(self.client.get("/api/subscriptions").status_code, 503)

    def test_timing_renderer_handles_success_failure_escaping_and_invalid_receipts(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        functions = (
            page[page.index("function renderDeploymentTimings("):page.index("let subscriptionLoad =")]
            + page[page.index("function escapeHtml("):page.index("// mm:ss")]
            + page[page.index("function formatElapsed("):page.index("// Starts a job")]
        )
        script = functions + """
const assert = require("node:assert/strict");
assert.equal(renderDeploymentTimings([]), "");
const receipt = {status:"succeeded", total_seconds:65, steps:[
  {section:1, label:"<b>Identity</b>", seconds:65, status:"completed"}
]};
const render = (value) => renderDeploymentTimings(["[deploy] DEPLOY_TIMINGS=" + JSON.stringify(value)]);
const html = render(receipt);
assert.ok(html.includes("01:05"));
assert.ok(html.includes("&lt;b&gt;Identity&lt;/b&gt;"));
assert.ok(!html.includes("<b>Identity</b>"));
assert.ok(render({...receipt,status:"failed"}).includes("(failed)"));
assert.ok(render({steps:"invalid"}).includes('role="alert"'));
assert.ok(render({...receipt,total_seconds:-1}).includes('role="alert"'));
assert.ok(renderDeploymentTimings(["DEPLOY_TIMINGS={"]).includes('role="alert"'));
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def assert_exclusive_action(self, endpoint: str, payload: dict):
        starter = "_start_workflow" if endpoint in ("/api/full-workflow", "/api/run-pipeline") else "_start"
        with patch.object(SERVER, starter, return_value="job-id") as start:
            response = self.client.post(endpoint, json=payload)

        self.assertEqual(response.status_code, 200, response.get_json())
        if starter == "_start":
            self.assertTrue(start.call_args.kwargs.get("exclusive"))
        else:
            self.assertEqual(start.call_count, 1)
            self.assertTrue(SERVER.Job(SERVER.WORKFLOW_PHASES, exclusive=True).exclusive)

    def test_all_workspace_mutations_are_exclusive(self):
        target = {"tenant": "tenant.example", "workspace": "Demo Workspace"}
        actions = [
            (
                "/api/sync",
                {
                    **target,
                    "repository": "owner/repository",
                    "pat": "test-pat",
                },
            ),
            ("/api/delete", target),
            (
                "/api/run-pipeline",
                {
                    **target,
                    "parameters": PIPELINE_PARAMS,
                },
            ),
            ("/api/deploy-app", target),
            ("/api/setup-foundry", target),
            ("/api/setup-energy", target),
            (
                "/api/full-workflow",
                {
                    **target,
                    "repository": "owner/repository",
                    "connectionId": "yes",
                    "parameters": PIPELINE_PARAMS,
                },
            ),
        ]
        for endpoint, payload in actions:
            with self.subTest(endpoint=endpoint):
                self.assert_exclusive_action(endpoint, payload)

    def test_core_setup_includes_energy_once_and_keeps_foundry_separate(self):
        target = {"tenant": "tenant.example", "workspace": "Demo", "parameters": PIPELINE_PARAMS}
        with patch.object(SERVER, "_start_workflow", return_value="core-job") as start:
            response = self.client.post("/api/run-pipeline", json=target)
        self.assertEqual(response.status_code, 200)
        steps = start.call_args.args[0]
        self.assertEqual([step["name"] for step in steps], ["Setup pipeline", "Energy setup"])
        self.assertEqual(steps[1]["argv"][steps[1]["argv"].index("--stage") + 1], "energy")
        self.assertEqual(start.call_args.kwargs["phases"], SERVER.CORE_SETUP_PHASES)
        self.assertLess(steps[0]["phase_offset"], steps[1]["phase_offset"])
        self.assertFalse(any(phase.startswith("Foundry") for phase in SERVER.CORE_SETUP_PHASES))
        with patch.object(SERVER, "_start_workflow", return_value="full-job") as start:
            response = self.client.post("/api/full-workflow", json={
                **target, "repository": "owner/repo", "connectionId": "yes", "energyMap": "keep",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual([step["name"] for step in start.call_args.args[0]].count("Energy setup"), 1)

    def test_full_deploy_is_outside_collapsible_actions_and_all_actions_have_icons(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        navigation = page[page.index('<nav class="nav"'):page.index("</nav>")]
        group = navigation[navigation.index('<details id="deploymentActions"'):navigation.index("</details>")]
        self.assertIn("Deployment Actions", group)
        self.assertNotIn('id="tabWorkflow"', group)
        self.assertIn("> Full Deploy</button>", navigation)
        self.assertNotIn("Full workflow", page)
        for name in ("Sync", "Pipeline", "Energy", "Foundry", "Deploy", "Delete"):
            button = group[group.index(f'id="tab{name}"'):]
            button = button[:button.index("</button>")]
            self.assertIn("<svg", button)
        self.assertIn("Refresh energy sources", group)
        self.assertNotIn("workflowEnableEnergy", page)

    def test_sidebar_toggle_is_accessible_and_remembers_state(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('aria-controls="deploymentSidebar"', page)
        self.assertIn(".page.sidebar-collapsed .shell { grid-template-columns:minmax(0,1fr); }", page)
        code = page[page.index("function setSidebarCollapsed("):page.index("function renderFoundryFields(")]
        script = """
const assert = require("node:assert/strict");
const elements = {
  deploymentSidebar:{hidden:false},
  sidebarToggle:{setAttribute(name,value){this[name]=value},addEventListener(name,handler){this.click=handler}},
  sidebarPreferenceStatus:{textContent:""},
};
const $ = id => elements[id];
let collapsedClass=false;
const document = {querySelector:()=>({classList:{toggle:(name,value)=>{collapsedClass=value}}})};
const localStorage = {getItem:()=>"true",setItem:(key,value)=>{localStorage.saved=value}};
""" + code + """
assert.equal(elements.deploymentSidebar.hidden,true);
assert.equal(elements.sidebarToggle["aria-expanded"],"false");
assert.equal(elements.sidebarToggle.textContent,"Show sidebar");
elements.sidebarToggle.click();
assert.equal(elements.deploymentSidebar.hidden,false);
assert.equal(elements.sidebarToggle["aria-expanded"],"true");
assert.equal(collapsedClass,false);
assert.equal(localStorage.saved,"false");
localStorage.setItem=()=>{throw new Error("blocked")};
elements.sidebarToggle.click();
assert.equal(elements.deploymentSidebar.hidden,true);
assert.match(elements.sidebarPreferenceStatus.textContent,/Could not save.*blocked/);
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_connection_test_only_starts_from_explicit_sync_test_button(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("maybeAutoTest", page)
        self.assertNotIn("runWorkflowTest", page)
        self.assertEqual(page.count('runJob("/api/test-connection"'), 1)
        code = page[
            page.index('document.querySelectorAll("input").forEach'):
            page.index('$("syncBtn").addEventListener')
        ]
        script = """
const assert = require("node:assert/strict");
const fields = new Map();
let activeSync=false, connTestState=null;
const $ = id => {
  if(!fields.has(id)) fields.set(id,{
    value:id, checked:true, disabled:false, open:false, listeners:{},
    classList:{contains:()=>activeSync}, focus(){},
    addEventListener(type,handler){(this.listeners[type]??=[]).push(handler)},
    dispatchEvent(event){for(const handler of this.listeners[event.type]||[])handler(event)},
  });
  return fields.get(id);
};
const inputs=["tenant","workspace","repository","branch","directory","pat","reuseConn",
              "workflowRepository","workflowBranch","workflowDirectory","workflowPat","workflowReuseConn"].map($);
const document={querySelectorAll:()=>inputs};
const refresh=()=>{}, setTestResult=()=>{}, testValid=()=>true;
const connValue=()=>"yes", workflowReuseChecked=()=>$("workflowReuseConn").checked;
const activateTab=name=>{activeSync=name==="sync"};
const requests=[];
let complete;
const runJob=async(url,payload,panel,onDone)=>{requests.push({url,payload});complete=onDone};
""" + code + """
(async()=>{
  for(const field of inputs) {
    field.dispatchEvent(new Event("input"));
    field.dispatchEvent(new Event("change"));
  }
  assert.equal(requests.length,0,"Edits and reuse toggles started a test");
  await runTest();
  assert.equal(requests.length,0,"Hidden Sync pane started a test");
  $("workflowTestBtn").dispatchEvent(new Event("click"));
  assert.equal(activeSync,true);
  assert.equal($("repository").value,$("workflowRepository").value);
  assert.equal($("pat").value,$("workflowPat").value);
  assert.equal(requests.length,0,"Opening Sync from Full Deploy started a test");
  await $("testBtn").listeners.click[0]();
  assert.equal(requests.length,1);
  assert.equal(requests[0].url,"/api/test-connection");
  await $("testBtn").listeners.click[0]();
  assert.equal(requests.length,1,"Duplicate in-flight test started");
  complete({returncode:0});
  activeSync=false;
  await runTest();
  assert.equal(requests.length,1,"Test started after leaving Sync");
})().catch(error=>{console.error(error);process.exitCode=1});
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_launcher_does_not_expose_agent_mode_flags(self):
        response = self.client.get("/api/pipeline-params")
        self.assertEqual(response.status_code, 200)
        specs = {spec["name"]: spec for spec in response.get_json()["parameters"]}
        for name in ("ontology_data_agent_mode", "ontology_operations_agent_mode"):
            self.assertNotIn(name, specs)

    def test_launcher_separates_alert_recipient_from_run_as_and_teams(self):
        response = self.client.get("/api/pipeline-params")
        self.assertEqual(response.status_code, 200)
        specs = {spec["name"]: spec for spec in response.get_json()["parameters"]}
        self.assertEqual(specs["alert_email_to"]["default"], "")
        self.assertIn("independent", specs["alert_email_to"]["help"])
        pipeline = json.loads((SERVER.REPO_ROOT / "Orchestrator_Pipelines" /
                               "01_Pipe_Setup.DataPipeline" / "pipeline-content.json").read_text())
        for name in ("key_vault_uri", "ops_agent_teams_team_id", "ops_agent_teams_channel_id",
                     "ops_agent_run_as_user"):
            self.assertEqual(specs[name]["default"], "")
            self.assertEqual(pipeline["properties"]["parameters"][name]["defaultValue"], "")

    def test_pipeline_requires_destinations_before_start_and_clears_legacy_run_as_default(self):
        for name in ("ops_agent_teams_team_id", "ops_agent_teams_channel_id"):
            with self.subTest(name=name), patch.object(SERVER, "_start") as start:
                response = self.client.post("/api/run-pipeline", json={
                    "tenant": "tenant.example", "workspace": "Demo",
                    "parameters": {**PIPELINE_PARAMS, name: ""},
                })
                self.assertEqual(response.status_code, 400)
                self.assertIn("required for Operations Agent", response.get_json()["error"])
                start.assert_not_called()
        _, env = SERVER._pipeline_invocation({
            "tenant": "tenant.example", "workspace": "Demo", "parameters": PIPELINE_PARAMS,
        })
        self.assertEqual(json.loads(env["FABRIC_PIPELINE_PARAMS"])["ops_agent_run_as_user"], "")

    def test_parameter_defaults_are_target_scoped_and_allowlisted(self):
        config = {
            "FABRIC_TENANT": "tenant.example",
            "FABRIC_WORKSPACE": "Demo",
            "FABRIC_PIPELINE_PARAMS": json.dumps({
                **PIPELINE_PARAMS, "env_suffix": "V12", "per_notebook_timeout_secs": 7200,
                "workspace_id": "wrong-workspace", "pat": "must-not-be-returned",
            }),
        }
        with patch.dict(os.environ, config):
            for target, expected in (
                ("?tenant=TENANT.example&workspace=demo", "V12"),
                ("?tenant=other.example&workspace=Demo", "V6"),
                ("?tenant=tenant.example&workspace=Other", "V6"),
                ("", "V6"),
            ):
                with self.subTest(target=target):
                    response = self.client.get("/api/pipeline-params" + target)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    specs = {spec["name"]: spec for spec in response.get_json()["parameters"]}
                    self.assertEqual(specs["env_suffix"]["default"], expected)
                    self.assertNotIn("pat", specs)
                    self.assertNotIn("must-not-be-returned", response.get_data(as_text=True))
                    self.assertEqual(specs["workspace_id"]["default"], "")
                    if expected == "V12":
                        self.assertEqual(specs["per_notebook_timeout_secs"]["default"], "7200")
                        self.assertEqual(specs["key_vault_uri"]["source"], "server configuration")
        self.assertEqual(SERVER.PIPELINE_PARAM_SPEC[0]["default"], "V6")

    def test_parameter_defaults_report_invalid_configuration(self):
        for raw in ("{", "[]", '{"env_suffix":true}', '{"alert_email_to":{}}',
                    '{"per_notebook_timeout_secs":"not a number"}'):
            with self.subTest(raw=raw), patch.dict(os.environ, {
                "FABRIC_TENANT": "tenant.example", "FABRIC_WORKSPACE": "Demo",
                "FABRIC_PIPELINE_PARAMS": raw,
            }):
                response = self.client.get("/api/pipeline-params")
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.get_json()["error"])
        with patch.dict(os.environ, {
            "FABRIC_TENANT": "", "FABRIC_WORKSPACE": "", "FABRIC_PIPELINE_PARAMS": "{}",
        }):
            response = self.client.get("/api/pipeline-params")
            self.assertEqual(response.status_code, 400)
            self.assertIn("FABRIC_TENANT", response.get_json()["error"])

    def test_browser_parameter_storage_and_identity_suggestions(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        functions = (
            page[page.index("let pipelineSpec ="):page.index("function renderPipelineFields(")]
            + page[page.index("function updatePipelineDefaultsSummary("):page.index("async function loadPipelineParams(")]
        )
        script = """
const assert = require("node:assert/strict");
const fields = new Map();
const $ = id => fields.get(id);
for (const id of ["tenant", "workspace", "pp_alert_email_to", "wf_pp_alert_email_to",
  "pp_env_suffix", "wf_pp_env_suffix", "pipelineSettingsStatus", "workflowPipelineSettingsStatus"]) {
  fields.set(id, {value:"", textContent:""});
}
const stored = new Map();
const localStorage = {
  getItem:key => stored.get(key) ?? null,
  setItem:(key,value) => stored.set(key,value),
};
""" + functions + """
$("tenant").value = " tenant-A ";
$("workspace").value = " Demo ";
pipelineTarget = pipelineTargetKey();
pipelineSpec = [{name:"env_suffix"}, {name:"alert_email_to"}, {name:"workspace_id"}];
savePipelineField("env_suffix", "V12");
assert.equal($("wf_pp_env_suffix").value, "V12");
assert.equal(readPipelineSettings().env_suffix, "V12");
savePipelineField("workspace_id", "must-not-save");
assert.equal(readPipelineSettings().workspace_id, undefined);
pipelineIdentity = {userType:"user", tenantId:"TENANT-A", user:"admin@example.test"};
suggestPipelineEmail();
assert.equal($("pp_alert_email_to").value, "admin@example.test");
assert.equal($("wf_pp_alert_email_to").value, "admin@example.test");
savePipelineField("alert_email_to", "");
suggestPipelineEmail();
assert.equal($("pp_alert_email_to").value, "");
$("workspace").value = "Other";
savePipelineField("env_suffix", "must-not-leak");
pipelineTarget = pipelineTargetKey();
pipelineSavedValues = readPipelineSettings();
assert.deepEqual(pipelineSavedValues, {});
pipelineIdentity.tenantId = "different-tenant";
suggestPipelineEmail();
assert.equal($("pp_alert_email_to").value, "");
pipelineIdentity.tenantId = "tenant-a";
pipelineIdentity.userType = "servicePrincipal";
suggestPipelineEmail();
assert.equal($("pp_alert_email_to").value, "");
$("workspace").value = "DEMO";
pipelineTarget = pipelineTargetKey();
assert.equal(readPipelineSettings().env_suffix, "V12");
assert.equal(readPipelineSettings().alert_email_to, "");
localStorage.getItem = () => "{";
assert.deepEqual(readPipelineSettings(), {});
assert.match($("pipelineSettingsStatus").textContent, /Could not restore/);
localStorage.setItem = () => { throw new Error("blocked"); };
savePipelineField("env_suffix", "V13");
assert.match($("pipelineSettingsStatus").textContent, /Could not save.*blocked/);
localStorage.getItem = key => stored.get(key) ?? null;
localStorage.setItem = (key,value) => stored.set(key,value);
for (const prefix of ["deploy", "workflow"]) {
  for (const suffix of [...foundrySettingSuffixes, "FoundryCreate", "FoundryStatus"]) {
    fields.set(prefix + suffix, {value:"",checked:false,dispatchEvent(){}});
  }
}
fields.set("foundryClientId", {value:""});
fields.set("workflowDeployClientId", {value:""});
restoreFoundrySettings();
$("workflowFoundryModel").value="approved-model";
$("workflowFoundryCreate").checked=true;
saveFoundrySettings("workflow");
assert.equal($("deployFoundryModel").value, "approved-model");
assert.equal(JSON.parse(stored.get(foundryTarget)).FoundryCreate, undefined);
$("workspace").value="Other";
restoreFoundrySettings();
assert.equal($("workflowFoundryModel").value, "");
assert.equal($("workflowFoundryCreate").checked, false);
$("workspace").value="Demo";
restoreFoundrySettings();
assert.equal($("workflowFoundryModel").value, "approved-model");
assert.equal($("workflowFoundryCreate").checked, false);
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pipeline_rejects_blank_alert_recipient(self):
        with patch.object(SERVER, "_start") as start:
            response = self.client.post("/api/run-pipeline", json={
                "tenant": "tenant.example", "workspace": "DEV",
                "parameters": {
                    "key_vault_uri": "https://vault.vault.azure.net/",
                    "alert_email_to": " ",
                },
            })
        self.assertEqual(response.status_code, 400)
        self.assertIn("Alert email recipient is required", response.get_json()["error"])
        start.assert_not_called()

    def test_alert_recipient_uses_the_standard_styled_input(self):
        html = (Path(SERVER.__file__).with_name("static") / "index.html").read_text(encoding="utf-8")
        self.assertIn(
            '<input id="${prefix}${p.name}" type="text"',
            html,
        )
        self.assertIn('${requiredPipelineFields.includes(p.name) ? "required" : ""}', html)
        self.assertNotIn('type="${p.name === "alert_email_to" ? "email" : "text"}"', html)

    def test_pipeline_rejects_old_published_environment_before_starting(self):
        fabric = Mock()
        fabric.resolve_workspace_id.return_value = ("canonical-workspace", "DEV")
        fabric.request.side_effect = [
            Mock(status_code=200, json=Mock(return_value={"environment": {"runtimeVersion": "2.0"}})),
            Mock(status_code=200, json=Mock(return_value={"value": [{"id": "weather", "displayName": "Weather"}]})),
            Mock(status_code=200, json=Mock(return_value={"runtimeVersion": "1.3"})),
        ]
        with (
            patch.object(sys, "argv", ["run_pipeline.py", "--tenant", "tenant.example",
                                      "--workspace", "DEV", "--yes", "--no-wait"]),
            patch.object(run_pipeline, "Fabric", return_value=fabric),
            patch.object(run_pipeline, "ensure_key_vault_access") as key_vault,
            self.assertRaisesRegex(SystemExit, "Weather requires published Runtime 2.0"),
        ):
            run_pipeline.main()
        key_vault.assert_not_called()
        fabric.start_pipeline.assert_not_called()

    def test_pipeline_api_worker_and_poll_preserve_required_agent_failure_and_success(self):
        for state, energy_code, expected_status, expected_code in (
            ("Completed", 0, "succeeded", 0), ("Failed", 0, "failed", 1),
            ("Completed", 23, "failed", 23),
        ):
            with self.subTest(state=state, energy_code=energy_code):
                fabric = Mock()
                fabric.resolve_workspace_id.return_value = ("canonical-workspace", "DEV")
                fabric.request.side_effect = [
                    Mock(status_code=200, json=Mock(return_value={"environment": {"runtimeVersion": "2.0"}})),
                    Mock(status_code=200, json=Mock(return_value={"value": [{"id": "weather"}]})),
                    Mock(status_code=200, json=Mock(return_value={"runtimeVersion": "2.0"})),
                ]
                fabric.find_pipeline.return_value = ("pipeline-id", "01_Pipe_Setup")
                fabric.start_pipeline.return_value = "https://mock.invalid/job"
                reason = "Required Data Agent ontology runtime smoke failed: unsupported Ontology API"
                fabric.poll_job.return_value = {"status": state, "failureReason": {"message": reason}}
                started_scripts = []

                def child_process(command, **kwargs):
                    started_scripts.append(Path(command[2]).name)
                    if Path(command[2]).name == "deploy_fabric_app.py":
                        self.assertEqual(command[command.index("--stage") + 1], "energy")
                        return Mock(stdout=io.StringIO("Energy: setup\n"), returncode=energy_code)
                    output = io.StringIO()
                    with (
                        patch.object(sys, "argv", command[2:]),
                        patch.dict(os.environ, kwargs["env"]),
                        patch.object(run_pipeline, "Fabric", return_value=fabric),
                        patch.object(run_pipeline, "ensure_key_vault_access"),
                        contextlib.redirect_stdout(output), contextlib.redirect_stderr(output),
                    ):
                        returncode = run_pipeline.main()
                    return Mock(stdout=io.StringIO(output.getvalue()), returncode=returncode)

                def start_job(steps, *, phases):
                    job = SERVER.Job(phases)
                    SERVER.JOBS[job.id] = job
                    with patch.object(SERVER.subprocess, "Popen", side_effect=child_process), patch.object(SERVER.threading, "Timer"):
                        SERVER._workflow_worker(job, steps)
                    return job.id

                try:
                    with patch.object(SERVER, "_start_workflow", side_effect=start_job):
                        response = self.client.post("/api/run-pipeline", json={
                            "tenant": "tenant.example", "workspace": "DEV",
                            "parameters": {**PIPELINE_PARAMS, "key_vault_uri": "https://mock.vault.azure.net/",
                                           "alert_email_to": "operations@example.test",
                                           "ontology_data_agent_mode": "enabled",
                                           "ontology_operations_agent_mode": "enabled"},
                        })
                    self.assertEqual(response.status_code, 200)
                    job = self.client.get("/api/jobs/" + response.get_json()["jobId"]).get_json()
                    self.assertEqual(job["status"], expected_status)
                    self.assertEqual(job["returncode"], expected_code)
                    self.assertTrue(job["done"])
                    parameters = fabric.start_pipeline.call_args.args[2]
                    self.assertEqual(parameters["workspace_id"], "canonical-workspace")
                    self.assertNotIn("ontology_data_agent_mode", parameters)
                    self.assertNotIn("ontology_operations_agent_mode", parameters)
                    log = "\n".join(job["lines"])
                    if state == "Failed":
                        self.assertIn(reason, log)
                        self.assertNotIn("run COMPLETED", log)
                        self.assertEqual(started_scripts, ["run_pipeline.py"])
                    else:
                        self.assertIn("run COMPLETED", log)
                        self.assertEqual(started_scripts, ["run_pipeline.py", "deploy_fabric_app.py"])
                        if energy_code:
                            self.assertEqual(job["failedComponent"], "Energy setup")
                finally:
                    SERVER.JOBS.clear()

    def test_pipeline_does_not_forward_hidden_agent_mode_flags(self):
        with patch.object(SERVER, "_start_workflow", return_value="job-id") as start:
            response = self.client.post("/api/run-pipeline", json={
                "tenant": "tenant.example", "workspace": "DEV",
                "parameters": {
                    **PIPELINE_PARAMS,
                    "key_vault_uri": "https://vault.vault.azure.net/",
                    "alert_email_to": "operations@example.test",
                    "ontology_data_agent_mode": "force",
                    "ontology_operations_agent_mode": "disabled",
                },
            })
        self.assertEqual(response.status_code, 200)
        forwarded = json.loads(start.call_args.args[0][0]["env"]["FABRIC_PIPELINE_PARAMS"])
        self.assertNotIn("ontology_data_agent_mode", forwarded)
        self.assertNotIn("ontology_operations_agent_mode", forwarded)

    def test_background_jobs_are_exclusive_by_default(self):
        with patch.object(SERVER.threading, "Thread"):
            first_job_id = SERVER._start([], None, 1, ["Queued", "Done"], [])
            second_job_id = SERVER._start([], None, 1, ["Queued", "Done"], [])
        try:
            self.assertIsNotNone(first_job_id)
            self.assertIsNone(second_job_id)
        finally:
            SERVER.JOBS.clear()

    def test_frontend_is_never_cached(self):
        response = self.client.get("/")
        try:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        finally:
            response.close()

    def test_deploy_app_forwards_optional_spa_client_id(self):
        client_id = "11111111-1111-1111-1111-111111111111"
        with patch.object(SERVER, "_start", return_value="job-id") as start:
            response = self.client.post(
                "/api/deploy-app",
                json={
                    "tenant": "tenant.example",
                    "workspace": "Demo Workspace",
                    "clientId": client_id,
                },
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        argv = start.call_args.args[0]
        self.assertEqual(argv[-2:], ["--client-id", client_id])

    def test_deploy_app_omits_client_id_for_automatic_resolution(self):
        with patch.object(SERVER, "_start", return_value="job-id") as start:
            response = self.client.post(
                "/api/deploy-app",
                json={"tenant": "tenant.example", "workspace": "Demo Workspace"},
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertNotIn("--client-id", start.call_args.args[0])

    def test_deploy_progress_exposes_backend_sync_and_cors_readiness(self):
        self.assertIn("Syncing backend settings", SERVER.DEPLOY_PHASES)
        self.assertIn("Checking endpoint and CORS readiness", SERVER.DEPLOY_PHASES)

    def test_deploy_progress_shows_each_foundry_stage_in_the_order_the_deployer_prints_them(self):
        phases = SERVER.FOUNDRY_PHASES
        for name in ("Foundry project", "Foundry agents", "Hosted Agent"):
            self.assertIn(name, phases)
        indexes = [index for index, _markers in SERVER.FOUNDRY_MARKERS]
        self.assertEqual(indexes, sorted(set(indexes)))
        self.assertLess(max(indexes), len(phases) - 1)
        deployer_lines = [
            "[1/8] Checking Azure tenant", "[2/8] Resolving the tenant SPA", "[3/8] Resetting local Rayfin deployment state",
            "Foundry: project - resolving the Foundry project.", "Foundry: agents - provisioning the specialist prompt agents.",
            "Foundry: Hosted Agent - converging hydro-orchestrator.",
        ]
        job, seen = SERVER.Job(phases), []
        for line in deployer_lines:
            for index, markers in SERVER.FOUNDRY_MARKERS:
                if index > job.phase_index and any(marker in line for marker in markers):
                    job.phase_index = index
            seen.append(phases[job.phase_index])
        self.assertEqual(seen, [
            "Checking prerequisites", "Resolving SPA", "Preparing source/gateway", "Foundry project", "Foundry agents",
            "Hosted Agent",
        ])
        # The markers must be lines the deployer really prints, not an assumption.
        printed = (SERVER.SCRIPT_DIR / "provision_foundry_agents.py").read_text(encoding="utf-8")
        for marker in ("Foundry: project", "Foundry: agents", "Foundry: Hosted Agent"):
            self.assertIn(f'"{marker}', printed)

    def test_deploy_job_limit_covers_project_creation_build_and_publish(self):
        self.assertGreaterEqual(SERVER.FOUNDRY_TIMEOUT_S, 75 * 60)
        self.assertLess(SERVER.DEPLOY_TIMEOUT_S, SERVER.FOUNDRY_TIMEOUT_S)

    def test_deploy_environment_forwards_only_validated_foundry_settings(self):
        env = SERVER._deploy_environment({
            "foundryEndpoint": "https://res.services.ai.azure.com/api/projects/proj/",
            "foundryModel": "gpt-5.6-sol", "acrName": "myreg12345", "foundryLocation": "Sweden Central",
            "createFoundry": True, "productionWrites": "TRUE", "unexpected": "ignored",
        })
        self.assertEqual(env, {
            "HYDRO_FOUNDRY_PROJECT_ENDPOINT": "https://res.services.ai.azure.com/api/projects/proj/",
            "HYDRO_FOUNDRY_MODEL": "gpt-5.6-sol", "HYDRO_ACR_NAME": "myreg12345",
            "HYDRO_FOUNDRY_LOCATION": "Sweden Central", "HYDRO_FOUNDRY_CREATE": "true",
            "HYDRO_PRODUCTION_WRITES_ENABLED": "true",
        })
        self.assertEqual(SERVER._deploy_environment({}), {})
        self.assertEqual(SERVER._deploy_environment({
            "foundryEndpoint": "  ", "createFoundry": False, "productionWrites": "", "foundryModel": None,
        }), {"HYDRO_FOUNDRY_CREATE": "false"})
        with self.assertRaisesRegex(ValueError, "explicit boolean"):
            SERVER._deploy_environment({"createFoundry": "true"})

    def test_subscription_and_model_creation_inputs_are_forwarded_without_defaults(self):
        subscription = "11111111-2222-3333-4444-555555555555"
        settings = SERVER._deploy_environment({
            "azureSubscriptionId": subscription, "foundryModelName": "catalog-model",
            "foundryModelVersion": "available-version", "foundryModelSku": "AvailableSku",
            "foundryModelCapacity": 12, "createFoundry": False,
        })
        self.assertEqual(settings, {
            "HYDRO_AZURE_SUBSCRIPTION_ID": subscription, "HYDRO_FOUNDRY_MODEL_NAME": "catalog-model",
            "HYDRO_FOUNDRY_MODEL_VERSION": "available-version", "HYDRO_FOUNDRY_MODEL_SKU": "AvailableSku",
            "HYDRO_FOUNDRY_MODEL_CAPACITY": "12", "HYDRO_FOUNDRY_CREATE": "false",
        })

    def test_project_resource_id_is_explicit_validated_and_forwarded_only_to_setup(self):
        project_id = (
            "/subscriptions/11111111-2222-3333-4444-555555555555/resourceGroups/demo"
            "/providers/Microsoft.CognitiveServices/accounts/hydro/projects/demo"
        )
        self.assertEqual(SERVER._deploy_environment({"foundryProjectResourceId": project_id}), {
            "HYDRO_FOUNDRY_PROJECT_RESOURCE_ID": project_id,
        })
        for invalid in ("demo", project_id + "?other=true", project_id.replace("projects/demo", "accounts/demo")):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "ARM resource ID"):
                SERVER._deploy_environment({"foundryProjectResourceId": invalid})
        with patch.object(SERVER, "_start") as start:
            response = self.client.post("/api/deploy-app", json={
                "tenant": "tenant.example", "workspace": "Demo", "foundryProjectResourceId": project_id,
            })
            self.assertEqual(response.status_code, 400)
            start.assert_not_called()

    def test_checkout_metadata_prefills_source_and_guide_is_locally_available(self):
        values = ["100", "abcd1234", "2026-10-25T00:00:00+00:00", "feat/example",
                  "https://github.com/owner/repository.git"]
        with patch.object(SERVER.shutil, "which", return_value="git"), \
                patch.object(SERVER.Path, "exists", return_value=True), \
                patch.object(SERVER.subprocess, "run", side_effect=[Mock(stdout=value) for value in values]):
            data = self.client.get("/api/version").get_json()
        self.assertEqual((data["branch"], data["repository"]), ("feat/example", "owner/repository"))
        guide = self.client.get("/deploy-guide")
        self.assertEqual(guide.status_code, 200)
        self.assertIn("text/plain", guide.content_type)
        guide.close()

    def test_operator_graph_binding_is_passed_only_as_explicit_json(self):
        binding = {
            key: f"{index:08x}-1111-4111-8111-{index:012x}"
            for index, key in enumerate(("workspaceId", "ontologyId", "graphModelId"), start=1)
        }
        settings = SERVER._deploy_environment({"ontologyGraphBinding": json.dumps(binding)})
        self.assertEqual(json.loads(settings["HYDRO_ONTOLOGY_GRAPH_BINDING"]), binding)
        for invalid in ("not-json", "[]", "{}", '{"workspaceId":"wrong"}'):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "graph binding"):
                SERVER._deploy_environment({"ontologyGraphBinding": invalid})

    def test_deploy_environment_rejects_unsafe_or_malformed_values(self):
        bad = (
            {"foundryEndpoint": "http://res.services.ai.azure.com/api/projects/p"},
            {"foundryEndpoint": "https://evil.example/api/projects/p"},
            {"foundryEndpoint": "https://res.services.ai.azure.com/api/projects/p; calc"},
            {"foundryModel": "bad model"}, {"foundryModel": "x" * 80}, {"acrName": "UPPERCASE1"},
            {"acrName": "abc"}, {"foundryLocation": "sweden;central"}, {"productionWrites": "maybe"},
            {"azureSubscriptionId": "not-a-guid"}, {"foundryModelName": "bad model"},
            {"foundryModelVersion": "v;1"}, {"foundryModelSku": "SKU with spaces"},
            {"foundryModelCapacity": "0"}, {"foundryModelCapacity": "-1"},
            {"foundryModelCapacity": "1.5"}, {"foundryModelCapacity": True},
        )
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                SERVER._deploy_environment(payload)

    def test_deploy_app_passes_foundry_settings_to_the_job_environment(self):
        payload = {"tenant": "tenant.example", "workspace": "Demo Workspace", "createFoundry": True,
                   "foundryModel": "m1", "productionWrites": "false"}
        with patch.object(SERVER, "_start", return_value="job-id") as start:
            response = self.client.post("/api/setup-foundry", json=payload)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(start.call_args.args[1], {
            "HYDRO_FOUNDRY_MODEL": "m1", "HYDRO_FOUNDRY_CREATE": "true", "HYDRO_PRODUCTION_WRITES_ENABLED": "false",
        })
        self.assertNotIn("m1", " ".join(start.call_args.args[0]))  # settings never ride on the command line
        with patch.object(SERVER, "_start", return_value="job-id") as start:
            self.client.post("/api/setup-foundry", json={"tenant": "tenant.example", "workspace": "w"})
        self.assertIsNone(start.call_args.args[1])  # nothing set: the server's own environment still applies

    def test_invalid_foundry_settings_stop_the_request_before_any_job_starts(self):
        with patch.object(SERVER, "_start") as start, patch.object(SERVER, "_start_workflow") as workflow:
            deploy = self.client.post("/api/deploy-app", json={
                "tenant": "tenant.example", "workspace": "w", "acrName": "BAD"})
            full = self.client.post("/api/full-workflow", json={
                "tenant": "tenant.example", "workspace": "w", "repository": "o/r", "connectionId": "yes",
                "parameters": PIPELINE_PARAMS,
                "productionWrites": "maybe"})
        self.assertEqual((deploy.status_code, full.status_code), (400, 400))
        self.assertIn("Container registry", deploy.get_json()["error"])
        self.assertIn("Work-order writes", full.get_json()["error"])
        start.assert_not_called()
        workflow.assert_not_called()

    def test_full_workflow_gives_foundry_settings_to_the_deploy_step_only(self):
        payload = {
            "tenant": "tenant.example", "workspace": "w", "repository": "o/r", "connectionId": "yes", "pat": "x",
            "parameters": PIPELINE_PARAMS,
            "createFoundry": True, "foundryLocation": "swedencentral",
        }
        with patch.object(SERVER, "_start_workflow", return_value="id") as start:
            response = self.client.post("/api/full-workflow", json=payload)
        self.assertEqual(response.status_code, 200, response.get_json())
        steps = start.call_args.args[0]
        self.assertEqual(steps[3]["env"], {"HYDRO_FOUNDRY_CREATE": "true", "HYDRO_FOUNDRY_LOCATION": "swedencentral"})
        for step in steps[:3]:
            self.assertNotIn("HYDRO_FOUNDRY_CREATE", step["env"] or {})

    def test_both_deploy_forms_collect_foundry_settings_and_warn_about_cost(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        renderer = page[page.index("function renderFoundryFields("):page.index('$("workflowFoundryFields").innerHTML')]
        script = renderer + """
console.log(JSON.stringify([
  renderFoundryFields("deploy", "foundryClientId"),
  renderFoundryFields("workflow", "workflowDeployClientId"),
]));
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        forms = json.loads(result.stdout)
        for prefix, form in zip(("deploy", "workflow"), forms):
            for suffix in ("AzureSubscriptionId", "OntologyGraphBinding", "FoundryEndpoint", "FoundryModel", "AcrName", "ProductionWrites",
                           "FoundryCreate", "FoundryLocation", "FoundryModelName", "FoundryModelVersion",
                           "FoundryModelSku", "FoundryModelCapacity"):
                self.assertIn(f'id="{prefix}{suffix}"', form)
            self.assertIn(f'foundryPayload("{prefix}")', page)
            self.assertIn(f'id="{prefix}FoundryCreation" class="foundry-block" hidden', form)
            normal_fields = form[:form.index('<section')]
            self.assertEqual(normal_fields.count('type="text"'), 3)
            self.assertIn("Change automatic settings (optional)", form)
        self.assertIn("billable Azure resources", page)
        self.assertNotIn('placeholder="gpt-5.6-sol"', page)
        self.assertNotIn("Advanced / first-time", page)
        self.assertNotIn("Advanced: secret names", page)
        handler = page[page.index('$("deployBtn").addEventListener'):page.index('$("workflowBtn").addEventListener')]
        self.assertNotIn("workflow", handler)

    def test_foundry_creation_requires_complete_explicit_model_settings(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        start = page.index("function foundryValid(")
        function = page[start:page.index('for (const prefix of ["deploy", "workflow"])', start)]
        script = """
const assert = require("node:assert/strict");
const fields = {};
const $ = id => fields[id];
for (const suffix of ["FoundryCreate", "FoundryModel", "FoundryModelVersion", "FoundryModelSku",
                     "FoundryModelCapacity", "FoundryCreationStatus"]) fields["deploy"+suffix] = {value:"",checked:false};
""" + function + """
assert.equal(foundryValid("deploy"), true);
fields.deployFoundryCreate.checked = true;
assert.equal(foundryValid("deploy"), false);
assert.match(fields.deployFoundryCreationStatus.textContent, /enter the model/);
for (const suffix of ["FoundryModel", "FoundryModelVersion", "FoundryModelSku"]) fields["deploy"+suffix].value="approved";
for (const invalid of ["", "0", "-1", "1.5"]) {
  fields.deployFoundryModelCapacity.value=invalid;
  assert.equal(foundryValid("deploy"), false);
}
fields.deployFoundryModelCapacity.value="12";
assert.equal(foundryValid("deploy"), true);
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_full_workflow_progress_groups_detailed_phases(self):
        html = (MODULE_PATH.parent / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="workflow-phase-groups"', html)
        self.assertIn('title: "GitHub Sync"', html)
        self.assertIn('title: "Core Setup: Fabric and Weather"', html)
        self.assertIn('title: "Fabric App Deployment"', html)
        self.assertIn('["workflowPanel", "pipelinePanel"].includes(el.id)', html)
        self.assertIn('state === "active" || state === "failed"', html)

    def test_full_workflow_builds_existing_commands_in_serial_order(self):
        payload = {
            "tenant": "tenant.example",
            "workspace": "Demo Workspace",
            "repository": "owner/repository",
            "branch": "feature/demo",
            "directory": "/",
            "connectionId": "yes",
            "pat": "test-pat",
            "parameters": {
                **PIPELINE_PARAMS,
                "key_vault_uri": "https://vault.vault.azure.net/",
                "alert_email_to": "operations@example.test",
                "per_notebook_timeout_secs": "3600",
            },
            "clientId": "11111111-1111-1111-1111-111111111111",
        }
        with patch.object(SERVER, "_start_workflow", return_value="workflow-id") as start:
            response = self.client.post("/api/full-workflow", json=payload)

        self.assertEqual(response.status_code, 200, response.get_json())
        steps = start.call_args.args[0]
        self.assertEqual([step["name"] for step in steps], [
            "GitHub sync", "Setup pipeline", "Energy setup", "Foundry multi-agent setup", "Fabric app deploy",
        ])
        self.assertEqual(Path(steps[0]["argv"][0]).name, "sync_workspace_from_git.py")
        self.assertEqual(Path(steps[1]["argv"][0]).name, "run_pipeline.py")
        self.assertEqual(Path(steps[2]["argv"][0]).name, "deploy_fabric_app.py")
        self.assertNotIn("test-pat", " ".join(steps[0]["argv"]))
        self.assertEqual(steps[0]["env"]["FABRIC_GIT_PAT"], "test-pat")
        self.assertEqual(
            json.loads(steps[1]["env"]["FABRIC_PIPELINE_PARAMS"])["workspace_id"],
            "Demo Workspace",
        )
        self.assertEqual(steps[2]["argv"][-2:], [
            "--client-id", "11111111-1111-1111-1111-111111111111",
        ])

    def test_full_workflow_stops_at_failed_component_and_reports_it(self):
        job = SERVER.Job(SERVER.WORKFLOW_PHASES, exclusive=True)
        steps = [
            {"name": name, "argv": [name], "env": None, "timeout": 1,
             "markers": [], "phase_offset": index + 1}
            for index, name in enumerate(("GitHub sync", "Setup pipeline", "Fabric app deploy"))
        ]
        with patch.object(SERVER, "_run_workflow_step", side_effect=[0, 23]) as run:
            SERVER._workflow_worker(job, steps)

        self.assertEqual(job.status, "failed")
        self.assertEqual(job.returncode, 23)
        self.assertEqual(job.failed_component, "Setup pipeline")
        self.assertEqual(run.call_count, 2)
        self.assertIn("Setup pipeline failed with exit code 23", "\n".join(job.lines))
        self.assertNotIn("Fabric app deploy completed", "\n".join(job.lines))

    def test_full_workflow_success_reports_all_components(self):
        job = SERVER.Job(SERVER.WORKFLOW_PHASES, exclusive=True)
        steps = [
            {"name": name, "argv": [name], "env": None, "timeout": 1,
             "markers": [], "phase_offset": index + 1}
            for index, name in enumerate(("GitHub sync", "Setup pipeline", "Fabric app deploy"))
        ]
        with patch.object(SERVER, "_run_workflow_step", return_value=0) as run:
            SERVER._workflow_worker(job, steps)

        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.returncode, 0)
        self.assertEqual(job.phase_index, len(SERVER.WORKFLOW_PHASES) - 1)
        self.assertEqual(run.call_count, 3)
        log = "\n".join(job.lines)
        for name in ("GitHub sync", "Setup pipeline", "Fabric app deploy"):
            self.assertIn(f"{name} completed successfully", log)

    def test_real_workflow_step_returns_after_child_exits(self):
        job = SERVER.Job(["Queued", "Running", "Done"], exclusive=True)
        returncode = SERVER._run_workflow_step(
            job,
            "Test component",
            ["-c", "print('child completed')"],
            None,
            5,
            [],
            1,
        )

        self.assertEqual(returncode, 0)
        self.assertIsNone(job.process)
        self.assertIn("[Test component] child completed", job.lines)
        self.assertFalse(any("timed out" in line for line in job.lines))

    def test_full_workflow_monitor_exception_fails_instead_of_staying_running(self):
        job = SERVER.Job(SERVER.WORKFLOW_PHASES, exclusive=True)
        steps = [{
            "name": "GitHub sync",
            "argv": ["sync"],
            "env": None,
            "timeout": 1,
            "markers": [],
            "phase_offset": 1,
        }]
        with patch.object(
            SERVER,
            "_run_workflow_step",
            side_effect=NameError("monitor state is unavailable"),
        ):
            SERVER._workflow_worker(job, steps)

        self.assertEqual(job.status, "failed")
        self.assertEqual(job.returncode, -1)
        self.assertEqual(job.failed_component, "GitHub sync")
        self.assertIn(
            "GitHub sync monitor failed: NameError: monitor state is unavailable",
            "\n".join(job.lines),
        )

    def test_job_api_exposes_failed_workflow_component(self):
        job = SERVER.Job(SERVER.WORKFLOW_PHASES, exclusive=True)
        job.status = "failed"
        job.returncode = 9
        job.component = "Setup pipeline"
        job.failed_component = "Setup pipeline"
        SERVER.JOBS[job.id] = job
        try:
            response = self.client.get(f"/api/jobs/{job.id}")
            self.assertEqual(response.status_code, 200, response.get_json())
            body = response.get_json()
            self.assertEqual(body["component"], "Setup pipeline")
            self.assertEqual(body["failedComponent"], "Setup pipeline")
            self.assertTrue(body["done"])
        finally:
            SERVER.JOBS.clear()

    def test_version_git_commands_never_open_windows_consoles(self):
        completed = Mock(stdout="1\n")
        with (
            patch.object(SERVER.shutil, "which", return_value="git.exe"),
            patch.object(SERVER.subprocess, "run", return_value=completed) as run,
            patch.object(SERVER.Path, "exists", return_value=True),
        ):
            response = self.client.get("/api/version")

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(run.call_count, 5)
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["creationflags"], SERVER.NO_WINDOW)

    def test_full_workflow_validates_every_step_before_starting(self):
        base = {
            "tenant": "tenant.example",
            "workspace": "Demo Workspace",
            "repository": "owner/repository",
            "connectionId": "yes",
            "parameters": PIPELINE_PARAMS,
        }
        cases = (
            ({**base, "repository": ""}, "repository"),
            ({**base, "parameters": {**base["parameters"], "alert_email_to": ""}}, "Alert email"),
            ({**base, "clientId": "not-a-guid"}, "SPA client id"),
        )
        for payload, message in cases:
            with self.subTest(message=message), patch.object(SERVER, "_start_workflow") as start:
                response = self.client.post("/api/full-workflow", json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, response.get_json()["error"])
                start.assert_not_called()

    def test_full_workflow_page_explains_serial_failure_boundary(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="workflowBtn"', page)
        self.assertIn('"/api/full-workflow"', page)
        self.assertIn("Each step starts only after the previous step succeeds", page)
        self.assertIn("later changes", page)
        self.assertLess(page.index('id="tabWorkflow"'), page.index('id="deploymentActions"'))
        self.assertLess(page.index('id="tabWorkflow"'), page.index('id="tabDelete"'))

    def test_full_workflow_page_is_self_contained_except_for_target(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        workflow_pane = page[
            page.index('<section id="paneWorkflow"'):
            page.index('<section id="paneSync"')
        ]
        for field_id in (
            "workflowRepository",
            "workflowBranch",
            "workflowDirectory",
            "workflowReuseConn",
            "workflowKeepConnected",
            "workflowPat",
            "workflowTestBtn",
            "workflowPipelineFields",
            "workflowFoundryFields",
        ):
            self.assertIn(f'id="{field_id}"', workflow_pane)
        self.assertIn("Set your tenant and workspace on the left", workflow_pane)
        self.assertNotIn("Configure all required fields in the other tabs", workflow_pane)
        self.assertIn('renderPipelineFields("wf_pp_")', page)
        self.assertIn('renderFoundryFields("workflow", "workflowDeployClientId")', page)

    def test_full_workflow_submission_uses_only_its_own_fields(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        handler = page[
            page.index('$("workflowBtn").addEventListener'):
            page.index('$("restartBtn").addEventListener')
        ]
        for field_id in (
            "workflowRepository",
            "workflowBranch",
            "workflowDirectory",
            "workflowKeepConnected",
            "workflowPat",
            "workflowDeployClientId",
        ):
            self.assertIn(f'$("{field_id}")', handler)
        self.assertIn('workflowConnValue()', handler)
        self.assertIn('collectPipelineParams("wf_pp_")', handler)
        self.assertNotIn('$("repository")', handler)
        self.assertNotIn('$("deployClientId")', handler)

    def test_deploy_page_explains_portable_backend_readiness_contract(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("Workspace or capacity changes are detected automatically", page)
        self.assertIn("CORS preflights and POST probes", page)
        self.assertIn("/graphql", page)
        self.assertIn("/api/auth/v1/token", page)

    def test_cancel_all_jobs_terminates_only_running_jobs(self):
        running = SERVER.Job(["Queued", "Done"])
        finished = SERVER.Job(["Queued", "Done"])
        running.process = object()
        finished.status = "succeeded"
        SERVER.JOBS.update({running.id: running, finished.id: finished})
        try:
            with patch.object(SERVER, "_terminate_owned_process") as terminate:
                response = self.client.post("/api/jobs/cancel-all", json={})

            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(response.get_json()["cancelled"], 1)
            self.assertTrue(running.cancel_requested)
            self.assertFalse(finished.cancel_requested)
            terminate.assert_called_once_with(running.process)
        finally:
            SERVER.JOBS.clear()

    def test_cancel_all_jobs_handles_job_before_process_start(self):
        running = SERVER.Job(["Queued", "Done"])
        SERVER.JOBS[running.id] = running
        try:
            with patch.object(SERVER, "_terminate_owned_process") as terminate:
                response = self.client.post("/api/jobs/cancel-all", json={})

            self.assertEqual(response.get_json()["cancelled"], 1)
            self.assertTrue(running.cancel_requested)
            terminate.assert_not_called()
        finally:
            SERVER.JOBS.clear()


if __name__ == "__main__":
    unittest.main()
