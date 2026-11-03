import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "deployment/backup_openwebui_before_upgrade.sh"


class OpenWebUIUpgradeBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.source = self.root / "source"
        self.source.mkdir()
        with sqlite3.connect(self.source / "webui.db") as db:
            db.execute("CREATE TABLE example (value TEXT)")
            db.execute("INSERT INTO example VALUES ('preserved')")
        (self.source / "attachment.txt").write_text("preserved")
        docker = self.bin / "docker"
        docker.write_text('''#!/usr/bin/env python3
import json, os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['FAKE_DOCKER_LOG'], 'a') as out:
    out.write(' '.join(args) + '\\n')
if args[0] == 'inspect':
    if os.environ.get('CONTAINER_MISSING') == '1': sys.exit(1)
    fmt = args[args.index('--format') + 1] if '--format' in args else ''
    if '.Config.Image' in fmt: print(os.environ.get('CURRENT_IMAGE', 'idea-openwebui:old'))
    elif '.Mounts' in fmt: print('idea_idea_openwebui_data')
    elif '.State.Running' in fmt: print('true')
    elif '.Image' in fmt: print('sha256:previous')
elif args[0] == 'cp':
    if os.environ.get('COPY_FAILURE') == '1': sys.exit(1)
    shutil.copytree(os.environ['FAKE_SOURCE'], args[-1], dirs_exist_ok=True)
elif args[0] == 'compose':
    print(json.dumps({'volumes': {'idea_openwebui_data': {'name': 'idea_idea_openwebui_data'}}}))
elif args[0] == 'volume':
    sys.exit(0 if os.environ.get('VOLUME_EXISTS') == '1' else 1)
''')
        docker.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "FAKE_SOURCE": str(self.source),
            "FAKE_DOCKER_LOG": str(self.root / "docker.log"),
        }

    def run_backup(self, **env):
        return subprocess.run(
            ["bash", str(SCRIPT)], cwd=self.root, env={**self.env, **env},
            text=True, capture_output=True, check=False,
        )

    def log(self):
        return (self.root / "docker.log").read_text()

    def test_backup_preserves_data_and_verifies_database(self):
        result = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        backup = next((self.root / ".local-validation/openwebui-deploy-backups").iterdir())
        self.assertTrue((backup / "VERIFIED").is_file())
        self.assertEqual((backup / "data/attachment.txt").read_text(), "preserved")
        self.assertIn("stop -t 30 idea_openwebui", self.log())
        self.assertNotIn("start idea_openwebui", self.log())

    def test_current_image_does_not_stop_or_back_up(self):
        result = self.run_backup(CURRENT_IMAGE="idea-openwebui:0.12.5-idea.0.11")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("stop ", self.log())
        self.assertFalse((self.root / ".local-validation").exists())

    def test_failure_restarts_old_container(self):
        result = self.run_backup(COPY_FAILURE="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("start idea_openwebui", self.log())
        self.assertFalse(any(self.root.glob(".local-validation/**/VERIFIED")))

    def test_invalid_database_restarts_old_container(self):
        (self.source / "webui.db").write_bytes(b"not a SQLite database")
        result = self.run_backup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("start idea_openwebui", self.log())
        self.assertFalse(any(self.root.glob(".local-validation/**/VERIFIED")))

    def test_missing_container_with_existing_volume_aborts(self):
        result = self.run_backup(CONTAINER_MISSING="1", VOLUME_EXISTS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("data volume exists", result.stderr)

    def test_fresh_install_without_container_or_volume_continues(self):
        result = self.run_backup(CONTAINER_MISSING="1")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
