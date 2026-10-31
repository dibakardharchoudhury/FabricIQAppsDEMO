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
    ("tenant", "workspace", "ontology", "eventhouse", "database", "graphql", "appbackend", "spa", "operational"), start=1)}
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
ENDPOINT = "https://res.services.ai.azure.com/api/projects/proj"
PROJECT_ID = ("/subscriptions/sub-1/resourceGroups/rg1/providers/Microsoft.CognitiveServices/accounts/res/projects/proj")
VALUES = {
    "RAYFIN_PUBLIC_EVENTHOUSE_ID": GUIDS["eventhouse"], "RAYFIN_PUBLIC_KQL_DATABASE_ID": GUIDS["database"],
    "RAYFIN_PUBLIC_LAKEHOUSE_ID": GUIDS["graphql"], "RAYFIN_PUBLIC_ITEM_ID": GUIDS["appbackend"],
    "RAYFIN_PUBLIC_OPERATIONAL_SQL_DATABASE_ID": GUIDS["operational"],
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


def environment(writes=True, state="/home/session/hydro"):
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
            self.build_cwd = cwd
            self.build_context = Path(argv[-1])
            self.build_context_files = (
                {path.relative_to(self.build_context).as_posix(): path.read_bytes()
                 for path in self.build_context.rglob("*") if path.is_file()}
                if self.build_context != self.REPO_ROOT else None
            )
            self.tags[argv[argv.index("--image") + 1].split(":", 1)[1]] = DIGEST_B
        return ""

    def commands(self, *prefix):
        return [argv for argv in self.log if argv[:len(prefix)] == list(prefix)]

    def environment_settings(self):
        return dict(
            assignment.split("=", 1)
            for argv in self.commands("azd", "env", "set")
            for assignment in argv[3:argv.index("--environment")]
        )


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
    def test_prepared_image_is_built_once_and_publication_still_reads_its_digest(self):
        fake = FakeDeploy()
        tag, digest = hosted.ensure_hosted_image(fake, registry="myreg", subscription="sub-1")
        self.assertEqual(digest, DIGEST_B)
        self.assertEqual(fake.tags[tag], digest)
        self.assertFalse(fake.commands("azd", "deploy"))
        with patch.dict(os.environ, {"HYDRO_PRODUCTION_WRITES_ENABLED": "true"}):
            run(fake, [agent(DIGEST_B)])
        self.assertEqual(len(fake.commands("az", "acr", "build")), 1)
        self.assertGreaterEqual(len(fake.commands("az", "acr", "repository")), 3)
        self.assertFalse(fake.commands("azd", "deploy"))

    def test_every_file_the_dockerfile_copies_is_fingerprinted(self):
        names = {path.relative_to(REPO_ROOT).as_posix() for path in hosted.packaged_sources(REPO_ROOT)}
        for expected in (
            "HydroOperationsService/Dockerfile", "HydroOperationsService/requirements.lock",
            "HydroOperationsService/src/hydro_orchestrator/foundry_supervisor.py",
            "HydroOperationsService/skills/work-order-review/SKILL.md",
            "HydroOperationsApp/scripts/local-fabric-sources.mjs",
            "HydroOperationsApp/src/services/operationalConnector.ts",
            "HydroOperationsApp/src/services/hostedSourceAccess.ts",
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
    def test_shared_project_uses_separate_runtime_configuration_names_and_azd_state(self):
        directories = set()
        for workspace in (GUIDS["workspace"], GUIDS["ontology"]):
            suffix = provision.foundry_resource_suffix(workspace)
            configuration_value, _sources = hosted.compose_runtime_configuration(
                tenant=GUIDS["tenant"], workspace=workspace, ontology=GUIDS["ontology"],
                spa_client_id=GUIDS["spa"], kusto_origin=KUSTO, project_endpoint=ENDPOINT,
                values=VALUES, source_digest=configuration()[1],
                production_writes=False, resource_suffix=suffix,
            )
            binding = configuration_value["native_binding"]
            self.assertEqual(binding["agent_name_suffix"], suffix)
            self.assertEqual(binding["connections"]["data-agent"], "hydro-fabric-iq" + suffix)
            name = hosted.AGENT_NAME + suffix
            directory = hosted.azd_project_dir(FakeDeploy(), ENDPOINT, agent_name=name)
            yaml = (directory / "azure.yaml").read_text(encoding="utf-8")
            self.assertIn("    " + name + ":", yaml)
            self.assertIn("        name: " + name, yaml)
            self.assertEqual(hosted.azd_project_dir(FakeDeploy(), ENDPOINT, agent_name=name), directory)
            directories.add(directory)
        self.assertEqual(len(directories), 2)
        with self.assertRaises(ValueError):
            hosted.compose_runtime_configuration(
                tenant=GUIDS["tenant"], workspace=GUIDS["workspace"], ontology=GUIDS["ontology"],
                spa_client_id=GUIDS["spa"], kusto_origin=KUSTO, project_endpoint=ENDPOINT,
                values=VALUES, source_digest=configuration()[1],
                production_writes=False, resource_suffix="-" + "0" * 32,
            )

    def test_runtime_configuration_matches_the_documented_contract(self):
        (orchestrator, fabric), digest = configuration()
        self.assertEqual(orchestrator["source"], {
            "tenant_id": GUIDS["tenant"], "workspace_id": GUIDS["workspace"], "ontology_id": GUIDS["ontology"],
            "generation": 2, "configuration_digest": digest,
        })
        self.assertEqual(orchestrator["native_binding"], {"source": orchestrator["source"],
                                                          "connections": {"data-agent": "hydro-fabric-iq"},
                                                          "toolbox": "hydro-fabric-iq-tools"})
        authorization = orchestrator["authorization"]
        self.assertEqual(authorization["audiences"]["kusto"], [KUSTO])
        self.assertEqual(authorization["scopes"]["kusto"], KUSTO + "/.default")
        self.assertEqual(authorization["scopes"]["fabric"], "https://api.fabric.microsoft.com/.default")
        self.assertEqual(authorization["scopes"]["foundry"], "https://ai.azure.com/.default")
        self.assertEqual(orchestrator["project_endpoint"], ENDPOINT)
        self.assertIs(orchestrator["production_writes_enabled"], False)
        self.assertEqual(set(fabric), {"tenant_id", "workspace_id", "ontology_id", "eventhouse_id", "database_id",
                                       "lakehouse_id", "appbackend_id", "operational_sql_database_id", "api_url", "publishable_key"})
        self.assertIs(configuration(True)[0][0]["production_writes_enabled"], True)

    def test_the_hosted_config_passes_the_deployers_own_verification(self):
        (orchestrator, _fabric), digest = configuration()
        live = {"versions": {"latest": {"definition": {"environment_variables": {
            "HYDRO_ORCHESTRATOR_CONFIG": json.dumps(orchestrator)}}}}}
        provision.verify_hosted_source(live, GUIDS["tenant"], GUIDS["workspace"], GUIDS["ontology"], digest)

    def test_a_second_tenant_workspace_has_no_first_target_ids_or_endpoints(self):
        ids = {name: f"{index + 30:08x}-1111-4111-8111-{index + 30:012x}"
               for index, name in enumerate(GUIDS)}
        values = {key: value for key, value in VALUES.items()}
        for key, name in (
            ("RAYFIN_PUBLIC_EVENTHOUSE_ID", "eventhouse"), ("RAYFIN_PUBLIC_KQL_DATABASE_ID", "database"),
            ("RAYFIN_PUBLIC_LAKEHOUSE_ID", "graphql"), ("RAYFIN_PUBLIC_ITEM_ID", "appbackend"),
            ("RAYFIN_PUBLIC_OPERATIONAL_SQL_DATABASE_ID", "operational"),
        ):
            values[key] = ids[name]
        values["RAYFIN_PUBLIC_API_URL"] = "https://" + "b" * 32 + ".pbidedicated.windows.net/other/"
        values["RAYFIN_PUBLIC_PUBLISHABLE_KEY"] = "pk-other-target"
        digest = provision.source_configuration_digest(ids["tenant"], ids["workspace"], ids["ontology"], values)
        other, sources = hosted.compose_runtime_configuration(
            tenant=ids["tenant"], workspace=ids["workspace"], ontology=ids["ontology"], spa_client_id=ids["spa"],
            kusto_origin="https://other.kusto.fabric.microsoft.com",
            project_endpoint="https://other.services.ai.azure.com/api/projects/other",
            values=values, source_digest=digest, production_writes=False,
        )
        serialized = json.dumps([other, sources])
        for old in (*GUIDS.values(), VALUES["RAYFIN_PUBLIC_API_URL"], VALUES["RAYFIN_PUBLIC_PUBLISHABLE_KEY"], ENDPOINT, KUSTO):
            self.assertNotIn(old, serialized)
        live = {"versions": {"latest": {"definition": {"environment_variables": {
            "HYDRO_ORCHESTRATOR_CONFIG": json.dumps(other),
        }}}}}
        provision.verify_hosted_source(live, ids["tenant"], ids["workspace"], ids["ontology"], digest)


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
        self.assertEqual(hosted.registry_name(CONNECTIONS, "myreg"), "myreg")
        self.assertEqual(hosted.registry_name(CONNECTIONS, ""), "myreg")
        two = CONNECTIONS + [{"name": "b", "properties": {"category": "ContainerRegistry", "target": "other.azurecr.io"}}]
        self.assertEqual(hosted.registry_name(two, "other"), "other")
        with self.assertRaisesRegex(RuntimeError, "live ContainerRegistry"):
            hosted.registry_name(CONNECTIONS, "stale")
        with self.assertRaisesRegex(RuntimeError, "HYDRO_ACR_NAME"):
            hosted.registry_name(two, "")

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
    def test_scoped_hosted_agent_reuses_model_and_image_and_publishes_only_its_service(self):
        suffix = provision.foundry_resource_suffix(GUIDS["workspace"])
        configuration_value, fabric_source = hosted.compose_runtime_configuration(
            tenant=GUIDS["tenant"], workspace=GUIDS["workspace"], ontology=GUIDS["ontology"],
            spa_client_id=GUIDS["spa"], kusto_origin=KUSTO, project_endpoint=ENDPOINT,
            values=VALUES, source_digest=configuration()[1], production_writes=True,
            resource_suffix=suffix,
        )
        expected = {
            **environment(),
            "HYDRO_ORCHESTRATOR_CONFIG": json.dumps(configuration_value, separators=(",", ":")),
            "HYDRO_FABRIC_SOURCE_CONFIG": json.dumps(fabric_source, separators=(",", ":")),
        }
        fake = FakeDeploy(registry_tags={self.tag(): DIGEST_A})
        run(fake, [None, agent(DIGEST_A, env=expected)], resource_suffix=suffix)
        commands = fake.commands("azd", "deploy")
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][2], "hydro-orchestrator" + suffix)
        self.assertFalse(fake.commands("az", "acr", "build"))
        fake = FakeDeploy(registry_tags={self.tag(): DIGEST_A})
        run(fake, [agent(DIGEST_A, env=expected)], resource_suffix=suffix)
        self.assertFalse(fake.commands("azd", "deploy"))

    def tag(self):
        return hosted.image_tag(REPO_ROOT)

    def test_a_current_agent_costs_one_registry_lookup_and_changes_nothing(self):
        fake = FakeDeploy(registry_tags={self.tag(): DIGEST_A})
        with patch.object(hosted, "azd_project_dir") as project_dir:
            self.assertEqual(run(fake, [agent()]), agent())
        project_dir.assert_not_called()
        self.assertEqual(len(fake.commands("az", "acr", "repository")), 1)
        self.assertEqual(fake.commands("az", "acr", "build"), [])
        self.assertEqual(fake.commands("azd"), [])

    def test_changed_source_builds_sets_the_environment_deploys_and_waits_for_activation(self):
        fake = FakeDeploy()
        result = run(fake, [agent(DIGEST_A, version=7), agent(DIGEST_A, version=7), agent(DIGEST_B, version=8)])
        self.assertEqual(hosted.hosted_state(result)[1:3], ("8", DIGEST_B))
        build = fake.commands("az", "acr", "build")[0]
        self.assertIn(f"hydro-orchestrator:{self.tag()}", build)
        self.assertEqual(build[build.index("--platform") + 1], "linux/amd64")
        self.assertEqual(Path(build[build.index("--file") + 1]), fake.build_context / hosted.DOCKERFILE)
        self.assertIn("--no-logs", build)
        self.assertNotIn("--no-wait", build)
        self.assertEqual(build[build.index("--timeout") + 1], "1200")
        sets = fake.environment_settings()
        self.assertEqual(len(fake.commands("azd", "env", "set")), 1)
        self.assertEqual(sets["HYDRO_ORCHESTRATOR_IMAGE"], f"myreg.azurecr.io/hydro-orchestrator@{DIGEST_B}")
        self.assertEqual(json.loads(sets["HYDRO_ORCHESTRATOR_CONFIG"])["production_writes_enabled"], True)
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

    def test_acr_build_context_contains_only_the_exact_fingerprinted_files_and_is_cleaned(self):
        fake = FakeDeploy()
        run(fake, [agent(DIGEST_A), agent(DIGEST_A), agent(DIGEST_B)])
        self.assertNotEqual(fake.build_context, REPO_ROOT)
        self.assertEqual(fake.build_cwd, REPO_ROOT)
        self.assertTrue(fake.build_context.is_absolute())
        self.assertEqual(fake.build_context_files, {
            path.relative_to(REPO_ROOT).as_posix(): path.read_bytes()
            for path in hosted.packaged_sources(REPO_ROOT)
        })
        self.assertFalse(fake.build_context.exists())

    def test_failed_acr_build_cleans_its_context_and_propagates_the_failure(self):
        fake = FakeDeploy()
        contexts = []
        original = fake.run_stream

        def stream(argv, cwd=None, env=None):
            if argv[:3] == ["az", "acr", "build"]:
                self.assertEqual(cwd, REPO_ROOT)
                contexts.append(Path(argv[-1]))
                raise fake.DeployError("remote build failed")
            return original(argv, cwd=cwd, env=env)

        with patch.object(fake, "run_stream", side_effect=stream):
            with self.assertRaisesRegex(fake.DeployError, "remote build failed"):
                run(fake, [agent(DIGEST_A)])
        self.assertEqual(len(contexts), 1)
        self.assertFalse(contexts[0].exists())

    def test_existing_azd_environment_is_rebound_to_verified_project_and_registry_before_publish(self):
        fake = FakeDeploy(registry_tags={self.tag(): DIGEST_A})
        stale = agent(DIGEST_A, env={**environment(), "HYDRO_FABRIC_SOURCE_CONFIG": "{}"})
        run(fake, [stale, agent()])
        self.assertEqual(fake.commands("azd", "env", "new"), [])
        sets = fake.environment_settings()
        self.assertEqual(sets["AZURE_AI_PROJECT_ID"], PROJECT_ID)
        self.assertEqual(sets["AZURE_AI_PROJECT_ENDPOINT"], ENDPOINT)
        self.assertEqual(sets["AZURE_TENANT_ID"], GUIDS["tenant"])
        self.assertEqual(sets["AZURE_CONTAINER_REGISTRY_NAME"], "myreg")
        self.assertEqual(sets["AZURE_AI_PROJECT_ACR_CONNECTION_NAME"], "reg-conn")
        self.assertLess(
            fake.log.index(fake.commands("azd", "env", "set")[0]),
            fake.log.index(fake.commands("azd", "deploy")[0]),
        )

    def test_old_live_write_policy_does_not_override_the_enabled_default(self):
        for raw in ("not-json", "[]", '{"production_writes_enabled":"false"}'):
            with self.subTest(raw=raw), patch.dict(os.environ, {"HYDRO_PRODUCTION_WRITES_ENABLED": ""}):
                fake = FakeDeploy()
                live = agent(env={**environment(), "HYDRO_ORCHESTRATOR_CONFIG": raw})
                run(fake, [live, agent(DIGEST_B)])
                sets = fake.environment_settings()
                self.assertIs(json.loads(sets["HYDRO_ORCHESTRATOR_CONFIG"])["production_writes_enabled"], True)
        with patch.dict(os.environ, {"HYDRO_PRODUCTION_WRITES_ENABLED": "false"}):
            live = agent(env={**environment(), "HYDRO_ORCHESTRATOR_CONFIG": "not-json"})
            fake = FakeDeploy()
            run(fake, [live, agent(DIGEST_B, env=environment(writes=False))])
            sets = fake.environment_settings()
            self.assertIs(json.loads(sets["HYDRO_ORCHESTRATOR_CONFIG"])["production_writes_enabled"], False)

    def test_production_writes_default_to_enabled_and_respect_an_explicit_override(self):
        fake = FakeDeploy()
        live = agent(DIGEST_A, env=environment(writes=False))
        with patch.dict("os.environ", {"HYDRO_PRODUCTION_WRITES_ENABLED": ""}):
            run(fake, [live, live, agent(DIGEST_B, env=environment(writes=True))])
        sets = fake.environment_settings()
        self.assertIs(json.loads(sets["HYDRO_ORCHESTRATOR_CONFIG"])["production_writes_enabled"], True)
        for value, expected in (("false", False), ("true", True)):
            with self.subTest(override=value):
                fake = FakeDeploy()
                before = agent(DIGEST_A, env=environment(writes=not expected))
                after = agent(DIGEST_B, env=environment(writes=expected))
                with patch.dict("os.environ", {"HYDRO_PRODUCTION_WRITES_ENABLED": value}):
                    run(fake, [before, before, after])
                sets = fake.environment_settings()
                self.assertIs(json.loads(sets["HYDRO_ORCHESTRATOR_CONFIG"])["production_writes_enabled"], expected)
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
        names = set(fake.environment_settings())
        self.assertEqual(len(fake.commands("azd", "env", "set")), 1)
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

    def test_repository_template_never_pins_a_live_project_or_reuses_repo_state(self):
        template = (REPO_ROOT / "azure.yaml").read_text(encoding="utf-8")
        self.assertEqual(hosted.PINNED_ENDPOINT_RE.search(template)["value"], "${AZURE_AI_PROJECT_ENDPOINT}")
        self.assertNotIn(".services.ai.azure.com", template)
        self.assertNotEqual(hosted.azd_project_dir(FakeDeploy(), ENDPOINT), REPO_ROOT)

    def test_distinct_project_endpoints_cannot_collide_in_generated_state(self):
        first = "https://res.services.ai.azure.com/api/projects/a.b"
        second = "https://res.services.ai.azure.com/api/projects/a-b"
        self.assertNotEqual(hosted.azd_project_dir(FakeDeploy(), first), hosted.azd_project_dir(FakeDeploy(), second))
        self.assertEqual(hosted.azd_project_dir(FakeDeploy(), first), hosted.azd_project_dir(FakeDeploy(), first + "/"))

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

    def setUp(self):
        settings = patch.dict(os.environ, {
            "HYDRO_FOUNDRY_MODEL_NAME": "", "HYDRO_FOUNDRY_MODEL_VERSION": "test-version",
            "HYDRO_FOUNDRY_MODEL_SKU": "TestSku", "HYDRO_FOUNDRY_MODEL_CAPACITY": "7",
            "HYDRO_FOUNDRY_NEW_PROJECT_NAME": "",
            "HYDRO_FOUNDRY_ACCOUNT_NAME": "",
        })
        settings.start()
        self.addCleanup(settings.stop)

    def create(self, fake, **overrides):
        arguments = dict(tenant="tenant.example", workspace="ws", subscription="sub-1",
                         location="swedencentral", model="model-x",
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

    def test_new_model_defaults_prefer_regional_then_pay_as_you_go_never_provisioned(self):
        fake = self.fake()
        model = {
            "name": "approved-chat", "format": "OpenAI", "version": "catalog-version",
            "isDefaultVersion": True, "capabilities": {"chatCompletion": "true"},
            "skus": [
                {"name": "Standard", "usageName": "chat", "capacity": {"default": 6, "maximum": 100}},
                {"name": "Standard", "usageName": "chat-finetune", "capacity": {"default": 10}},
                {"name": "GlobalStandard", "capacity": {"default": 12}},
                {"name": "ProvisionedManaged", "capacity": {"minimum": 25}},
            ],
        }
        with patch.dict(os.environ, {
            "HYDRO_FOUNDRY_MODEL_NAME": "", "HYDRO_FOUNDRY_MODEL_VERSION": "",
            "HYDRO_FOUNDRY_MODEL_SKU": "", "HYDRO_FOUNDRY_MODEL_CAPACITY": "",
        }), patch.object(fake, "run_capture", return_value=json.dumps([{"model": model}])) as capture:
            settings = project.model_defaults(fake, subscription="sub", location="targetregion", model="approved-chat")
            self.assertEqual(settings, {
                "HYDRO_FOUNDRY_MODEL_NAME": "approved-chat", "HYDRO_FOUNDRY_MODEL_VERSION": "catalog-version",
                "HYDRO_FOUNDRY_MODEL_SKU": "Standard", "HYDRO_FOUNDRY_MODEL_CAPACITY": "6",
            })
            argv = capture.call_args.args[0]
            self.assertEqual(argv[:3], ["az", "rest", "--method"])
            self.assertEqual(argv[argv.index("--subscription") + 1], "sub")
            self.assertIn("/locations/targetregion/models?", argv[argv.index("--url") + 1])
            model["skus"] = [
                {"name": "GlobalStandard", "capacity": {"default": 12}},
                {"name": "DataZoneStandard", "capacity": {"default": 8}},
            ]
            capture.return_value = json.dumps([{"model": model}])
            self.assertEqual(project.model_defaults(
                fake, subscription="sub", location="region", model="approved-chat",
            )["HYDRO_FOUNDRY_MODEL_SKU"], "DataZoneStandard")
            model["skus"] = [{"name": "GlobalStandard", "capacity": {"default": 12}}]
            capture.return_value = json.dumps([{"model": model}])
            self.assertEqual(project.model_defaults(
                fake, subscription="sub", location="region", model="approved-chat",
            )["HYDRO_FOUNDRY_MODEL_SKU"], "GlobalStandard")
            with patch.dict(os.environ, {"HYDRO_FOUNDRY_MODEL_SKU": "GlobalStandard"}):
                self.assertEqual(project.model_defaults(
                    fake, subscription="sub", location="region", model="approved-chat",
                )["HYDRO_FOUNDRY_MODEL_SKU"], "GlobalStandard")
            model["skus"] = [{"name": "ProvisionedManaged", "capacity": {"minimum": 25}}]
            capture.return_value = json.dumps([{"model": model}])
            with self.assertRaisesRegex(fake.DeployError, "pay-as-you-go"):
                project.model_defaults(fake, subscription="sub", location="region", model="approved-chat")
            with self.assertRaisesRegex(fake.DeployError, "does not identify one underlying model"):
                project.model_defaults(fake, subscription="sub", location="region", model="missing")

    def test_deployment_alias_resolves_live_model_and_version_without_additional_inputs(self):
        fake = self.fake()
        catalog = [{"model": {
            "name": "catalog-chat", "format": "OpenAI", "version": "v2",
            "isDefaultVersion": False, "capabilities": {"chatCompletion": True},
            "skus": [{"name": "GlobalStandard", "capacity": {"minimum": 1}}],
        }}]
        accounts = [{"kind": "AIServices", "name": "account", "resourceGroup": "group"}]
        deployments = [{"name": "my-chat-alias", "properties": {
            "provisioningState": "Succeeded",
            "model": {"format": "OpenAI", "name": "catalog-chat", "version": "v2"},
        }}]
        with patch.dict(os.environ, {
            "HYDRO_FOUNDRY_MODEL_NAME": "", "HYDRO_FOUNDRY_MODEL_VERSION": "",
            "HYDRO_FOUNDRY_MODEL_SKU": "", "HYDRO_FOUNDRY_MODEL_CAPACITY": "",
        }), patch.object(fake, "run_capture", side_effect=[
            json.dumps(catalog), json.dumps(accounts), json.dumps(deployments),
        ]) as capture:
            settings = project.model_defaults(fake, subscription="sub", location="region", model="my-chat-alias")
        self.assertEqual(settings, {
            "HYDRO_FOUNDRY_MODEL_NAME": "catalog-chat", "HYDRO_FOUNDRY_MODEL_VERSION": "v2",
            "HYDRO_FOUNDRY_MODEL_SKU": "GlobalStandard", "HYDRO_FOUNDRY_MODEL_CAPACITY": "1",
        })
        encoded = project.deployments_parameter("my-chat-alias", settings)
        self.assertEqual(json.loads(json.loads('{"value":"' + encoded + '"}')["value"])[0]["name"], "my-chat-alias")
        self.assertTrue(all(call.args[0][call.args[0].index("--subscription") + 1] == "sub"
                            for call in capture.call_args_list))
        self.assertEqual(fake.log, [])

    def test_duplicate_account_sku_catalog_rows_are_not_ambiguous_model_versions(self):
        fake = self.fake()
        model = {
            "name": "chat", "format": "OpenAI", "version": "v1", "isDefaultVersion": True,
            "capabilities": {"chatCompletion": True},
            "skus": [{"name": "GlobalStandard", "capacity": {"default": 10, "minimum": None}}],
        }
        with patch.dict(os.environ, {
            "HYDRO_FOUNDRY_MODEL_NAME": "", "HYDRO_FOUNDRY_MODEL_VERSION": "",
            "HYDRO_FOUNDRY_MODEL_SKU": "", "HYDRO_FOUNDRY_MODEL_CAPACITY": "",
        }), patch.object(fake, "run_capture", return_value=json.dumps([
            {"model": model, "skuName": "S0"}, {"model": model, "skuName": "S1"},
        ])) as capture:
            settings = project.model_defaults(fake, subscription="sub", location="region", model="chat")
            self.assertEqual(settings["HYDRO_FOUNDRY_MODEL_VERSION"], "v1")
            self.assertEqual(settings["HYDRO_FOUNDRY_MODEL_CAPACITY"], "10")
            capture.return_value = json.dumps([{"model": model}, {"model": {**model, "version": "v2"}}])
            with self.assertRaisesRegex(fake.DeployError, "no unique supported chat version"):
                project.model_defaults(fake, subscription="sub", location="region", model="chat")

    def test_ambiguous_deployment_alias_fails_before_resource_creation(self):
        fake = self.fake()
        accounts = [{"kind": "OpenAI", "name": name, "resourceGroup": "group"} for name in ("first", "second")]
        def deployment(name):
            return [{"name": "shared-alias", "properties": {
                "provisioningState": "Succeeded", "model": {"name": name, "format": "OpenAI", "version": "v1"},
            }}]
        with patch.object(fake, "run_capture", side_effect=[
            json.dumps(accounts), json.dumps(deployment("chat-one")), json.dumps(deployment("chat-two")),
        ]):
            with self.assertRaisesRegex(fake.DeployError, "No model was guessed"):
                project.model_reference(fake, subscription="sub", deployment="shared-alias")

    def test_catalog_capacity_constraints_and_nondefault_version_require_valid_selection(self):
        fake = self.fake()
        model = {
            "name": "chat", "format": "OpenAI", "version": "v1", "isDefaultVersion": True,
            "capabilities": {"chatCompletion": True},
            "skus": [{"name": "Standard", "capacity": {"minimum": 2, "maximum": 10, "step": 2}}],
        }
        with patch.dict(os.environ, {
            "HYDRO_FOUNDRY_MODEL_NAME": "", "HYDRO_FOUNDRY_MODEL_VERSION": "",
            "HYDRO_FOUNDRY_MODEL_SKU": "", "HYDRO_FOUNDRY_MODEL_CAPACITY": "",
        }), patch.object(fake, "run_capture", return_value=json.dumps([{"model": model}])) as capture:
            self.assertEqual(project.model_defaults(
                fake, subscription="sub", location="region", model="chat",
            )["HYDRO_FOUNDRY_MODEL_CAPACITY"], "2")
            for capacity in ("0", "3", "12"):
                with self.subTest(capacity=capacity), patch.dict(os.environ, {"HYDRO_FOUNDRY_MODEL_CAPACITY": capacity}):
                    with self.assertRaises(fake.DeployError):
                        project.model_defaults(fake, subscription="sub", location="region", model="chat")
            model["isDefaultVersion"] = False
            capture.return_value = json.dumps([{"model": model}])
            with self.assertRaisesRegex(fake.DeployError, "no unique supported"):
                project.model_defaults(fake, subscription="sub", location="region", model="chat")
            with patch.dict(os.environ, {"HYDRO_FOUNDRY_MODEL_VERSION": "v1"}):
                self.assertEqual(project.model_defaults(
                    fake, subscription="sub", location="region", model="chat",
                )["HYDRO_FOUNDRY_MODEL_VERSION"], "v1")
        with patch.dict(os.environ, {"HYDRO_FOUNDRY_CREATE": "yes"}):
            self.assertFalse(project.enabled())

    def test_new_capacity_prefers_75k_and_falls_back_to_available_model_specific_units(self):
        fake = self.fake()
        sku = {
            "name": "Standard", "usageName": "OpenAI.Standard.chat",
            "capacity": {"minimum": 1, "maximum": 1000, "step": 1},
            "rateLimits": [{"key": "token", "count": 1000, "renewalPeriod": 60}],
        }
        cases = [
            (1000, 100, 0, {}, "75"),
            (1000, 50, 10, {}, "40"),
            (6000, 100, 0, {}, "13"),
            (1000, 100, 0, {"minimum": 10, "step": 10}, "80"),
            (1000, 66, 0, {"minimum": 10, "step": 10}, "60"),
            (1000, 100, 0, {"allowedValues": [10, 40, 100]}, "100"),
            (1000, 60, 0, {"allowedValues": [10, 40, 100]}, "40"),
            (1000, 100, 0, {"maximum": 30}, "30"),
        ]
        for rate, limit, used, constraints, expected in cases:
            selected = {**sku, "capacity": {**sku["capacity"], **constraints},
                        "rateLimits": [{"key": "token", "count": rate, "renewalPeriod": 60}]}
            usage = [{"name": {"value": sku["usageName"]}, "limit": limit, "currentValue": used}]
            with self.subTest(rate=rate, limit=limit, constraints=constraints), \
                    patch.object(fake, "run_capture", return_value=json.dumps(usage)):
                self.assertEqual(project.preferred_model_capacity(fake, "sub", "region", selected, "1"), expected)
        with patch.object(fake, "run_capture", return_value=json.dumps([
            {"name": {"value": sku["usageName"]}, "limit": 10, "currentValue": 10},
        ])):
            with self.assertRaisesRegex(fake.DeployError, "No supported positive capacity"):
                project.preferred_model_capacity(fake, "sub", "region", sku, "1")

    def test_new_model_uses_raw_catalog_rates_and_preserves_explicit_capacity(self):
        fake = self.fake()
        catalog = [{"model": {
            "name": "chat", "format": "OpenAI", "version": "v1", "isDefaultVersion": True,
            "capabilities": {"chatCompletion": True},
            "skus": [{"name": "Standard", "usageName": "OpenAI.Standard.chat",
                      "capacity": {"minimum": 1, "maximum": 1000},
                      "rateLimits": [{"key": "token", "count": 1000, "renewalPeriod": 60}]}],
        }}]
        usage = [{"name": {"value": "OpenAI.Standard.chat"}, "limit": 50, "currentValue": 10}]
        with patch.dict(os.environ, {
            "HYDRO_FOUNDRY_MODEL_NAME": "", "HYDRO_FOUNDRY_MODEL_VERSION": "",
            "HYDRO_FOUNDRY_MODEL_SKU": "", "HYDRO_FOUNDRY_MODEL_CAPACITY": "",
        }), patch.object(fake, "run_capture", side_effect=[json.dumps(catalog), json.dumps(usage)]) as read:
            self.assertEqual(project.model_defaults(
                fake, subscription="sub", location="region", model="chat",
            )["HYDRO_FOUNDRY_MODEL_CAPACITY"], "40")
            self.assertEqual(read.call_count, 2)
            read.reset_mock(side_effect=True)
            read.return_value = json.dumps(catalog)
            with patch.dict(os.environ, {"HYDRO_FOUNDRY_MODEL_CAPACITY": "20"}):
                self.assertEqual(project.model_defaults(
                    fake, subscription="sub", location="region", model="chat",
                )["HYDRO_FOUNDRY_MODEL_CAPACITY"], "20")
            read.assert_called_once()

    def test_deployments_json_survives_the_templates_quoted_parameter_file(self):
        parameter = project.deployments_parameter("model-x")
        # The template embeds the value inside quotes in a JSON parameters file.
        embedded = json.loads('{"value": "' + parameter + '"}')["value"]
        self.assertEqual(json.loads(embedded), [{
            "name": "model-x", "model": {"name": "model-x", "format": "OpenAI", "version": "test-version"},
            "sku": {"name": "TestSku", "capacity": 7},
        }])
        with patch.dict(os.environ, {"HYDRO_FOUNDRY_MODEL_VERSION": "v2", "HYDRO_FOUNDRY_MODEL_SKU": "Standard",
                                      "HYDRO_FOUNDRY_MODEL_CAPACITY": "50"}):
            again = json.loads(json.loads('{"value": "' + project.deployments_parameter("m") + '"}')["value"])[0]
        self.assertEqual((again["model"]["version"], again["sku"]), ("v2", {"name": "Standard", "capacity": 50}))
        with patch.dict(os.environ, {"HYDRO_FOUNDRY_MODEL_CAPACITY": "lots"}):
            with self.assertRaisesRegex(ValueError, "whole number"):
                project.deployments_parameter("m")

    def test_creation_has_no_implicit_model_version_sku_or_capacity(self):
        for key in ("HYDRO_FOUNDRY_MODEL_VERSION", "HYDRO_FOUNDRY_MODEL_SKU", "HYDRO_FOUNDRY_MODEL_CAPACITY"):
            with self.subTest(key=key), patch.dict(os.environ, {key: ""}):
                fake = self.fake()
                with self.assertRaisesRegex(FakeDeploy.DeployError, "New Foundry projects require"):
                    self.create(fake)
                self.assertEqual(fake.log, [])
        for bad in ("0", "-1", "1.5"):
            with self.subTest(bad=bad), patch.dict(os.environ, {"HYDRO_FOUNDRY_MODEL_CAPACITY": bad}):
                with self.assertRaisesRegex(ValueError, "whole number"):
                    project.deployments_parameter("m")

    def test_catalog_model_name_is_distinct_from_deployment_name(self):
        with patch.dict(os.environ, {"HYDRO_FOUNDRY_MODEL_NAME": "catalog-chat-model"}):
            parameter = project.deployments_parameter("my-custom-deployment")
        deployment = json.loads(json.loads('{"value": "' + parameter + '"}')["value"])[0]
        self.assertEqual(deployment["name"], "my-custom-deployment")
        self.assertEqual(deployment["model"]["name"], "catalog-chat-model")

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
        self.assertNotEqual(project.environment_name("t", "w", "sub-1"), project.environment_name("t", "w", "sub-2"))
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
        sets = fake.environment_settings()
        self.assertEqual(len(fake.commands("azd", "env", "set")), 1)
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

    def test_named_project_is_exact_and_state_is_scoped_to_target_and_name(self):
        with patch.dict(os.environ, {"HYDRO_FOUNDRY_NEW_PROJECT_NAME": "My.Project"}):
            name = project.environment_name("t", "w", "sub")
            self.assertEqual(name, project.environment_name("T", "W", "SUB"))
            self.assertNotEqual(name, project.environment_name("t", "other", "sub"))
            fake = self.fake("https://new-res.services.ai.azure.com/api/projects/My.Project")
            self.create(fake)
            self.assertEqual(fake.environment_settings()["AZURE_AI_PROJECT_NAME"], "My.Project")
            with self.assertRaisesRegex(fake.DeployError, "different project"):
                self.create(self.fake())
        with patch.dict(os.environ, {"HYDRO_FOUNDRY_NEW_PROJECT_NAME": "Another.Project"}):
            self.assertNotEqual(name, project.environment_name("t", "w", "sub"))
        with patch.dict(os.environ, {"HYDRO_FOUNDRY_NEW_PROJECT_NAME": "../bad"}):
            with self.assertRaises(ValueError):
                project.environment_name("t", "w", "sub")

    def test_named_account_is_exact_and_scopes_state_without_changing_blank_defaults(self):
        original = project.environment_name("t", "w", "sub")
        with patch.dict(os.environ, {"HYDRO_FOUNDRY_ACCOUNT_NAME": "new-res"}):
            name = project.environment_name("t", "w", "sub")
            self.assertNotEqual(name, original)
            fake = self.fake()
            fake.azd_values += 'AZURE_AI_ACCOUNT_NAME="new-res"\n'
            self.create(fake)
            self.assertEqual(fake.environment_settings()["AZURE_AI_ACCOUNT_NAME"], "new-res")
            with self.assertRaisesRegex(fake.DeployError, "different account"):
                self.create(self.fake())
            with patch.dict(os.environ, {"HYDRO_FOUNDRY_ACCOUNT_NAME": "NEW-RES"}):
                self.assertEqual(name, project.environment_name("T", "W", "SUB"))
            with patch.dict(os.environ, {"HYDRO_FOUNDRY_ACCOUNT_NAME": "other-account"}):
                self.assertNotEqual(name, project.environment_name("t", "w", "sub"))
        for invalid in ("a", "-account", "account-", "has.dot", "x" * 65):
            with patch.dict(os.environ, {"HYDRO_FOUNDRY_ACCOUNT_NAME": invalid}):
                fake = self.fake()
                with self.assertRaisesRegex(fake.DeployError, "Foundry account name"):
                    self.create(fake)
                self.assertEqual(fake.commands("azd", "provision"), [])

    def test_shared_project_name_reuses_the_existing_named_creation_pipeline(self):
        with patch.dict(os.environ, {
            "HYDRO_FOUNDRY_CREATE": "true", "HYDRO_FOUNDRY_NEW_PROJECT_NAME": "",
            "HYDRO_FOUNDRY_EXISTING_PROJECT_NAME": "Shared.Project",
        }):
            endpoint = "https://new-res.services.ai.azure.com/api/projects/Shared.Project"
            fake = self.fake(endpoint=endpoint)
            self.assertEqual(self.create(fake), endpoint)
            self.assertEqual(fake.environment_settings()["AZURE_AI_PROJECT_NAME"], "Shared.Project")
            name = project.environment_name("tenant.example", "ws", "sub-1")
            with patch.dict(os.environ, {"HYDRO_FOUNDRY_EXISTING_PROJECT_NAME": "shared.project"}):
                self.assertEqual(project.environment_name("tenant.example", "ws", "sub-1"), name)
            with patch.dict(os.environ, {"HYDRO_FOUNDRY_EXISTING_PROJECT_NAME": "another-project"}):
                self.assertNotEqual(project.environment_name("tenant.example", "ws", "sub-1"), name)

    def test_an_already_initialized_folder_is_reused_not_reinitialized(self):
        fake = self.fake()
        folder = hosted.state_root() / "foundry-project" / project.environment_name("tenant.example", "ws", "sub-1")
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
        return {**{key: "x" for key in self.KEYS[:present]},
                "RAYFIN_PUBLIC_OPERATIONAL_SQL_DATABASE_ID": GUIDS["operational"]}, None

    def test_an_existing_target_is_left_alone(self):
        with patch.object(DEPLOY, "current_rayfin_target", return_value=({key: "x" for key in self.KEYS}, None)), \
             patch.object(DEPLOY, "run_rayfin_deployment") as up:
            DEPLOY.ensure_appbackend_identity("tenant", "workspace")
        up.assert_not_called()

    def test_a_fresh_target_gets_a_backend_without_static_hosting_before_foundry_provisioning(self):
        states = [self.values(0), self.values(0), self.values(3)]
        with patch.object(DEPLOY, "current_rayfin_target", side_effect=states), \
             patch.object(DEPLOY, "ensure_rayfin_login") as login, \
             patch.object(DEPLOY, "rayfin24", side_effect=lambda *a: ["rayfin", *a]), \
             patch.object(DEPLOY, "run_rayfin_deployment") as up:
            DEPLOY.ensure_appbackend_identity("tenant", "workspace")
        login.assert_called_once_with("tenant", "workspace")
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
        source = inspect.getsource(DEPLOY._deploy)
        self.assertLess(source.index('"validate-env.mjs"'), source.index("provision_foundry_agents("))
        provisioning = inspect.getsource(DEPLOY.provision_foundry_agents)
        self.assertLess(provisioning.index("ensure_appbackend_identity("),
                        provisioning.index("ensure_hosted_source_prerequisites("))
        self.assertLess(provisioning.index("ensure_hosted_source_prerequisites("),
                        provisioning.index("foundry.provision("))


if __name__ == "__main__":
    unittest.main()
