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


class BlockingPythonLLM:
    def __init__(self) -> None:
        self.started = threading.Event()

    async def astream(self, messages):
        yield AIMessageChunk(
            content="",
            tool_call_chunks=[{
                "name": "run_python_tool",
                "args": '{"code":"print(',
                "id": "call-blocked",
                "index": 0,
                "type": "tool_call_chunk",
            }],
        )
        self.started.set()
        await asyncio.Event().wait()


class DelayedLLM:
    async def astream(self, messages):
        await asyncio.sleep(0.25)
        yield AIMessageChunk(content="Done")


class RateLimitedLLM:
    async def astream(self, messages):
        raise RuntimeError("429 rate limit exceeded")
        if False:
            yield AIMessageChunk(content="")


class TimeoutThenSuccessLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def astream(self, messages):
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("Request timed out.")
        yield AIMessageChunk(content="Recovered")


class AlwaysTimeoutLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def astream(self, messages):
        self.calls += 1
        raise TimeoutError("Request timed out.")
        if False:
            yield AIMessageChunk(content="")


class PartialThenTimeoutLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def astream(self, messages):
        self.calls += 1
        yield AIMessageChunk(content="Partial response")
        raise TimeoutError("Request timed out.")


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
        self.assertEqual(events[2]["type"], "python_code_start")
        self.assertEqual(events[3]["type"], "python_code_delta")
        self.assertEqual(events[3]["content"], "pri")
        self.assertEqual(events[4]["type"], "python_code_delta")
        self.assertEqual(events[4]["content"], "nt(1)")
        self.assertEqual(events[5]["type"], "python_code_end")
        self.assertTrue(events[5]["complete"])

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

    def test_retries_a_timeout_before_any_output_and_reports_recovery(self):
        llm = TimeoutThenSuccessLLM()
        events = []
        runtime = TerminalGraphRuntime.__new__(TerminalGraphRuntime)
        runtime.agent = SimpleNamespace(
            llm=llm,
            model_request_timeout_seconds=180,
            model_max_retries=1,
        )
        runtime.event_callback = events.append

        response = runtime.call_model([])

        self.assertEqual(response.content, "Recovered")
        self.assertEqual(llm.calls, 2)
        self.assertEqual(events[0], {
            "type": "status",
            "phase": "model_retrying",
            "description": (
                "The model response timed out after 180 seconds; "
                "retrying (1/1)…"
            ),
            "done": False,
            "attempt": 1,
            "max_retries": 1,
        })

    def test_classifies_final_timeout_with_actionable_message(self):
        llm = AlwaysTimeoutLLM()
        events = []
        runtime = TerminalGraphRuntime.__new__(TerminalGraphRuntime)
        runtime.agent = SimpleNamespace(
            llm=llm,
            model_request_timeout_seconds=180,
            model_max_retries=1,
        )
        runtime.event_callback = events.append

        expected = (
            "The model did not respond within 180 seconds. Any completed "
            "tool operations were retained; please retry."
        )
        with self.assertRaisesRegex(RuntimeError, expected.replace(".", r"\.")):
            runtime.call_model([])

        self.assertEqual(llm.calls, 2)
        self.assertEqual(events[-1], {
            "type": "status",
            "phase": "model_timeout",
            "description": expected,
            "done": True,
            "error": True,
            "retryable": True,
        })

    def test_does_not_retry_after_partial_output_was_streamed(self):
        llm = PartialThenTimeoutLLM()
        events = []
        runtime = TerminalGraphRuntime.__new__(TerminalGraphRuntime)
        runtime.agent = SimpleNamespace(
            llm=llm,
            model_request_timeout_seconds=180,
            model_max_retries=1,
        )
        runtime.event_callback = events.append

        with self.assertRaisesRegex(RuntimeError, "after partial output"):
            runtime.call_model([])

        self.assertEqual(llm.calls, 1)
        self.assertEqual(events[0], "Partial response")
        self.assertEqual(events[-1]["phase"], "model_timeout")
        self.assertFalse(events[-1]["retryable"])

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

    def test_user_stop_closes_a_partial_python_code_stream(self):
        llm = BlockingPythonLLM()
        events = []
        runtime = TerminalGraphRuntime.__new__(TerminalGraphRuntime)
        runtime.agent = SimpleNamespace(llm=llm)
        runtime.event_callback = events.append
        cancellation = RunCancellation()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(
                runtime.call_model, [], cancellation=cancellation
            )
            self.assertTrue(llm.started.wait(timeout=1))
            cancellation.request("user_requested")
            with self.assertRaises(ModelCallCancelled):
                result.result(timeout=1)

        self.assertEqual(events[-1]["type"], "python_code_end")
        self.assertFalse(events[-1]["complete"])


if __name__ == "__main__":
    unittest.main()
