import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, call, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "langgraph"))
sys.path.insert(0, str(ROOT / "sandbox_service"))

# The production image supplies langchain_core. These tests exercise the
# underlying functions directly, so keep them runnable in the repository's
# dependency-light host test environment with a minimal decorator stub.
try:
    import langchain_core.tools  # noqa: F401
except ModuleNotFoundError:
    langchain_core = ModuleType("langchain_core")
    langchain_tools = ModuleType("langchain_core.tools")
    langchain_tools.tool = lambda function: function
    langchain_core.tools = langchain_tools
    sys.modules["langchain_core"] = langchain_core
    sys.modules["langchain_core.tools"] = langchain_tools

from tools import persistent_terminal  # noqa: E402
from msb_sandbox import MicrosandboxTerminal  # noqa: E402


class TerminalOutputArchivingTests(unittest.TestCase):
    @staticmethod
    def _response(output):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "success": True,
            "output": output,
            "elapsed_time": 0,
        }
        return response

    def test_complete_inline_output_is_not_archived(self):
        output = "HTML parsed successfully\nLocal dependencies: []"

        with (
            patch.object(
                persistent_terminal._client,
                "post",
                return_value=self._response(output),
            ),
            patch.object(persistent_terminal, "write_file") as write_file,
        ):
            result = persistent_terminal.run_terminal(
                "python build_report.py", session_id="user-1"
            )

        self.assertIn(output, result)
        self.assertNotIn("Full output saved to:", result)
        self.assertNotIn("Failed to save full output", result)
        write_file.assert_not_called()

    def test_truncated_output_is_archived(self):
        output = "\n".join(f"line {index}" for index in range(25))

        with (
            patch.object(
                persistent_terminal._client,
                "post",
                return_value=self._response(output),
            ),
            patch.object(
                persistent_terminal,
                "write_file",
                return_value="✓ Wrote output",
            ) as write_file,
        ):
            result = persistent_terminal.run_terminal(
                "python noisy_script.py", session_id="user-1"
            )

        self.assertIn("Output truncated", result)
        self.assertIn("Full output saved to:", result)
        write_file.assert_called_once()
        args, kwargs = write_file.call_args
        self.assertEqual(args[1], output)
        self.assertEqual(kwargs, {"session_id": "user-1"})


class MicrosandboxWriteFileTests(unittest.TestCase):
    def make_terminal(self):
        terminal = MicrosandboxTerminal.__new__(MicrosandboxTerminal)
        terminal._cwd = "/workspace"
        terminal._sandbox = SimpleNamespace(
            shell=Mock(return_value=SimpleNamespace(stdout_text="")),
            fs=SimpleNamespace(write=Mock()),
        )
        terminal._exec = Mock(side_effect=lambda operation: operation())
        return terminal

    def test_write_creates_missing_parent_directory(self):
        terminal = self.make_terminal()

        terminal.write_file("/outputs/report/index.html", "hello")

        self.assertEqual(
            terminal._sandbox.shell.call_args_list,
            [call("mkdir -p -- /outputs/report")],
        )
        terminal._sandbox.fs.write.assert_called_once_with(
            "/outputs/report/index.html", b"hello"
        )

    def test_relative_write_creates_resolved_parent_directory(self):
        terminal = self.make_terminal()

        terminal.write_file("reports/daily.txt", "hello")

        self.assertEqual(
            terminal._sandbox.shell.call_args_list,
            [call("mkdir -p -- /workspace/reports")],
        )
        terminal._sandbox.fs.write.assert_called_once_with(
            "/workspace/reports/daily.txt", b"hello"
        )


if __name__ == "__main__":
    unittest.main()
