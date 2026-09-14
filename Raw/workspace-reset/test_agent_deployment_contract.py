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


class AgentDeploymentContractTests(unittest.TestCase):
    def test_canonical_orchestrator_exists(self):
        self.assertTrue((REPO_ROOT / CANONICAL_SCRIPT).is_file())

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


if __name__ == "__main__":
    unittest.main()
