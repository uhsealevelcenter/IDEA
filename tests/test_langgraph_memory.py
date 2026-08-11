import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "langgraph"))

from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from idea_graph.control import RunCancellation  # noqa: E402
from idea_graph import checkpoints  # noqa: E402
from idea_graph.graph import _requests_visual_context, build_idea_graph  # noqa: E402
from idea_graph.identities import derive_execution_identities  # noqa: E402
from idea_graph.memory import (  # noqa: E402
    bounded_records,
    compact_turn_messages,
    defined_names,
    execution_memory_block,
    safe_arguments,
)
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

    def call_model(self, messages, *, cancellation=None):
        self.model_inputs.append(messages)
        self.model_calls += 1
        if self.model_calls == 1:
            return AIMessage(
                content="",
                usage_metadata={
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                },
                tool_calls=[{
                    "id": "call-1",
                    "name": "run_python_tool",
                    "args": {"code": "values = [1, 2, 3]\nline = values"},
                    "type": "tool_call",
                }],
            )
        return AIMessage(
            content="Updated the previous plot.",
            usage_metadata={
                "input_tokens": 80,
                "output_tokens": 10,
                "total_tokens": 90,
            },
        )

    def persist_python_source(self, execution_id, code, state):
        return f"/workspace/.idea/{execution_id}.py"

    def execute_tool(self, tool_call, state):
        self.executed.append(tool_call)
        return ToolOutcome(
            content="[1 image generated]",
            artifacts=["/outputs/.idea/kernel-images/run-1-1.png"],
            vision_images=["/outputs/.idea/kernel-images/run-1-1.png"],
            kernel_namespace=[
                {"name": "values", "type": "list", "length": 3},
                {"name": "line", "type": "list", "length": 3},
            ],
        )

    def finalize(self, state):
        return []


class RepeatingRuntime(FakeRuntime):
    def __init__(self, finish_after=None):
        super().__init__()
        self.finish_after = finish_after

    def call_model(self, messages, *, cancellation=None):
        self.model_inputs.append(messages)
        self.model_calls += 1
        if self.finish_after and self.model_calls > self.finish_after:
            return AIMessage(content="Finished after repeated calls.")
        return AIMessage(
            content="",
            tool_calls=[{
                "id": f"call-{self.model_calls}",
                "name": "run_terminal_tool",
                "args": {"command": "same-command"},
                "type": "tool_call",
            }],
        )


class CodexRuntime(FakeRuntime):
    def call_model(self, messages, *, cancellation=None):
        self.model_inputs.append(messages)
        self.model_calls += 1
        if self.model_calls == 1:
            return AIMessage(content="", tool_calls=[{
                "id": "codex-call-1",
                "name": "delegate_to_codex",
                "args": {
                    "task": "Review the parser",
                    "cwd": "/workspace/repo",
                    "access": "read-only",
                },
                "type": "tool_call",
            }])
        return AIMessage(content="Codex review complete.")

    def execute_tool(self, tool_call, state):
        return ToolOutcome(
            content="Reviewed parser.py",
            metadata={
                "codex_cwd": "/workspace/repo",
                "codex_thread_id": "codex-thread-123",
                "codex_usage": {"total_tokens": 42},
            },
        )


class LangGraphMemoryTests(unittest.TestCase):
    def test_codex_thread_and_usage_are_checkpointed(self):
        runtime = CodexRuntime()
        graph = build_idea_graph(runtime, checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "idea-thread-codex"}}

        result = graph.invoke({
            "conversation_messages": [{"role": "user", "content": "Review it"}],
            "run_id": "run-codex",
            "thread_id": "idea-thread-codex",
            "workspace_id": "workspace-1",
            "kernel_id": "kernel-1",
        }, config=config)

        self.assertEqual(
            result["codex_threads"]["/workspace/repo"],
            "codex-thread-123",
        )
        self.assertEqual(result["codex_usage"][-1]["usage"]["total_tokens"], 42)

    def test_visual_context_requires_a_reference_or_followup_action(self):
        self.assertTrue(
            _requests_visual_context("Please change it to black and white.")
        )
        self.assertTrue(_requests_visual_context("Describe what you see."))
        self.assertTrue(_requests_visual_context("Improve the plot layout."))
        self.assertFalse(_requests_visual_context("Please plot the trend of ONI."))
        self.assertFalse(_requests_visual_context("Create another chart."))

    def test_execution_memory_is_newest_first_and_byte_bounded(self):
        state = {
            "python_executions": [
                {
                    "execution_id": f"exec-{index}",
                    "status": "completed",
                    "submitted_code": "value = " + (str(index) * 200),
                }
                for index in range(10)
            ]
        }

        block = execution_memory_block(state, recent=8, max_bytes=500)

        self.assertLessEqual(len(block.encode("utf-8")), 500)
        self.assertIn("exec-9", block)
        self.assertNotIn("exec-1", block)

    def test_checkpoint_ledgers_keep_newest_records_within_bounds(self):
        records = [
            {"execution_id": f"exec-{index}", "value": "x" * 80}
            for index in range(10)
        ]

        bounded = bounded_records(records, max_count=5, max_bytes=300)

        self.assertEqual(bounded[-1]["execution_id"], "exec-9")
        self.assertNotIn("exec-0", {item["execution_id"] for item in bounded})
        self.assertLessEqual(
            len(json.dumps(bounded).encode("utf-8")),
            300,
        )

    def test_older_tool_observations_are_compacted_but_recent_are_preserved(self):
        messages = [
            ToolMessage(content="old-" + "x" * 100, tool_call_id="old"),
            ToolMessage(content="new-" + "y" * 100, tool_call_id="new"),
        ]

        compacted = compact_turn_messages(
            messages,
            observation_bytes=30,
            keep_recent_tools=1,
        )

        self.assertIn("compacted", compacted[0].content)
        self.assertEqual(compacted[1].content, messages[1].content)

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

    def test_prior_execution_summary_replaces_exact_code_in_next_turn(self):
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
        self.assertNotIn("values = [1, 2, 3]", text)
        self.assertIn("values: list len=3", text)
        self.assertIn("source path:", text)
        self.assertIn("make the line red", text)

    def test_generated_plot_becomes_active_vision_for_visual_followup(self):
        runtime = FakeRuntime()
        graph = build_idea_graph(runtime, checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "thread-vision"}}
        first = graph.invoke({
            "conversation_messages": [{"role": "user", "content": "plot values"}],
            "run_id": "run-1", "thread_id": "thread-vision",
            "workspace_id": "workspace-1", "kernel_id": "kernel-1",
        }, config=config)

        image_path = "/outputs/.idea/kernel-images/run-1-1.png"
        self.assertEqual(first["vision_images"], [image_path])
        self.assertTrue(first["active_artifact_id"].startswith("artifact_"))
        self.assertEqual(first["artifacts"][-1]["path"], image_path)

        runtime.model_calls = 1
        second = graph.invoke({
            "conversation_messages": [
                {"role": "user", "content": "plot values"},
                {"role": "assistant", "content": "done"},
                {"role": "user", "content": "Improve the plot layout"},
            ],
            "run_id": "run-2", "thread_id": "thread-vision",
            "workspace_id": "workspace-1", "kernel_id": "kernel-1",
        }, config=config)

        self.assertEqual(second["vision_images"], [image_path])

    def test_new_plot_request_does_not_receive_the_active_image(self):
        runtime = FakeRuntime()
        graph = build_idea_graph(runtime, checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "thread-new-plot"}}
        graph.invoke({
            "conversation_messages": [{"role": "user", "content": "plot values"}],
            "run_id": "run-1", "thread_id": "thread-new-plot",
            "workspace_id": "workspace-1", "kernel_id": "kernel-1",
        }, config=config)

        runtime.model_calls = 1
        result = graph.invoke({
            "conversation_messages": [
                {"role": "user", "content": "plot values"},
                {"role": "assistant", "content": "done"},
                {"role": "user", "content": "Please plot the trend of ONI."},
            ],
            "run_id": "run-2", "thread_id": "thread-new-plot",
            "workspace_id": "workspace-1", "kernel_id": "kernel-1",
        }, config=config)

        self.assertEqual(result["vision_images"], [])

    def test_model_usage_is_recorded_per_call_and_emitted(self):
        runtime = FakeRuntime()
        graph = build_idea_graph(runtime)
        result = graph.invoke({
            "conversation_messages": [{"role": "user", "content": "plot values"}],
            "run_id": "run-usage", "thread_id": "thread-usage",
            "workspace_id": "workspace-1", "kernel_id": "kernel-1",
        })

        current = [
            item for item in result["model_usage"]
            if item["run_id"] == "run-usage"
        ]
        self.assertEqual(len(current), 2)
        self.assertEqual(sum(item["total_tokens"] for item in current), 210)
        usage_events = [
            event for event in runtime.events
            if isinstance(event, dict) and event.get("type") == "model_usage"
        ]
        self.assertEqual(len(usage_events), 2)

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

    def test_iteration_limit_saves_resumable_continuation(self):
        saver = InMemorySaver()
        runtime = RepeatingRuntime()
        graph = build_idea_graph(runtime, checkpointer=saver, max_iterations=2)
        config = {"configurable": {"thread_id": "thread-limit"}}
        result = graph.invoke({
            "conversation_messages": [{
                "role": "user", "content": "Create a PDF report"
            }],
            "run_id": "run-limit", "thread_id": "thread-limit",
            "workspace_id": "workspace-1", "kernel_id": "kernel-1",
        }, config={**config, "recursion_limit": 50})

        self.assertEqual(result["final_status"], "stopped")
        self.assertEqual(result["continuation"]["reason"], "iteration_limit")
        self.assertEqual(result["objective"], "Create a PDF report")
        self.assertEqual(result["plan"][0]["status"], "deferred")

        resumed_runtime = RepeatingRuntime(finish_after=0)
        resumed_runtime.call_model = lambda messages, cancellation=None: AIMessage(
            content="Resumed and finished."
        )
        resumed_graph = build_idea_graph(
            resumed_runtime, checkpointer=saver, max_iterations=2
        )
        resumed = resumed_graph.invoke({
            "conversation_messages": [
                {"role": "user", "content": "Create a PDF report"},
                {"role": "assistant", "content": result["final_response"]},
                {"role": "user", "content": "Please continue."},
            ],
            "run_id": "run-resume", "thread_id": "thread-limit",
            "workspace_id": "workspace-1", "kernel_id": "kernel-1",
        }, config=config)
        self.assertEqual(resumed["objective"], "Create a PDF report")
        self.assertEqual(resumed["final_status"], "completed")

    def test_identical_tool_loop_is_blocked_before_fourth_execution(self):
        runtime = RepeatingRuntime(finish_after=4)
        graph = build_idea_graph(runtime, max_iterations=8)
        result = graph.invoke({
            "conversation_messages": [{"role": "user", "content": "work"}],
            "run_id": "run-loop", "thread_id": "thread-loop",
            "workspace_id": "workspace-1", "kernel_id": "kernel-1",
        }, config={"recursion_limit": 100})

        self.assertEqual(len(runtime.executed), 3)
        self.assertIn("blocked", {a["status"] for a in result["completed_actions"]})

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
