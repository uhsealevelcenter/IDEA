import asyncio
import concurrent.futures
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessageChunk


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "langgraph"))

from idea_graph.control import RunCancellation  # noqa: E402
from idea_graph.runtime import ModelCallCancelled, TerminalGraphRuntime  # noqa: E402


class BlockingLLM:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.cancelled = threading.Event()

    async def astream(self, messages):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        if False:
            yield AIMessageChunk(content="")


class StreamingLLM:
    async def astream(self, messages):
        yield AIMessageChunk(content="Hello ")
        yield AIMessageChunk(content="world.")
        yield AIMessageChunk(
            content="",
            tool_call_chunks=[{
                "name": "run_python_tool",
                "args": '{"code":"pri',
                "id": "call-1",
                "index": 0,
                "type": "tool_call_chunk",
            }],
        )
        yield AIMessageChunk(
            content="",
            tool_call_chunks=[{
                "name": None,
                "args": 'nt(1)"}',
                "id": None,
                "index": 0,
                "type": "tool_call_chunk",
            }],
        )


class DelayedLLM:
    async def astream(self, messages):
        await asyncio.sleep(0.25)
        yield AIMessageChunk(content="Done")


class RateLimitedLLM:
    async def astream(self, messages):
        raise RuntimeError("429 rate limit exceeded")
        if False:
            yield AIMessageChunk(content="")


class ModelCancellationTests(unittest.TestCase):
    def test_streams_text_and_announces_tool_before_arguments_finish(self):
        events = []
        runtime = TerminalGraphRuntime.__new__(TerminalGraphRuntime)
        runtime.agent = SimpleNamespace(llm=StreamingLLM())
        runtime.event_callback = events.append

        response = runtime.call_model([])

        self.assertEqual(response.content, "Hello world.")
        self.assertEqual(
            response.tool_calls,
            [{
                "name": "run_python_tool",
                "args": {"code": "print(1)"},
                "id": "call-1",
                "type": "tool_call",
            }],
        )
        self.assertEqual(events[0], "Hello world.")
        self.assertEqual(
            events[1],
            {
                "type": "status",
                "phase": "preparing_tool",
                "description": "Preparing Python code…",
                "tool_name": "run_python_tool",
                "done": False,
            },
        )

    def test_reports_a_long_model_wait_before_tokens_arrive(self):
        events = []
        runtime = TerminalGraphRuntime.__new__(TerminalGraphRuntime)
        runtime.agent = SimpleNamespace(llm=DelayedLLM())
        runtime.event_callback = events.append

        with patch("idea_graph.runtime.MODEL_WAITING_STATUS_SECONDS", 0.01), patch(
            "idea_graph.runtime.MODEL_BUSY_STATUS_SECONDS", 0.02
        ):
            response = runtime.call_model([])

        self.assertEqual(response.content, "Done")
        self.assertIn(
            "Waiting for the model to respond…",
            [event.get("description") for event in events if isinstance(event, dict)],
        )
        self.assertIn(
            "Model is busy; still waiting…",
            [event.get("description") for event in events if isinstance(event, dict)],
        )

    def test_reports_rate_limit_as_a_terminal_status(self):
        events = []
        runtime = TerminalGraphRuntime.__new__(TerminalGraphRuntime)
        runtime.agent = SimpleNamespace(llm=RateLimitedLLM())
        runtime.event_callback = events.append

        with self.assertRaisesRegex(RuntimeError, "rate limit"):
            runtime.call_model([])

        self.assertEqual(events[-1], {
            "type": "status",
            "phase": "model_unavailable",
            "description": "Model capacity is temporarily limited; please retry",
            "done": True,
            "error": True,
        })

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
