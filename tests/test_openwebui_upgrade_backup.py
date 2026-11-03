import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
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
        (self.source / "cache").mkdir()
        (self.source / "cache/model.bin").write_bytes(b"regenerable")
        (self.source / "uploads").mkdir()
        (self.source / "uploads/file.txt").write_text("upload")
        (self.source / "vector_db").mkdir()
        (self.source / "vector_db/index").write_text("vectors")
        docker = self.bin / "docker"
        docker.write_text('''#!/usr/bin/env python3
import json, os, shutil, sys, tarfile
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
elif args[0] == 'stop':
    if os.environ.get('CHECKPOINT_ON_STOP') == '1':
        for name in ('webui.db-wal', 'webui.db-shm'):
            (Path(os.environ['FAKE_SOURCE']) / name).unlink(missing_ok=True)
elif args[0] == 'cp':
    if os.environ.get('COPY_FAILURE') == '1': sys.exit(1)
    with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as archive:
        archive.add(os.environ['FAKE_SOURCE'], arcname='.')
elif args[0] == 'exec':
    source = Path(os.environ['FAKE_SOURCE'])
    print(json.dumps({'entries': sorted(p.name for p in source.iterdir() if p.name != 'cache'),
                      'bytes': int(os.environ.get('SOURCE_BYTES', '10000'))}))
elif args[0] == 'compose':
    print(json.dumps({'services': {'openwebui': {'image': os.environ.get('TARGET_IMAGE', 'idea-openwebui:0.12.5-idea.0.11')}}, 'volumes': {'idea_openwebui_data': {'name': 'idea_idea_openwebui_data'}}}))
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
        self.assertEqual((backup / "data/uploads/file.txt").read_text(), "upload")
        self.assertEqual((backup / "data/vector_db/index").read_text(), "vectors")
        self.assertFalse((backup / "data/cache").exists())

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
        self.assertFalse(any(self.root.glob(".local-validation/**/backup-*")))

    def test_invalid_database_restarts_old_container(self):
        (self.source / "webui.db").write_bytes(b"not a SQLite database")
        result = self.run_backup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("start idea_openwebui", self.log())
        self.assertFalse(any(self.root.glob(".local-validation/**/backup-*")))

    def test_checkpoint_removing_wal_during_stop_does_not_break_backup(self):
        for name in ("webui.db-wal", "webui.db-shm"):
            (self.source / name).touch()
        result = self.run_backup(CHECKPOINT_ON_STOP="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(self.root.glob(".local-validation/**/VERIFIED")))

    def test_compose_image_change_triggers_backup(self):
        result = self.run_backup(CURRENT_IMAGE="idea-openwebui:0.12.5-idea.0.11",
                                 TARGET_IMAGE="idea-openwebui:future")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stop -t 30", self.log())

    def test_insufficient_space_aborts_before_stopping(self):
        result = self.run_backup(SOURCE_BYTES=str(10**20))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Insufficient disk space", result.stderr)
        self.assertNotIn("stop ", self.log())
        self.assertFalse(any(self.root.glob(".local-validation/**/backup-*")))

    def make_previous_backups(self):
        root = self.root / ".local-validation/openwebui-deploy-backups"
        for i in range(3):
            old = root / f"backup-old-{i}"
            old.mkdir(parents=True)
            (old / "VERIFIED").touch()
            os.utime(old, (time.time() - 100 + i, time.time() - 100 + i))
        return root

    def test_retention_keeps_only_new_verified_copy(self):
        root = self.make_previous_backups()
        unrelated = root / "manual-copy"
        unrelated.mkdir()
        result = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(root.glob("backup-*"))), 1)
        self.assertFalse(any(root.glob("backup-old-*")))
        self.assertTrue(next(root.glob("backup-*/VERIFIED")).is_file())
        self.assertTrue(unrelated.is_dir())

    def test_failed_backup_preserves_previous_copies(self):
        root = self.make_previous_backups()
        result = self.run_backup(COPY_FAILURE="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sorted(p.name for p in root.iterdir()),
                         [f"backup-old-{i}" for i in range(3)])

    def test_missing_container_with_existing_volume_aborts(self):
        result = self.run_backup(CONTAINER_MISSING="1", VOLUME_EXISTS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("data volume exists", result.stderr)

    def test_fresh_install_without_container_or_volume_continues(self):
        result = self.run_backup(CONTAINER_MISSING="1")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
