import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_SCRIPT = "Raw/workspace-reset/deploy_fabric_app.py"
INSTRUCTION_FILES = (
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / ".github" / "copilot-instructions.md",
    REPO_ROOT / ".github" / "prompts" / "deploy-fresh-tenant.prompt.md",
    REPO_ROOT / "HydroOperationsApp" / "DEPLOY.md",
)
RUNTIME_READINESS_FILES = (
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / ".github" / "copilot-instructions.md",
    REPO_ROOT / ".github" / "prompts" / "deploy-fresh-tenant.prompt.md",
)
DATA_AGENT_NOTEBOOKS = (
    REPO_ROOT / "Notebooks" / "RTI_009_build_data_agent.Notebook" / "notebook-content.py",
    REPO_ROOT / "Raw" / "RTI_Notebooks" / "RTI_009_build_data_agent.ipynb",
)


class AgentDeploymentContractTests(unittest.TestCase):
    def test_canonical_orchestrator_exists(self):
        self.assertTrue((REPO_ROOT / CANONICAL_SCRIPT).is_file())
        self.assertFalse(
            (REPO_ROOT / "Raw/workspace-reset/deploy_fabric_app.old").exists()
        )

    def test_all_agent_instructions_name_the_one_shot_orchestrator(self):
        for file in INSTRUCTION_FILES:
            with self.subTest(file=file.relative_to(REPO_ROOT)):
                content = file.read_text(encoding="utf-8")
                self.assertIn(CANONICAL_SCRIPT, content)
                self.assertIn("--tenant", content)
                self.assertIn("--workspace", content)

    def test_agent_guidance_has_no_stale_path_or_destructive_reset_command(self):
        for file in INSTRUCTION_FILES:
            with self.subTest(file=file.relative_to(REPO_ROOT)):
                content = file.read_text(encoding="utf-8")
                self.assertNotIn("FabricOntologyHydro", content)
                self.assertNotIn("Remove-Item", content)
                self.assertNotIn("→ delete it", content)

    def test_agent_guidance_requires_portable_endpoint_and_cors_validation(self):
        for file in RUNTIME_READINESS_FILES:
            with self.subTest(file=file.relative_to(REPO_ROOT)):
                content = file.read_text(encoding="utf-8")
                self.assertIn("pbidedicated.windows.net", content)
                self.assertIn("/graphql", content)
                self.assertIn("/api/auth/v1/token", content)
                self.assertIn("CORS", content)

    def test_data_agent_has_canonical_running_bad_contract(self):
        required = (
            "Canonical",
            "running bad",
            "literal telemetry quality",
            "all of its active instruments",
            "30-minute lookback",
            "single raw reading with greatest",
            "same-signal",
            "equipment-level work",
        )
        for file in DATA_AGENT_NOTEBOOKS:
            with self.subTest(file=file.relative_to(REPO_ROOT)):
                content = file.read_text(encoding="utf-8")
                for phrase in required:
                    self.assertIn(phrase, content)


if __name__ == "__main__":
    unittest.main()
