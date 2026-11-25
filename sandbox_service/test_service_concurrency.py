import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, call, patch

from fastapi import HTTPException, Request

SERVICE_DIR = Path(__file__).resolve().parent
registry_stub = types.ModuleType("terminal_registry")
registry_stub.run_python = lambda *args, **kwargs: {"chunks": []}
registry_stub.run_python_stream = lambda *args, **kwargs: iter(())
registry_stub.interrupt_run = lambda *args, **kwargs: False
registry_stub.stop_all_terminals = lambda: 0
registry_stub.SandboxAdmissionError = type("SandboxAdmissionError", (RuntimeError,), {})
registry_stub.isolated_sandboxes_enabled = lambda: True
previous_registry = sys.modules.get("terminal_registry")
sys.modules["terminal_registry"] = registry_stub
try:
    spec = importlib.util.spec_from_file_location(
        "sandbox_service_main_under_test",
        SERVICE_DIR / "main.py",
    )
    sandbox_main = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(sandbox_main)
finally:
    if previous_registry is None:
        sys.modules.pop("terminal_registry", None)
    else:
        sys.modules["terminal_registry"] = previous_registry


class SandboxServiceConcurrencyTests(unittest.TestCase):
    def test_python_and_interrupt_calls_are_offloaded_from_event_loop(self):
        async def exercise():
            request = sandbox_main.RunPythonRequest(
                code="while True: pass",
                kernel_id="kernel-1",
                run_id="run-1",
            )
            with patch.object(
                sandbox_main.asyncio,
                "to_thread",
                new_callable=AsyncMock,
                side_effect=[{"chunks": []}, True],
            ) as to_thread:
                run_result = await sandbox_main.run_python(
                    "sandbox-1", request
                )
                interrupt_result = await sandbox_main.interrupt_run(
                    "sandbox-1", "run-1"
                )

            self.assertEqual(
                interrupt_result,
                {"ok": True, "interrupted": True},
            )
            self.assertEqual(run_result, {"chunks": []})
            self.assertEqual(
                to_thread.await_args_list,
                [
                    call(
                        sandbox_main.registry.run_python,
                        "while True: pass",
                        sandbox_id="sandbox-1",
                        kernel_id="kernel-1",
                        run_id="run-1",
                    ),
                    call(
                        sandbox_main.registry.interrupt_run,
                        "sandbox-1",
                        "run-1",
                    ),
                ],
            )

        asyncio.run(exercise())


class FileExplorerAuthorizationTests(unittest.TestCase):
    def setUp(self):
        tokens = patch.multiple(
            sandbox_main,
            INTERNAL_SERVICE_TOKEN="service-token",
            FILE_EXPLORER_TOKEN="file-token",
        )
        tokens.start()
        self.addCleanup(tokens.stop)

    def request(self, token, user_id=None):
        headers = [(b"authorization", f"Bearer {token}".encode()), (b"x-session-id", b"other")]
        if user_id is not None:
            headers.append((b"x-user-id", user_id.encode()))
        return Request({
            "type": "http",
            "headers": headers,
            "query_string": b"session_id=other&sandbox_id=other",
        })

    def test_file_explorer_uses_only_the_authenticated_user_id(self):
        for user_id in ("first", "second"):
            self.assertEqual(
                sandbox_main.get_request_sandbox_id(self.request("file-token", user_id)),
                user_id,
            )
        with self.assertRaises(HTTPException) as denied:
            sandbox_main.get_request_sandbox_id(self.request("file-token"))
        self.assertEqual(denied.exception.status_code, 403)

    def test_file_explorer_token_cannot_authorize_internal_operations(self):
        for internal_token in ("service-token", ""):
            with patch.object(sandbox_main, "INTERNAL_SERVICE_TOKEN", internal_token):
                with self.assertRaises(HTTPException) as denied:
                    sandbox_main.require_internal_token(self.request("file-token", "first"))
                self.assertEqual(denied.exception.status_code, 401)

    def test_authenticated_operations_require_an_isolated_backend(self):
        with patch.object(registry_stub, "isolated_sandboxes_enabled", return_value=False):
            for authorize, token in (
                (sandbox_main.get_request_sandbox_id, "file-token"),
                (sandbox_main.require_internal_token, "service-token"),
            ):
                with self.assertRaises(HTTPException) as unavailable:
                    authorize(self.request(token, "first"))
                self.assertEqual(unavailable.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
