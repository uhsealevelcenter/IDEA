import ast
import asyncio
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lifecycle_recovery import require_native_pid_guard
from msb_sandbox import MicrosandboxTerminal
import offline_catalog_check


class LifecycleRecoveryTests(unittest.TestCase):
    def test_offline_check_rejects_live_process_before_opening_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            process = Path(directory) / '99999'
            process.mkdir()
            for command in (b'python3\0/app/worker.py\0', b'/bin/msb\0machine\0', b'/usr/bin/uvicorn\0main:app\0'):
                (process / 'cmdline').write_bytes(command)
                with self.assertRaisesRegex(RuntimeError, 'another API/SDK/VM'):
                    offline_catalog_check.assert_offline(Path(directory))
        with patch.object(offline_catalog_check, 'assert_offline', side_effect=RuntimeError('live')), \
                patch.object(offline_catalog_check.sqlite3, 'connect') as connect:
            with self.assertRaisesRegex(RuntimeError, 'live'):
                offline_catalog_check.main()
            connect.assert_not_called()

    def test_service_modules_never_import_python_sqlite(self):
        root = Path(__file__).resolve().parent
        for name in ("msb_sandbox.py", "lifecycle_recovery.py", "lifecycle_smoke.py", "terminal_registry.py", "main.py"):
            tree = ast.parse((root / name).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertFalse(any(alias.name == "sqlite3" for alias in node.names), name)
                if isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "sqlite3", name)

    def test_unpatched_native_sdk_is_rejected(self):
        for native in (types.SimpleNamespace(), types.SimpleNamespace(idea_pid_identity_guard=lambda: False)):
            module = types.ModuleType("microsandbox")
            module._microsandbox = native
            with patch.dict(sys.modules, {"microsandbox": module}):
                with self.assertRaisesRegex(RuntimeError, "patched Microsandbox"):
                    require_native_pid_guard()

    def test_patched_native_sdk_is_accepted(self):
        module = types.ModuleType("microsandbox")
        module._microsandbox = types.SimpleNamespace(idea_pid_identity_guard=lambda: True)
        with patch.dict(sys.modules, {"microsandbox": module}):
            require_native_pid_guard()

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
        for state in ("stopped", "running"):
            with self.subTest(refreshed_state=state):
                handle = types.SimpleNamespace(status="stopped", connect=AsyncMock(),
                                               shell=AsyncMock(return_value=types.SimpleNamespace(exit_code=0)))
                async def refresh():
                    if sandbox.start.await_count:
                        handle.status = "running" if state == "running" else "stopped"
                handle.refresh = refresh
                handle.connect.return_value = handle
                sandbox = types.SimpleNamespace(get=AsyncMock(return_value=handle),
                                                start=AsyncMock(side_effect=StillRunning))
                module = types.ModuleType("microsandbox")
                module.Sandbox = sandbox
                module.Image = None
                terminal = MicrosandboxTerminal.__new__(MicrosandboxTerminal)
                terminal.session_id = "alice"
                terminal._run = lambda factory: asyncio.run(factory())
                with patch.dict(sys.modules, {"microsandbox": module, "microsandbox.errors": errors}), \
                        patch("msb_sandbox.require_native_pid_guard"):
                    if state == "running":
                        terminal._connect_or_create()
                        handle.connect.assert_awaited_once()
                    else:
                        with self.assertRaises(StillRunning):
                            terminal._connect_or_create()
                        handle.connect.assert_not_awaited()
                    sandbox.start.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
