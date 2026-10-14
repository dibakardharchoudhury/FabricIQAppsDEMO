import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hosted = load("deploy_hosted_agent")
provision = load("provision_foundry_agents")
DEPLOY = load("deploy_fabric_app")
project = load("deploy_foundry_project")

_STATE = tempfile.TemporaryDirectory()


def setUpModule():
    # Generated azd projects and the Foundry project folder live under the per-user state root; keep tests out of it.
    os.environ["HYDRO_STATE_DIR"] = _STATE.name


def tearDownModule():
    os.environ.pop("HYDRO_STATE_DIR", None)
    _STATE.cleanup()

GUIDS = {name: f"{index:08x}-0000-0000-0000-{index:012x}" for index, name in enumerate(
    ("tenant", "workspace", "ontology", "eventhouse", "database", "graphql", "appbackend", "spa"), start=1)}
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
ENDPOINT = "https://res.services.ai.azure.com/api/projects/proj"
PROJECT_ID = ("/subscriptions/sub-1/resourceGroups/rg1/providers/Microsoft.CognitiveServices/accounts/res/projects/proj")
VALUES = {
    "RAYFIN_PUBLIC_EVENTHOUSE_ID": GUIDS["eventhouse"], "RAYFIN_PUBLIC_KQL_DATABASE_ID": GUIDS["database"],
    "RAYFIN_PUBLIC_STID_GRAPHQL_ID": GUIDS["graphql"], "RAYFIN_PUBLIC_ITEM_ID": GUIDS["appbackend"],
    "RAYFIN_PUBLIC_API_URL": "https://" + "a" * 32 + ".pbidedicated.windows.net/x/",
    "RAYFIN_PUBLIC_PUBLISHABLE_KEY": "pk-test",
}
KUSTO = "https://kusto.example.fabric.microsoft.com"
CONNECTIONS = [{"name": "reg-conn", "properties": {"category": "ContainerRegistry", "target": "myreg.azurecr.io"}},
               {"name": "other", "properties": {"category": "AppInsights", "target": "/x"}}]


def configuration(writes=False):
    digest = provision.source_configuration_digest(GUIDS["tenant"], GUIDS["workspace"], GUIDS["ontology"], VALUES)
    return hosted.compose_runtime_configuration(
        tenant=GUIDS["tenant"], workspace=GUIDS["workspace"], ontology=GUIDS["ontology"], spa_client_id=GUIDS["spa"],
        kusto_origin=KUSTO + "/", project_endpoint=ENDPOINT + "/", values=VALUES, source_digest=digest,
        production_writes=writes,
    ), digest


def environment(writes=False, state="/home/session/hydro"):
    (orchestrator, fabric), _ = configuration(writes)
    return {
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": "model-x", "HYDRO_RUNTIME_STATE_DIR": state,
        "HYDRO_ORCHESTRATOR_CONFIG": json.dumps(orchestrator, separators=(",", ":")),
        "HYDRO_FABRIC_SOURCE_CONFIG": json.dumps(fabric, separators=(",", ":")),
    }


def agent(digest=DIGEST_A, env=None, status="active", kind="hosted", version=7):
    return {"versions": {"latest": {"status": status, "version": version, "definition": {
        "kind": kind, "container_configuration": {"image": f"myreg.azurecr.io/hydro-orchestrator@{digest}"},
        "environment_variables": environment() if env is None else env}}}}


class FakeDeploy:
    """Records every external command instead of running it."""
    REPO_ROOT = REPO_ROOT

    class DeployError(RuntimeError):
        pass

    def __init__(self, registry_tags=None, azd_environments=("hydro-orchestrator",), azd_signed_in=True,
                 extensions=(), azd_values=""):
        self.tags = dict(registry_tags or {})
        self.azd_environments = list(azd_environments)
        self.azd_signed_in = azd_signed_in
        self.extensions = list(extensions)
        self.azd_values = azd_values
        self.log = []
        self.cwds = []

    def az(self, *args):
        return ["az", *args]

    def command_argv(self, executable, *args):
        return [executable, *args]

    def run_capture(self, argv, cwd=None):
        self.log.append(argv)
        self.cwds.append((argv, cwd))
        if argv[:3] == ["az", "acr", "repository"]:
            tag = argv[argv.index("--image") + 1].split(":", 1)[1]
            if tag not in self.tags:
                raise self.DeployError("ERROR: The requested data does not exist.")
            return self.tags[tag]
        if argv[:4] == ["azd", "auth", "login", "--check-status"] and not self.azd_signed_in:
            raise self.DeployError("Not logged in")
        if argv[:3] == ["azd", "extension", "list"]:
            return json.dumps([{"id": item} for item in self.extensions])
        if argv[:3] == ["azd", "env", "get-values"]:
            return self.azd_values
        if argv[:3] == ["azd", "env", "list"]:
            return json.dumps([{"Name": name} for name in self.azd_environments])
        if argv[:3] == ["azd", "env", "new"]:
            self.azd_environments.append(argv[3])
        if argv[:3] == ["az", "account", "show"]:
            return "sub-1"
        if argv[:3] == ["az", "resource", "show"]:
            return "swedencentral"
        if argv[:3] == ["az", "acr", "show"]:
            return "/subscriptions/sub-1/resourceGroups/rg1/providers/Microsoft.ContainerRegistry/registries/myreg"
        return ""

    def run_stream(self, argv, cwd=None, env=None):
        self.log.append(argv)
        if argv[:3] == ["az", "acr", "build"]:
            self.tags[argv[argv.index("--image") + 1].split(":", 1)[1]] = DIGEST_B
        return ""

    def commands(self, *prefix):
        return [argv for argv in self.log if argv[:len(prefix)] == list(prefix)]


def run(fake, live, **overrides):
    """Run the convergence against a sequence of live agent states (the last one repeats)."""
    states = list(live)
    seen = []

    def get_agent():
        seen.append(1)
        return states.pop(0) if len(states) > 1 else states[0]

    arguments = dict(
        get_agent=get_agent, endpoint=ENDPOINT, project_id=PROJECT_ID, model="model-x", tenant=GUIDS["tenant"],
        workspace=GUIDS["workspace"], ontology=GUIDS["ontology"], spa_client_id=GUIDS["spa"], values=VALUES,
        source_digest=configuration()[1], kusto_origin=KUSTO, connections=CONNECTIONS, sleep=lambda _seconds: None,
    )
    arguments.update(overrides)
    return hosted.ensure_hosted_agent(fake, **arguments)


class PackagingTests(unittest.TestCase):
    def test_every_file_the_dockerfile_copies_is_fingerprinted(self):
        names = {path.relative_to(REPO_ROOT).as_posix() for path in hosted.packaged_sources(REPO_ROOT)}
        for expected in (
            "HydroOperationsService/Dockerfile", "HydroOperationsService/requirements.lock",
            "HydroOperationsService/src/hydro_orchestrator/foundry_supervisor.py",
            "HydroOperationsService/skills/work-order-review/SKILL.md",
            "HydroOperationsApp/scripts/local-fabric-sources.mjs",
            "HydroOperationsApp/src/services/copilot/orchestration.ts",
            "HydroOperationsApp/src/services/copilot/agentDefinitions.ts", "HydroOperationsApp/package-lock.json",
        ):
            self.assertIn(expected, names)
        self.assertNotIn("HydroOperationsApp/src/App.tsx", names)

    def test_fingerprint_tracks_packaged_content_only_and_ignores_line_endings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "HydroOperationsService").mkdir()
            (root / "HydroOperationsService/Dockerfile").write_text("FROM x\nCOPY a.txt b.txt ./dest/\n")
            (root / "a.txt").write_bytes(b"one\ntwo\n")
            (root / "b.txt").write_bytes(b"b")
            (root / "unrelated.txt").write_bytes(b"x")
            first = hosted.source_fingerprint(root)
            (root / "unrelated.txt").write_bytes(b"changed")
            self.assertEqual(hosted.source_fingerprint(root), first)
            (root / "a.txt").write_bytes(b"one\r\ntwo\r\n")
            self.assertEqual(hosted.source_fingerprint(root), first)
            (root / "b.txt").write_bytes(b"changed")
            self.assertNotEqual(hosted.source_fingerprint(root), first)
            (root / "b.txt").unlink()
            with self.assertRaisesRegex(RuntimeError, "does not exist"):
                hosted.packaged_sources(root)


class ConfigurationTests(unittest.TestCase):
    def test_runtime_configuration_matches_the_documented_contract(self):
        (orchestrator, fabric), digest = configuration()
        self.assertEqual(orchestrator["source"], {
            "tenant_id": GUIDS["tenant"], "workspace_id": GUIDS["workspace"], "ontology_id": GUIDS["ontology"],
            "generation": 2, "configuration_digest": digest,
        })
        self.assertEqual(orchestrator["native_binding"], {"source": orchestrator["source"],
                                                          "connections": {"data-agent": "hydro-fabric-iq"}})
        authorization = orchestrator["authorization"]
        self.assertEqual(authorization["audiences"]["kusto"], [KUSTO])
        self.assertEqual(authorization["scopes"]["kusto"], KUSTO + "/.default")
        self.assertEqual(authorization["scopes"]["fabric"], "https://api.fabric.microsoft.com/.default")
        self.assertEqual(authorization["scopes"]["foundry"], "https://ai.azure.com/.default")
        self.assertEqual(orchestrator["project_endpoint"], ENDPOINT)
        self.assertIs(orchestrator["production_writes_enabled"], False)
        self.assertEqual(set(fabric), {"tenant_id", "workspace_id", "ontology_id", "eventhouse_id", "database_id",
                                       "graphql_id", "appbackend_id", "api_url", "publishable_key"})
        self.assertIs(configuration(True)[0][0]["production_writes_enabled"], True)

    def test_the_hosted_config_passes_the_deployers_own_verification(self):
        (orchestrator, _fabric), digest = configuration()
        live = {"versions": {"latest": {"definition": {"environment_variables": {
            "HYDRO_ORCHESTRATOR_CONFIG": json.dumps(orchestrator)}}}}}
        provision.verify_hosted_source(live, GUIDS["tenant"], GUIDS["workspace"], GUIDS["ontology"], digest)


class DecisionTests(unittest.TestCase):
    def test_a_current_agent_needs_nothing(self):
        self.assertIsNone(hosted.needs_publish(agent(), image_digest=DIGEST_A, environment=environment()))

    def test_each_kind_of_drift_is_reported(self):
        expected = environment()
        cases = {
            "no hosted agent": (None, DIGEST_A),
            "not an active hosted agent": (agent(status="failed"), DIGEST_A),
            "image is not the one built": (agent(), DIGEST_B),
            "HYDRO_ORCHESTRATOR_CONFIG differs": (agent(env={**expected, "HYDRO_ORCHESTRATOR_CONFIG": "{}"}), DIGEST_A),
            "AZURE_AI_MODEL_DEPLOYMENT_NAME differs": (agent(env={**expected, "AZURE_AI_MODEL_DEPLOYMENT_NAME": "x"}), DIGEST_A),
            "HYDRO_FABRIC_SOURCE_CONFIG differs": (agent(env={**expected, "HYDRO_FABRIC_SOURCE_CONFIG": "not json"}), DIGEST_A),
        }
        for message, (live, digest) in cases.items():
            with self.subTest(message):
                self.assertRegex(hosted.needs_publish(live, image_digest=digest, environment=expected), message)

    def test_json_documents_compare_by_value_not_formatting(self):
        spaced = {**environment(), "HYDRO_ORCHESTRATOR_CONFIG": json.dumps(json.loads(environment()["HYDRO_ORCHESTRATOR_CONFIG"]), indent=2)}
        self.assertIsNone(hosted.needs_publish(agent(env=spaced), image_digest=DIGEST_A, environment=environment()))

    def test_registry_resolution_prefers_override_then_the_single_project_connection(self):
        self.assertEqual(hosted.registry_name(CONNECTIONS, "forced", {}), "forced")
        self.assertEqual(hosted.registry_name(CONNECTIONS, "", {}), "myreg")
        two = CONNECTIONS + [{"name": "b", "properties": {"category": "ContainerRegistry", "target": "other.azurecr.io"}}]
        self.assertEqual(hosted.registry_name(two, "", {"AZURE_CONTAINER_REGISTRY_NAME": "fromazd"}), "fromazd")
        with self.assertRaisesRegex(RuntimeError, "HYDRO_ACR_NAME"):
            hosted.registry_name(two, "", {})

    def test_bootstrap_variables_are_derived_from_the_live_project(self):
        values = hosted.azd_bootstrap_values(
            project_id=PROJECT_ID, endpoint=ENDPOINT, tenant="t", location="swedencentral", registry="myreg",
            registry_id="rid", connection="reg-conn")
        self.assertEqual(values["AZURE_SUBSCRIPTION_ID"], "sub-1")
        self.assertEqual(values["AZURE_AI_ACCOUNT_NAME"], "res")
        self.assertEqual(values["AZURE_CONTAINER_REGISTRY_ENDPOINT"], "myreg.azurecr.io")
        self.assertEqual(values["AZURE_AI_PROJECT_ACR_CONNECTION_NAME"], "reg-conn")
        with self.assertRaises(RuntimeError):
            hosted.azd_bootstrap_values(project_id="/bad", endpoint="e", tenant="t", location="l", registry="r",
                                        registry_id="i", connection="c")


class ConvergenceTests(unittest.TestCase):
    def tag(self):
        return hosted.image_tag(REPO_ROOT)

    def test_a_current_agent_costs_one_registry_lookup_and_changes_nothing(self):
        fake = FakeDeploy(registry_tags={self.tag(): DIGEST_A})
        self.assertEqual(run(fake, [agent()]), agent())
        self.assertEqual(fake.commands("az", "acr", "build"), [])
        self.assertEqual(fake.commands("azd"), [])

    def test_changed_source_builds_sets_the_environment_deploys_and_waits_for_activation(self):
        fake = FakeDeploy()
        result = run(fake, [agent(DIGEST_A, version=7), agent(DIGEST_A, version=7), agent(DIGEST_B, version=8)])
        self.assertEqual(hosted.hosted_state(result)[1:3], ("8", DIGEST_B))
        build = fake.commands("az", "acr", "build")[0]
        self.assertIn(f"hydro-orchestrator:{self.tag()}", build)
        self.assertEqual(build[build.index("--platform") + 1], "linux/amd64")
        self.assertEqual(build[build.index("--file") + 1], "HydroOperationsService/Dockerfile")
        sets = {argv[3]: argv[4] for argv in fake.commands("azd", "env", "set")}
        self.assertEqual(sets["HYDRO_ORCHESTRATOR_IMAGE"], f"myreg.azurecr.io/hydro-orchestrator@{DIGEST_B}")
        self.assertEqual(json.loads(sets["HYDRO_ORCHESTRATOR_CONFIG"])["production_writes_enabled"], False)
        self.assertEqual(sets["AZURE_AI_MODEL_DEPLOYMENT_NAME"], "model-x")
        deploy_calls = fake.commands("azd", "deploy")
        self.assertEqual(deploy_calls, [["azd", "deploy", "hydro-orchestrator", "--environment", "hydro-orchestrator", "--no-prompt"]])
        order = [argv[:3] for argv in fake.log if argv[:3] in (["az", "acr", "build"], ["azd", "env", "set"], ["azd", "deploy", "hydro-orchestrator"])]
        self.assertLess(order.index(["az", "acr", "build"]), order.index(["azd", "env", "set"]))
        self.assertLess(order.index(["azd", "env", "set"]), order.index(["azd", "deploy", "hydro-orchestrator"]))

    def test_a_built_image_is_reused_and_only_the_configuration_is_republished(self):
        fake = FakeDeploy(registry_tags={self.tag(): DIGEST_A})
        stale = agent(DIGEST_A, env={**environment(), "HYDRO_FABRIC_SOURCE_CONFIG": "{}"})
        run(fake, [stale, stale, agent()])
        self.assertEqual(fake.commands("az", "acr", "build"), [])
        self.assertEqual(len(fake.commands("azd", "deploy")), 1)

    def test_production_writes_are_preserved_from_the_live_agent_unless_overridden(self):
        fake = FakeDeploy()
        live = agent(DIGEST_A, env=environment(writes=True))
        with patch.dict("os.environ", {"HYDRO_PRODUCTION_WRITES_ENABLED": ""}):
            run(fake, [live, live, agent(DIGEST_B, env=environment(writes=True))])
        sets = {argv[3]: argv[4] for argv in fake.commands("azd", "env", "set")}
        self.assertIs(json.loads(sets["HYDRO_ORCHESTRATOR_CONFIG"])["production_writes_enabled"], True)
        fake = FakeDeploy()
        with patch.dict("os.environ", {"HYDRO_PRODUCTION_WRITES_ENABLED": "false"}):
            run(fake, [live, live, agent(DIGEST_B)])
        sets = {argv[3]: argv[4] for argv in fake.commands("azd", "env", "set")}
        self.assertIs(json.loads(sets["HYDRO_ORCHESTRATOR_CONFIG"])["production_writes_enabled"], False)
        with patch.dict("os.environ", {"HYDRO_PRODUCTION_WRITES_ENABLED": "maybe"}):
            with self.assertRaisesRegex(FakeDeploy.DeployError, "true or false"):
                run(FakeDeploy(), [live])

    def test_a_missing_agent_on_a_new_machine_creates_the_azd_environment_first(self):
        fake = FakeDeploy(azd_environments=(), azd_signed_in=False)
        run(fake, [None, None, agent(DIGEST_B, version=1)])
        self.assertEqual(len(fake.commands("azd", "auth", "login", "--tenant-id")), 1)
        created = fake.commands("azd", "env", "new")[0]
        self.assertEqual(created[3], "hydro-orchestrator")
        self.assertEqual(created[created.index("--location") + 1], "swedencentral")
        order = [argv[:3] for argv in fake.log if argv[:3] in (["azd", "env", "new"], ["azd", "env", "set"], ["azd", "deploy", "hydro-orchestrator"])]
        self.assertEqual(order[0], ["azd", "env", "new"])
        self.assertEqual(order[-1], ["azd", "deploy", "hydro-orchestrator"])
        names = {argv[3] for argv in fake.commands("azd", "env", "set")}
        self.assertTrue({"AZURE_AI_PROJECT_ENDPOINT", "AZURE_CONTAINER_REGISTRY_NAME", "HYDRO_ORCHESTRATOR_IMAGE"} <= names)

    def test_a_version_that_never_becomes_current_fails_with_the_reason(self):
        fake = FakeDeploy()
        clock = iter([0, 0, 10_000, 20_000])
        with patch.object(hosted.time, "monotonic", side_effect=lambda: next(clock)):
            with self.assertRaisesRegex(FakeDeploy.DeployError, "did not become active.*image"):
                run(fake, [agent(DIGEST_A)])

    def test_registry_errors_other_than_a_missing_tag_propagate(self):
        class Denied(FakeDeploy):
            def run_capture(self, argv, cwd=None):
                if argv[:3] == ["az", "acr", "repository"]:
                    raise self.DeployError("AuthorizationFailed: caller lacks permission")
                return super().run_capture(argv, cwd)
        with self.assertRaisesRegex(FakeDeploy.DeployError, "AuthorizationFailed"):
            run(Denied(), [agent()])


class ProjectRoutingTests(unittest.TestCase):
    """Which azd project a publish runs from, and azd readiness."""

    def test_the_pinned_project_publishes_from_the_repository_itself(self):
        pinned = hosted.PINNED_ENDPOINT_RE.search((REPO_ROOT / "azure.yaml").read_text(encoding="utf-8"))["value"]
        self.assertEqual(hosted.azd_project_dir(FakeDeploy(), pinned + "/"), REPO_ROOT)

    def test_another_project_gets_a_generated_untracked_azd_project_never_a_repo_edit(self):
        before = (REPO_ROOT / "azure.yaml").read_text(encoding="utf-8")
        directory = hosted.azd_project_dir(FakeDeploy(), ENDPOINT)
        self.assertNotEqual(directory, REPO_ROOT)
        self.assertTrue(str(directory).startswith(os.environ["HYDRO_STATE_DIR"]))
        generated = (directory / "azure.yaml").read_text(encoding="utf-8")
        self.assertIn(f"endpoint: {ENDPOINT}", generated)
        self.assertEqual(generated.replace(ENDPOINT, "X"), hosted.PINNED_ENDPOINT_RE.sub(
            lambda match: match["head"] + "X", before, count=1))
        self.assertEqual((REPO_ROOT / "azure.yaml").read_text(encoding="utf-8"), before)
        self.assertEqual(hosted.azd_project_dir(FakeDeploy(), ENDPOINT), directory)

    def test_publishing_to_another_project_runs_every_azd_command_in_its_generated_project(self):
        fake = FakeDeploy()
        run(fake, [agent(DIGEST_A, version=7), agent(DIGEST_A, version=7), agent(DIGEST_B, version=8)])
        azd_cwds = {cwd for argv, cwd in fake.cwds if argv[0] == "azd" and argv[1] in ("env",)}
        self.assertTrue(azd_cwds)
        self.assertTrue(all(cwd != REPO_ROOT and str(cwd).startswith(os.environ["HYDRO_STATE_DIR"]) for cwd in azd_cwds))

    def test_missing_azd_extensions_are_installed_and_present_ones_are_not(self):
        fake = FakeDeploy(extensions=["azure.ai.agents"])
        hosted.ensure_azd_ready(fake, "tenant")
        self.assertEqual(fake.commands("azd", "extension", "install"), [["azd", "extension", "install", "microsoft.foundry"]])
        both = FakeDeploy(extensions=list(hosted.REQUIRED_EXTENSIONS))
        hosted.ensure_azd_ready(both, "tenant")
        self.assertEqual(both.commands("azd", "extension", "install"), [])
        self.assertEqual(set(hosted.REQUIRED_EXTENSIONS), {"azure.ai.agents", "microsoft.foundry"})


class ProjectCreationTests(unittest.TestCase):
    ENDPOINT = "https://new-res.services.ai.azure.com/api/projects/new-proj"

    def create(self, fake, **overrides):
        arguments = dict(tenant="tenant.example", workspace="ws", location="swedencentral", model="model-x",
                         check_access=lambda _endpoint: True, sleep=lambda _seconds: None)
        arguments.update(overrides)
        return project.ensure_foundry_project(fake, hosted, **arguments)

    def fake(self, endpoint=None):
        values = f'AZURE_AI_PROJECT_ENDPOINT="{self.ENDPOINT if endpoint is None else endpoint}"\n'
        return FakeDeploy(extensions=list(hosted.REQUIRED_EXTENSIONS), azd_values=values)

    def test_creation_is_opt_in(self):
        with patch.dict(os.environ, {"HYDRO_FOUNDRY_CREATE": ""}):
            self.assertFalse(project.enabled())
        with patch.dict(os.environ, {"HYDRO_FOUNDRY_CREATE": "true"}):
            self.assertTrue(project.enabled())
        with patch.dict(os.environ, {"HYDRO_FOUNDRY_CREATE": "yes"}):
            self.assertFalse(project.enabled())

    def test_deployments_json_survives_the_templates_quoted_parameter_file(self):
        parameter = project.deployments_parameter("model-x")
        # The template embeds the value inside quotes in a JSON parameters file.
        embedded = json.loads('{"value": "' + parameter + '"}')["value"]
        self.assertEqual(json.loads(embedded), [{
            "name": "model-x", "model": {"name": "model-x", "format": "OpenAI", "version": "2026-07-09"},
            "sku": {"name": "GlobalStandard", "capacity": 500},
        }])
        with patch.dict(os.environ, {"HYDRO_FOUNDRY_MODEL_VERSION": "v2", "HYDRO_FOUNDRY_MODEL_SKU": "Standard",
                                      "HYDRO_FOUNDRY_MODEL_CAPACITY": "50"}):
            again = json.loads(json.loads('{"value": "' + project.deployments_parameter("m") + '"}')["value"])[0]
        self.assertEqual((again["model"]["version"], again["sku"]), ("v2", {"name": "Standard", "capacity": 50}))
        with patch.dict(os.environ, {"HYDRO_FOUNDRY_MODEL_CAPACITY": "lots"}):
            with self.assertRaisesRegex(ValueError, "whole number"):
                project.deployments_parameter("m")

    def test_the_parameter_survives_windows_command_line_quoting_into_a_child_process(self):
        import subprocess, sys
        parameter = project.deployments_parameter("model-x")
        out = subprocess.run([sys.executable, "-c", "import sys; print(sys.argv[1], end='')", parameter],
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(out, parameter)

    def test_names_and_locations_are_stable_and_normalized(self):
        self.assertEqual(project.normalize_location("Sweden Central"), "swedencentral")
        self.assertEqual(project.environment_name("T", "W"), project.environment_name("t", "w"))
        self.assertNotEqual(project.environment_name("t", "w1"), project.environment_name("t", "w2"))
        with patch.dict(os.environ, {"HYDRO_FOUNDRY_PROJECT_NAME": "my-name"}):
            self.assertEqual(project.environment_name("t", "w"), "my-name")
        with patch.dict(os.environ, {"HYDRO_FOUNDRY_PROJECT_NAME": "bad name!"}):
            with self.assertRaises(ValueError):
                project.environment_name("t", "w")

    def test_a_new_project_is_initialized_configured_provisioned_and_read_back_in_order(self):
        fake = self.fake()
        self.assertEqual(self.create(fake), self.ENDPOINT)
        init = fake.commands("azd", "init")[0]
        self.assertEqual(init[init.index("-t") + 1], project.TEMPLATE)
        name = init[init.index("-e") + 1]
        sets = {argv[3]: argv[4] for argv in fake.commands("azd", "env", "set")}
        self.assertEqual({key: sets[key] for key in (
            "AZURE_SUBSCRIPTION_ID", "AZURE_LOCATION", "AZURE_AI_DEPLOYMENTS_LOCATION", "ENABLE_HOSTED_AGENTS",
            "ENABLE_CAPABILITY_HOST", "ENABLE_MONITORING")}, {
            "AZURE_SUBSCRIPTION_ID": "sub-1", "AZURE_LOCATION": "swedencentral",
            "AZURE_AI_DEPLOYMENTS_LOCATION": "swedencentral", "ENABLE_HOSTED_AGENTS": "true",
            "ENABLE_CAPABILITY_HOST": "false", "ENABLE_MONITORING": "true"})
        self.assertEqual(sets["AI_PROJECT_DEPLOYMENTS"], project.deployments_parameter("model-x"))
        self.assertEqual(fake.commands("azd", "provision"), [["azd", "provision", "--environment", name, "--no-prompt"]])
        order = [argv[:3] for argv in fake.log if argv[:2] == ["azd", "init"] or argv[:3] in (
            ["azd", "env", "set"], ["azd", "provision", "--environment"], ["azd", "env", "get-values"])]
        self.assertEqual(order[0][:2], ["azd", "init"])
        self.assertEqual(order[-1], ["azd", "env", "get-values"])
        self.assertLess(order.index(["azd", "provision", "--environment"]), order.index(["azd", "env", "get-values"]))

    def test_an_already_initialized_folder_is_reused_not_reinitialized(self):
        fake = self.fake()
        folder = hosted.state_root() / "foundry-project" / project.environment_name("tenant.example", "ws")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "azure.yaml").write_text("name: x\n")
        self.create(fake)
        self.assertEqual(fake.commands("azd", "init"), [])
        self.assertEqual(len(fake.commands("azd", "provision")), 1)

    def test_it_waits_for_role_assignments_to_take_effect(self):
        answers, sleeps = iter([False, False, True]), []
        self.assertEqual(self.create(self.fake(), check_access=lambda _e: next(answers), sleep=sleeps.append), self.ENDPOINT)
        self.assertEqual(sleeps, [project.ACCESS_POLL_S] * 2)

    def test_a_project_that_never_becomes_usable_fails_with_the_endpoint(self):
        clock = iter([0, 0, 10_000, 20_000])
        with patch.object(project.time, "monotonic", side_effect=lambda: next(clock)):
            with self.assertRaisesRegex(FakeDeploy.DeployError, "new-proj.*not yet usable"):
                self.create(self.fake(), check_access=lambda _e: False)

    def test_an_invalid_endpoint_from_provisioning_is_rejected(self):
        for bad in ("http://x.services.ai.azure.com/api/projects/p", "https://evil.example/api/projects/p", ""):
            with self.subTest(bad):
                with self.assertRaisesRegex(FakeDeploy.DeployError, "no valid Foundry project endpoint"):
                    self.create(self.fake(bad))


class BackendBootstrapTests(unittest.TestCase):
    KEYS = DEPLOY.APPBACKEND_IDENTITY_KEYS

    def values(self, present):
        return {key: "x" for key in self.KEYS[:present]}, None

    def test_an_existing_target_is_left_alone(self):
        with patch.object(DEPLOY, "current_rayfin_target", return_value=({key: "x" for key in self.KEYS}, None)), \
             patch.object(DEPLOY, "run_rayfin_deployment") as up:
            DEPLOY.ensure_appbackend_identity("tenant", "workspace")
        up.assert_not_called()

    def test_a_fresh_target_gets_a_backend_without_static_hosting_before_foundry_provisioning(self):
        states = [self.values(0), self.values(3)]
        with patch.object(DEPLOY, "current_rayfin_target", side_effect=states), \
             patch.object(DEPLOY, "ensure_rayfin_login") as login, \
             patch.object(DEPLOY, "rayfin24", side_effect=lambda *a: ["rayfin", *a]), \
             patch.object(DEPLOY, "run_rayfin_deployment") as up:
            DEPLOY.ensure_appbackend_identity("tenant", "workspace")
        login.assert_called_once_with("tenant")
        up.assert_called_once_with(
            ["rayfin", "up", "--workspace-id", "workspace", "--exclude-services", "staticHosting", "--yes"], "tenant")

    def test_a_backend_that_records_no_identity_stops_the_deployment(self):
        with patch.object(DEPLOY, "current_rayfin_target", return_value=self.values(1)), \
             patch.object(DEPLOY, "ensure_rayfin_login"), patch.object(DEPLOY, "rayfin24", return_value=["r"]), \
             patch.object(DEPLOY, "run_rayfin_deployment"):
            with self.assertRaisesRegex(DEPLOY.DeployError, "did not record its identity"):
                DEPLOY.ensure_appbackend_identity("tenant", "workspace")

    def test_the_deployer_runs_it_immediately_before_foundry_provisioning(self):
        import inspect
        source = inspect.getsource(DEPLOY.deploy)
        self.assertLess(source.index("ensure_appbackend_identity("), source.index("provision_foundry_agents("))


if __name__ == "__main__":
    unittest.main()
