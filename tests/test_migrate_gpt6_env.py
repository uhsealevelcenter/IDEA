import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deployment" / "migrate_gpt6_env.py"
SPEC = importlib.util.spec_from_file_location("migrate_gpt6_env", SCRIPT)
migration = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(migration)


class MigrateGpt6EnvTests(unittest.TestCase):
    def test_replaces_old_defaults_and_preserves_secrets_and_custom_models(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "OPENAI_API_KEY=private-value\n"
                "IDEA_AGENT_MODEL=gpt-5.5\n"
                "IDEA_TOOL_MODEL=my-custom-model\n"
                "IDEA_CODEX_MODEL=gpt-5.6-terra\n"
            )
            changed = migration.migrate(path, migration.MIGRATIONS["langgraph/.env"])
            result = path.read_text()
            self.assertEqual(migration.migrate(path, migration.MIGRATIONS["langgraph/.env"]), [])

        self.assertIn("IDEA_AGENT_MODEL", changed)
        self.assertIn("IDEA_CODEX_REASONING_EFFORT", changed)
        self.assertIn("OPENAI_API_KEY=private-value\n", result)
        self.assertIn("IDEA_AGENT_MODEL=gpt-6-sol\n", result)
        self.assertIn("IDEA_TOOL_MODEL=my-custom-model\n", result)
        self.assertIn("IDEA_CODEX_MODEL=gpt-6-sol\n", result)
        self.assertIn("IDEA_CODEX_REASONING_EFFORT=medium\n", result)


if __name__ == "__main__":
    unittest.main()
