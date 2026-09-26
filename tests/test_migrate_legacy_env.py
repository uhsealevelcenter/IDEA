import importlib.util
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deployment" / "migrate_legacy_env.py"
SPEC = importlib.util.spec_from_file_location("migrate_legacy_env", SCRIPT)
migration = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(migration)


class MigrateLegacyEnvTests(unittest.TestCase):
    def make_root(self, directory: str) -> Path:
        root = Path(directory)
        for service in migration.SERVICES:
            target = root / service
            target.mkdir()
            shutil.copy(ROOT / service / ".env.example", target / ".env.example")
        return root

    def test_preserves_legacy_credentials_and_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_root(directory)
            (root / ".env").write_text(
                "POSTGRES_USER=legacy-admin\n"
                "POSTGRES_PASSWORD='postgres $value # kept'\n"
                "POSTGRES_DB=legacy_db\n"
                "LANGGRAPH_DB_PASSWORD=langgraph-db\n"
                "LITELLM_DB_PASSWORD=litellm-db\n"
                "LANGFUSE_DB_PASSWORD=langfuse-db\n"
                "OPENAI_API_KEY=openai-key\n"
                "OPENAI_BASE_URL=https://models.example/v1\n"
                "INTERNAL_SERVICE_TOKEN=identity-token\n"
                "OPENWEBUI_API_KEY=openwebui-key\n"
                "WEBUI_SECRET_KEY=webui-secret\n"
                "LITELLM_MASTER_KEY=litellm-master\n"
                "LITELLM_VIRTUAL_KEY=litellm-virtual\n"
                "LITELLM_LANGFUSE_PUBLIC_KEY=legacy-public\n"
                "LITELLM_LANGFUSE_SECRET_KEY=legacy-secret\n"
                "LANGFUSE_NEXTAUTH_SECRET=nextauth-secret\n"
                "LANGFUSE_SALT=langfuse-salt\n"
                "LANGFUSE_ENCRYPTION_KEY=encryption-key\n"
                "CORS_ORIGINS=https://idea.example\n"
            )

            created = migration.migrate(root)
            second_run = migration.migrate(root)

            self.assertEqual(len(created), len(migration.SERVICES))
            self.assertEqual(second_run, [])
            db = migration.parse_env(root / "db" / ".env")
            langgraph = migration.parse_env(root / "langgraph" / ".env")
            sandbox = migration.parse_env(root / "sandbox_service" / ".env")
            litellm = migration.parse_env(root / "litellm" / ".env")
            langfuse = migration.parse_env(root / "langfuse" / ".env")
            openwebui = migration.parse_env(root / "openwebui" / ".env")

            self.assertEqual(db["POSTGRES_PASSWORD"], "postgres $value # kept")
            self.assertEqual(langgraph["IDEA_IDENTITY_SECRET"], "identity-token")
            self.assertEqual(langgraph["INTERNAL_SERVICE_TOKEN"], "identity-token")
            self.assertEqual(sandbox["INTERNAL_SERVICE_TOKEN"], "identity-token")
            self.assertEqual(langgraph["OPENAI_API_KEY"], "openai-key")
            self.assertEqual(litellm["OPENAI_API_KEY"], "openai-key")
            self.assertEqual(litellm["LANGFUSE_PUBLIC_KEY"], "legacy-public")
            self.assertEqual(litellm["LANGFUSE_SECRET_KEY"], "legacy-secret")
            self.assertEqual(
                langfuse["LANGFUSE_INIT_PROJECT_PUBLIC_KEY"], "legacy-public"
            )
            self.assertEqual(
                langfuse["LANGFUSE_INIT_PROJECT_SECRET_KEY"], "legacy-secret"
            )
            self.assertEqual(langfuse["NEXTAUTH_SECRET"], "nextauth-secret")
            self.assertEqual(langfuse["SALT"], "langfuse-salt")
            self.assertEqual(langfuse["ENCRYPTION_KEY"], "encryption-key")
            self.assertEqual(openwebui["OPENWEBUI_API_KEY"], "openwebui-key")
            self.assertEqual(openwebui["CORS_ORIGINS"], "https://idea.example")
            self.assertEqual(openwebui["WEBUI_ADMIN_EMAIL"], "")
            self.assertEqual(openwebui["WEBUI_ADMIN_PASSWORD"], "")
            self.assertEqual(len(langgraph["LANGGRAPH_AES_KEY"]), 32)

            for path in created:
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

            urls = (
                (langgraph["LANGGRAPH_DATABASE_URL"], "langgraph-db"),
                (litellm["LITELLM_DATABASE_URL"], "litellm-db"),
                (langfuse["DATABASE_URL"], "langfuse-db"),
            )
            for url, password in urls:
                parsed = urlparse(url)
                self.assertEqual(parsed.hostname, "db")
                self.assertEqual(unquote(parsed.password or ""), password)

    def test_does_nothing_without_legacy_root_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_root(directory)
            self.assertEqual(migration.migrate(root), [])


if __name__ == "__main__":
    unittest.main()
