import asyncio
import concurrent.futures
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "langgraph"))

from idea_graph.control import RunCancellation  # noqa: E402
from idea_graph.runtime import ModelCallCancelled, TerminalGraphRuntime  # noqa: E402


class BlockingLLM:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.cancelled = threading.Event()

    async def ainvoke(self, messages):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class ModelCancellationTests(unittest.TestCase):
    def test_in_flight_model_request_is_cancelled_at_user_stop(self):
        llm = BlockingLLM()
        runtime = TerminalGraphRuntime.__new__(TerminalGraphRuntime)
        runtime.agent = SimpleNamespace(llm=llm)
        runtime.event_callback = None
        cancellation = RunCancellation()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(
                runtime.call_model, [], cancellation=cancellation
            )
            self.assertTrue(llm.started.wait(timeout=1))
            cancellation.request("user_requested")
            with self.assertRaises(ModelCallCancelled):
                result.result(timeout=1)

        self.assertTrue(llm.cancelled.is_set())


if __name__ == "__main__":
    unittest.main()
