import unittest
import sys
import types
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import pexpect  # noqa: F401
except ModuleNotFoundError:
    pexpect_stub = types.ModuleType("pexpect")
    pexpect_stub.TIMEOUT = TimeoutError
    pexpect_stub.EOF = EOFError
    pexpect_stub.spawn = Mock()
    sys.modules["pexpect"] = pexpect_stub

import terminal_registry as registry
from msb_sandbox import MicrosandboxTerminal


class StreamingStartupCancellationTests(unittest.TestCase):
    def setUp(self):
        with registry._registry_lock:
            registry._active_python_runs.clear()
            registry._cancelled_python_runs.clear()

    def tearDown(self):
        with registry._registry_lock:
            registry._active_python_runs.clear()
            registry._cancelled_python_runs.clear()

    @staticmethod
    def _terminal():
        terminal = MicrosandboxTerminal.__new__(MicrosandboxTerminal)
        terminal.run_python_stream = Mock(return_value=iter(()))
        terminal.interrupt_python = Mock(return_value=False)
        return terminal

    def test_stop_during_terminal_creation_prevents_python_submission(self):
        terminal = self._terminal()

        def create_terminal(_sandbox_id):
            self.assertTrue(registry.interrupt_run("sandbox-1", "run-1"))
            return terminal

        with patch.object(registry, "_get_terminal", side_effect=create_terminal):
            chunks = list(registry.run_python_stream(
                "while True: pass",
                sandbox_id="sandbox-1",
                kernel_id="kernel_1",
                run_id="run-1",
            ))

        self.assertEqual(chunks, [])
        terminal.run_python_stream.assert_not_called()

    def test_stop_after_terminal_creation_reaches_guest_and_callback(self):
        terminal = self._terminal()

        def stream(*args, **kwargs):
            self.assertTrue(registry.interrupt_run("sandbox-1", "run-1"))
            self.assertTrue(kwargs["cancelled"]())
            return iter(())

        terminal.run_python_stream.side_effect = stream
        with patch.object(registry, "_get_terminal", return_value=terminal):
            list(registry.run_python_stream(
                "while True: pass",
                sandbox_id="sandbox-1",
                kernel_id="kernel_1",
                run_id="run-1",
            ))

        terminal.interrupt_python.assert_not_called()


if __name__ == "__main__":
    unittest.main()
