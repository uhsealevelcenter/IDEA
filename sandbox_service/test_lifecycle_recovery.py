import asyncio
import fcntl
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lifecycle_recovery as recovery
from msb_sandbox import MicrosandboxTerminal


class LifecycleRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        (self.home / "db").mkdir()
        self.database = self.home / "db/msb.db"
        with sqlite3.connect(self.database) as c:
            c.executescript('''
                CREATE TABLE sandbox(id INTEGER PRIMARY KEY,name TEXT,status TEXT);
                CREATE TABLE run(id INTEGER PRIMARY KEY,sandbox_id INTEGER,pid INTEGER,status TEXT);
                INSERT INTO sandbox VALUES(1,'alice','Stopped'),(2,'bob','Running');
                INSERT INTO run VALUES(1,1,123,'Terminated'),(2,2,456,'Running');
            ''')
        disk = self.home / "sandboxes/alice/upper.ext4"
        disk.parent.mkdir(parents=True)
        disk.write_bytes(b"existing user files")
        self.disk = disk
        self.env = patch.dict(os.environ, MSB_HOME=str(self.home))
        self.env.start()
        self.addCleanup(self.env.stop)
        version = patch.object(recovery.importlib.metadata, "version", return_value="0.7.2")
        version.start()
        self.addCleanup(version.stop)

    def rows(self):
        with sqlite3.connect(self.database) as c:
            return c.execute("SELECT * FROM run ORDER BY id").fetchall()

    def test_repairs_only_latest_terminated_pid_and_preserves_files(self):
        with patch.object(recovery, "_recorded_runtime_alive", return_value=False):
            self.assertTrue(recovery.clear_stale_runtime_pid("alice"))
            self.assertFalse(recovery.clear_stale_runtime_pid("alice"))
        self.assertEqual(self.rows(), [(1, 1, None, "Terminated"), (2, 2, 456, "Running")])
        self.assertEqual(self.disk.read_bytes(), b"existing user files")

    def test_live_matching_runtime_is_untouched(self):
        before = self.rows()
        with patch.object(recovery, "_recorded_runtime_alive", return_value=True):
            self.assertFalse(recovery.clear_stale_runtime_pid("alice"))
        self.assertEqual(self.rows(), before)

    def test_active_or_unfinished_runs_are_never_repaired(self):
        for state, run_state in (("Running", "Terminated"), ("Starting", "Terminated"),
                                 ("Stopped", "Running"), ("Draining", "Running")):
            with self.subTest(state=state, run_state=run_state):
                with sqlite3.connect(self.database) as c:
                    c.execute("UPDATE sandbox SET status=? WHERE id=1", (state,))
                    c.execute("UPDATE run SET status=? WHERE id=1", (run_state,))
                with patch.object(recovery, "_recorded_runtime_alive", return_value=False):
                    self.assertFalse(recovery.clear_stale_runtime_pid("alice"))
                self.assertEqual(self.rows()[0][2], 123)

    def test_latest_record_wins_over_older_terminated_record(self):
        with sqlite3.connect(self.database) as c:
            c.execute("INSERT INTO run VALUES(3,1,789,'Running')")
        with patch.object(recovery, "_recorded_runtime_alive", return_value=False):
            self.assertFalse(recovery.clear_stale_runtime_pid("alice"))
        self.assertEqual(self.rows()[0][2], 123)

    def test_native_transition_and_runtime_locks_exclude_repair(self):
        key = hashlib.sha256(b"alice").hexdigest()[:32]
        for directory in ("creation-locks", "locks"):
            with self.subTest(directory=directory):
                path = self.home / "run" / directory / (key + ".lock")
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a+b") as owner:
                    fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    with patch.object(recovery, "_recorded_runtime_alive", return_value=False):
                        self.assertFalse(recovery.clear_stale_runtime_pid("alice"))
                self.assertEqual(self.rows()[0][2], 123)

    def test_other_sdk_versions_do_not_mutate_private_schema(self):
        with patch.object(recovery.importlib.metadata, "version", return_value="0.8.0"):
            self.assertFalse(recovery.clear_stale_runtime_pid("alice"))
        self.assertEqual(self.rows()[0][2], 123)

    def test_pid_identity_distinguishes_threads_and_other_vms(self):
        root = self.home / "proc"
        process = root / "123"
        process.mkdir(parents=True)
        for tgid, args, alive in (
            (1, ["python", "uvicorn"], False),
            (123, ["python", "uvicorn"], False),
            (123, ["/bin/msb", "machine", "--name", "bob", "--sandbox-id", "2"], False),
            (123, ["/bin/msb", "machine", "--name", "alice", "--sandbox-id", "1"], True),
            (123, ["/bin/msb", "machine"], True),
        ):
            with self.subTest(args=args, tgid=tgid):
                (process / "status").write_text(f"Tgid:\t{tgid}\nState:\tS (sleeping)\n")
                (process / "cmdline").write_bytes(b"\0".join(s.encode() for s in args) + b"\0")
                self.assertEqual(recovery._recorded_runtime_alive(123, "alice", 1, proc_root=root), alive)
        self.assertFalse(recovery._recorded_runtime_alive(999, "alice", 1, proc_root=root))
        with patch.object(Path, "read_text", side_effect=PermissionError):
            self.assertTrue(recovery._recorded_runtime_alive(123, "alice", 1, proc_root=root))

    def test_failed_initialization_does_not_leak_background_thread(self):
        terminal = MicrosandboxTerminal.__new__(MicrosandboxTerminal)
        with patch.object(terminal, "_connect_or_create", side_effect=RuntimeError("boot failed")):
            with self.assertRaisesRegex(RuntimeError, "boot failed"):
                terminal.__init__("failed-init")
        self.assertFalse(terminal._thread.is_alive())
        self.assertTrue(terminal._loop.is_closed())

    def test_start_error_only_connects_after_refresh_proves_running(self):
        class StillRunning(Exception):
            pass
        errors = types.ModuleType("microsandbox.errors")
        errors.SandboxStillRunningError = StillRunning
        errors.SandboxNotFoundError = type("NotFound", (Exception,), {})
        for state in ("stopped", "running", "repaired"):
            with self.subTest(refreshed_state=state):
                handle = types.SimpleNamespace(status="stopped", connect=AsyncMock(),
                                               shell=AsyncMock(return_value=types.SimpleNamespace(exit_code=0)))
                async def refresh():
                    if sandbox.start.await_count:
                        handle.status = "running" if state == "running" else "stopped"
                handle.refresh = refresh
                handle.connect.return_value = handle
                sandbox = types.SimpleNamespace(get=AsyncMock(return_value=handle),
                                                start=AsyncMock(side_effect=[StillRunning(), handle]
                                                                if state == "repaired" else StillRunning))
                module = types.ModuleType("microsandbox")
                module.Sandbox = sandbox
                module.Image = None
                terminal = MicrosandboxTerminal.__new__(MicrosandboxTerminal)
                terminal.session_id = "alice"
                terminal._run = lambda factory: asyncio.run(factory())
                with patch.dict(sys.modules, {"microsandbox": module, "microsandbox.errors": errors}), \
                        patch("msb_sandbox.clear_stale_runtime_pid",
                              side_effect=[False, True] if state == "repaired" else [False, False]):
                    if state == "running":
                        terminal._connect_or_create()
                        handle.connect.assert_awaited_once()
                    elif state == "repaired":
                        terminal._connect_or_create()
                        handle.connect.assert_not_awaited()
                    else:
                        with self.assertRaises(StillRunning):
                            terminal._connect_or_create()
                        handle.connect.assert_not_awaited()
                    self.assertEqual(sandbox.start.await_count, 2 if state == "repaired" else 1)


if __name__ == "__main__":
    unittest.main()
