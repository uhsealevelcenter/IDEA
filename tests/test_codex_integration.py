import importlib.util
import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "interpreter_kernel" / "codex_runner.py"
SPEC = importlib.util.spec_from_file_location("idea_codex_runner", RUNNER_PATH)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)

sys.path.insert(0, str(ROOT / "langgraph"))
sys.path.insert(0, str(ROOT / "sandbox_service"))

try:
    import pexpect  # noqa: F401
except ModuleNotFoundError:
    pexpect_stub = types.ModuleType("pexpect")
    pexpect_stub.TIMEOUT = type("TIMEOUT", (Exception,), {})
    pexpect_stub.EOF = type("EOF", (Exception,), {})
    pexpect_stub.spawn = Mock()
    sys.modules["pexpect"] = pexpect_stub

from tools import persistent_terminal  # noqa: E402
import terminal_registry  # noqa: E402
from msb_sandbox import MicrosandboxTerminal  # noqa: E402


class CodexIntegrationTests(unittest.TestCase):
    def test_runner_rejects_escape_and_full_access(self):
        self.assertEqual(runner.validate_cwd("repo"), Path("/workspace/repo"))
        with self.assertRaisesRegex(ValueError, "/workspace"):
            runner.validate_cwd("/etc")
        with self.assertRaisesRegex(ValueError, "read-only"):
            runner.validate_access("full-access")

    def test_runner_serializes_sdk_notification_wrapper(self):
        event = SimpleNamespace(
            method="item/completed",
            payload={"item": {"type": "agentMessage", "text": "done"}},
        )
        summary = runner._event_summary(event)
        self.assertEqual(summary["method"], "item/completed")
        self.assertEqual(summary["payload"]["item"]["text"], "done")

    def test_runner_collects_async_sdk_turn(self):
        events = [
            SimpleNamespace(
                method="item/completed",
                payload={"item": {"type": "agentMessage", "text": "Review complete"}},
            ),
            SimpleNamespace(
                method="item/completed",
                payload={
                    "item": {
                        "type": "fileChange",
                        "changes": [{"path": "/workspace/repo/parser.py"}],
                    }
                },
            ),
            SimpleNamespace(
                method="thread/tokenUsage/updated",
                payload={"tokenUsage": {"total": {"totalTokens": 42}}},
            ),
            SimpleNamespace(
                method="turn/completed",
                payload={"turn": {"status": "completed", "error": None}},
            ),
        ]

        class FakeTurn:
            async def interrupt(self):
                return None

            async def stream(self):
                for event in events:
                    yield event

        class FakeThread:
            id = "codex-thread-1"

            async def turn(self, task):
                self.task = task
                return FakeTurn()

        class FakeAsyncCodex:
            instance = None

            def __init__(self, config):
                self.config = config
                self.closed = False
                FakeAsyncCodex.instance = self

            async def thread_start(self, **kwargs):
                self.start_kwargs = kwargs
                return FakeThread()

            async def thread_resume(self, thread_id, **kwargs):
                raise AssertionError("new request should not resume")

            async def close(self):
                self.closed = True

        class FakeCodexConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        fake_sdk = types.ModuleType("openai_codex")
        fake_sdk.AsyncCodex = FakeAsyncCodex
        fake_sdk.CodexConfig = FakeCodexConfig
        fake_sdk.ApprovalMode = SimpleNamespace(deny_all="deny_all")
        fake_sdk.Sandbox = SimpleNamespace(
            read_only="read-only", workspace_write="workspace-write"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with (
                patch.dict(sys.modules, {"openai_codex": fake_sdk}),
                patch.object(runner, "WORKSPACE_ROOT", workspace),
                patch.object(runner, "CODEX_HOME", workspace / ".idea" / "codex"),
            ):
                result = asyncio.run(runner._run({
                    "task": "Review the parser",
                    "cwd": str(workspace / "repo"),
                    "access": "read-only",
                    "model": "gpt-test",
                    "base_url": "https://llm.internal/v1",
                    "api_key": "scoped-secret",
                    "max_events": 10,
                }))

        self.assertTrue(result["ok"])
        self.assertEqual(result["thread_id"], "codex-thread-1")
        self.assertEqual(result["final_response"], "Review complete")
        self.assertEqual(result["changed_paths"], ["/workspace/repo/parser.py"])
        self.assertEqual(result["usage"]["total"]["totalTokens"], 42)
        self.assertTrue(FakeAsyncCodex.instance.closed)
        overrides = FakeAsyncCodex.instance.config.kwargs["config_overrides"]
        self.assertIn(
            'shell_environment_policy.filters.IDEA_CODEX_API_KEY="exclude"',
            overrides,
        )

    def test_langgraph_client_keeps_credentials_out_of_model_arguments(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True, "thread_id": "thread-1"}
        with (
            patch.object(persistent_terminal._client, "post", return_value=response) as post,
            patch.object(persistent_terminal, "IDEA_CODEX_API_KEY", "scoped-secret"),
            patch.object(persistent_terminal, "IDEA_CODEX_BASE_URL", "https://llm.internal/v1"),
        ):
            persistent_terminal.run_codex(
                "Review it", "user-1", cwd="/workspace/repo", run_id="run-1"
            )

        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["api_key"], "scoped-secret")
        self.assertNotIn("api_key", {"task": "Review it", "cwd": "/workspace/repo"})
        self.assertNotIn("scoped-secret", post.call_args.args[0])

    def test_registry_fails_closed_on_local_backend(self):
        with patch.object(terminal_registry, "_get_terminal", return_value=object()):
            result = terminal_registry.run_codex({"task": "review"}, "user-1")
        self.assertFalse(result["ok"])
        self.assertIn("microsandbox", result["error"])

    def test_guest_command_line_does_not_contain_api_key(self):
        terminal = MicrosandboxTerminal.__new__(MicrosandboxTerminal)
        output = SimpleNamespace(
            stdout_text=json.dumps({"ok": True, "thread_id": "thread-1"}),
            stderr_text="",
            exit_code=0,
        )
        terminal._sandbox = SimpleNamespace(
            fs=SimpleNamespace(write=Mock()),
            shell=Mock(return_value=output),
        )
        terminal._exec = Mock(side_effect=lambda operation, timeout=None: operation())
        terminal._exec_timeout = Mock(return_value=30.0)

        result = terminal.run_codex({
            "task": "review",
            "run_id": "run-1",
            "api_key": "scoped-secret",
        })

        self.assertTrue(result["ok"])
        commands = [call.args[0] for call in terminal._sandbox.shell.call_args_list]
        self.assertTrue(any("codex_runner.py" in command for command in commands))
        self.assertTrue(all("scoped-secret" not in command for command in commands))


if __name__ == "__main__":
    unittest.main()
