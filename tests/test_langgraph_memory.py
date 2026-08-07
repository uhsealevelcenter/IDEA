import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "langgraph"))

from langchain_core.messages import AIMessage  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from idea_graph.control import RunCancellation  # noqa: E402
from idea_graph import checkpoints  # noqa: E402
from idea_graph.graph import build_idea_graph  # noqa: E402
from idea_graph.identities import derive_execution_identities  # noqa: E402
from idea_graph.memory import defined_names, safe_arguments  # noqa: E402
from idea_graph.runtime import ToolOutcome  # noqa: E402


class FakeRuntime:
    def __init__(self):
        self.model_inputs = []
        self.events = []
        self.model_calls = 0
        self.executed = []

    def emit(self, chunk):
        self.events.append(chunk)

    def prepare(self, state):
        return {}

    def model_messages(self, state):
        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [SystemMessage(content="test")]
        for item in state.get("conversation_messages", []):
            messages.append(HumanMessage(content=str(item.get("content", ""))))
        messages.extend(state.get("turn_messages", []))
        from idea_graph.memory import execution_memory_block

        block = execution_memory_block(state)
        if block:
            messages.insert(1, SystemMessage(content=block))
        return messages

    def call_model(self, messages):
        self.model_inputs.append(messages)
        self.model_calls += 1
        if self.model_calls == 1:
            return AIMessage(
                content="",
                tool_calls=[{
                    "id": "call-1",
                    "name": "run_python_tool",
                    "args": {"code": "values = [1, 2, 3]\nline = values"},
                    "type": "tool_call",
                }],
            )
        return AIMessage(content="Updated the previous plot.")

    def persist_python_source(self, execution_id, code, state):
        return f"/workspace/.idea/{execution_id}.py"

    def execute_tool(self, tool_call, state):
        self.executed.append(tool_call)
        return ToolOutcome(content="[1 image generated]")

    def finalize(self, state):
        return []


class LangGraphMemoryTests(unittest.TestCase):
    def test_exact_python_source_is_checkpointed(self):
        runtime = FakeRuntime()
        graph = build_idea_graph(runtime, checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "thread-1"}}
        result = graph.invoke({
            "conversation_messages": [
                {"role": "user", "content": "plot three values"}
            ],
            "run_id": "run-1",
            "thread_id": "thread-1",
            "workspace_id": "workspace-1",
            "kernel_id": "kernel-1",
        }, config=config)

        self.assertEqual(result["final_status"], "completed")
        self.assertEqual(
            result["python_executions"][0]["submitted_code"],
            "values = [1, 2, 3]\nline = values",
        )
        self.assertEqual(result["python_executions"][0]["status"], "completed")
        snapshot = graph.get_state(config)
        self.assertEqual(
            snapshot.values["python_executions"][0]["defined_names"],
            ["line", "values"],
        )

    def test_prior_exact_code_is_in_next_turn_model_context(self):
        runtime = FakeRuntime()
        graph = build_idea_graph(runtime, checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "thread-2"}}
        graph.invoke({
            "conversation_messages": [{"role": "user", "content": "plot values"}],
            "run_id": "run-1", "thread_id": "thread-2",
            "workspace_id": "workspace-1", "kernel_id": "kernel-1",
        }, config=config)
        runtime.model_calls = 1  # Next invocation should finish without another tool.
        graph.invoke({
            "conversation_messages": [
                {"role": "user", "content": "plot values"},
                {"role": "assistant", "content": "done"},
                {"role": "user", "content": "make the line red"},
            ],
            "run_id": "run-2", "thread_id": "thread-2",
            "workspace_id": "workspace-1", "kernel_id": "kernel-1",
        }, config=config)
        text = "\n".join(str(message.content) for message in runtime.model_inputs[-1])
        self.assertIn("values = [1, 2, 3]", text)
        self.assertIn("make the line red", text)

    def test_stop_before_model_returns_summary(self):
        runtime = FakeRuntime()
        control = RunCancellation()
        control.request("user_requested")
        graph = build_idea_graph(runtime, cancellation=control)
        result = graph.invoke({
            "conversation_messages": [{"role": "user", "content": "work"}],
            "run_id": "run-stop", "thread_id": "thread-stop",
            "workspace_id": "workspace-1", "kernel_id": "kernel-1",
        })
        self.assertEqual(result["final_status"], "stopped")
        self.assertEqual(runtime.model_calls, 0)
        self.assertIn("Stopped at your request", result["final_response"])


class IdentityAndRedactionTests(unittest.TestCase):
    def setUp(self):
        self.previous = os.environ.get("IDEA_IDENTITY_SECRET")
        os.environ["IDEA_IDENTITY_SECRET"] = "test-secret"

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("IDEA_IDENTITY_SECRET", None)
        else:
            os.environ["IDEA_IDENTITY_SECRET"] = self.previous

    def test_chat_assistant_kernel_isolation(self):
        first = derive_execution_identities(
            user_id="u1", chat_id="c1", assistant_id="a1", run_id="r1"
        )
        second = derive_execution_identities(
            user_id="u1", chat_id="c2", assistant_id="a1", run_id="r2"
        )
        self.assertEqual(first.workspace_id, second.workspace_id)
        self.assertNotEqual(first.thread_id, second.thread_id)
        self.assertNotEqual(first.kernel_id, second.kernel_id)

    def test_anonymous_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            derive_execution_identities(
                user_id="anonymous", chat_id="c1", assistant_id=None, run_id="r1"
            )

    def test_python_and_credentials_are_redacted_from_generic_arguments(self):
        args = safe_arguments("run_python_tool", {
            "code": "secret_value = 1",
            "api_key": "secret",
            "options": {"authorization": "Bearer nested-secret"},
            "headers": [{"access_token": "nested-token"}],
        })
        self.assertTrue(args["code"].startswith("sha256:"))
        self.assertEqual(args["api_key"], "[redacted]")
        self.assertEqual(args["options"]["authorization"], "[redacted]")
        self.assertEqual(args["headers"][0]["access_token"], "[redacted]")
        self.assertEqual(defined_names("x = 1\ndef f():\n    pass"), ["f", "x"])


class CheckpointEncryptionTests(unittest.TestCase):
    def test_configured_serializer_encrypts_checkpoint_values(self):
        with patch.object(checkpoints, "LANGGRAPH_AES_KEY", "x" * 32):
            serializer = checkpoints._serializer()

        kind, encrypted = serializer.dumps_typed({"submitted_code": "private-code"})
        self.assertIn("+aes", kind)
        self.assertNotIn(b"private-code", encrypted)
        self.assertEqual(
            serializer.loads_typed((kind, encrypted)),
            {"submitted_code": "private-code"},
        )

    def test_invalid_encryption_key_length_fails_closed(self):
        with (
            patch.object(checkpoints, "LANGGRAPH_AES_KEY", "too-short"),
            self.assertRaisesRegex(ValueError, "16, 24, or 32"),
        ):
            checkpoints._serializer()


if __name__ == "__main__":
    unittest.main()
