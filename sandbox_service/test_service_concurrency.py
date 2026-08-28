import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, call, patch

SERVICE_DIR = Path(__file__).resolve().parent
registry_stub = types.ModuleType("terminal_registry")
registry_stub.run_python = lambda *args, **kwargs: {"chunks": []}
registry_stub.run_python_stream = lambda *args, **kwargs: iter(())
registry_stub.interrupt_run = lambda *args, **kwargs: False
registry_stub.stop_all_terminals = lambda: 0
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


if __name__ == "__main__":
    unittest.main()
