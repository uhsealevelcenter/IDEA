import base64
import hashlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


LANGGRAPH_DIR = Path(__file__).resolve().parents[1] / "langgraph"
sys.path.insert(0, str(LANGGRAPH_DIR))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from agents import terminal_agent  # noqa: E402
from idea_graph.runtime import TerminalGraphRuntime  # noqa: E402
from tools import persistent_terminal  # noqa: E402


PNG_BYTES = b"\x89PNG\r\n\x1a\nvision-test"
JPEG_BYTES = b"\xff\xd8\xff\xe0vision-test"


class UploadedImageVisionTests(unittest.TestCase):
    def make_agent(self):
        agent = terminal_agent.TerminalAgent.__new__(
            terminal_agent.TerminalAgent
        )
        agent.sandbox_id = "user-1"
        return agent

    def test_detects_supported_image_magic(self):
        self.assertEqual(
            terminal_agent._detect_model_image_mime(PNG_BYTES),
            "image/png",
        )
        self.assertEqual(
            terminal_agent._detect_model_image_mime(JPEG_BYTES),
            "image/jpeg",
        )
        self.assertIsNone(
            terminal_agent._detect_model_image_mime(b"not-an-image"),
        )

    def test_builds_high_detail_multimodal_user_message(self):
        path = "/workspace/uploads/file-1/map.png"
        with patch.object(
            terminal_agent,
            "read_file_bytes",
            return_value=PNG_BYTES,
        ) as read:
            message = self.make_agent()._user_message_with_attached_images(
                "Describe the map.",
                [{
                    "name": "map.png",
                    "content_type": "image/png",
                    "size": len(PNG_BYTES),
                    "sandbox_path": path,
                }],
            )

        self.assertIsInstance(message, HumanMessage)
        image_parts = [
            part
            for part in message.content
            if part.get("type") == "image_url"
        ]
        self.assertEqual(len(image_parts), 1)
        image_url = image_parts[0]["image_url"]
        self.assertEqual(image_url["detail"], "high")
        prefix, encoded = image_url["url"].split(",", 1)
        self.assertEqual(prefix, "data:image/png;base64")
        self.assertEqual(base64.b64decode(encoded), PNG_BYTES)
        self.assertIn(path, str(message.content))
        read.assert_called_once_with(path, session_id="user-1")

    def test_non_image_attachment_remains_text_only(self):
        with patch.object(
            terminal_agent,
            "read_file_bytes",
        ) as read:
            message = self.make_agent()._user_message_with_attached_images(
                "Analyze the data.",
                [{
                    "name": "data.nc",
                    "content_type": "application/x-netcdf",
                    "size": 100,
                    "sandbox_path": "/workspace/uploads/file-1/data.nc",
                }],
            )

        self.assertEqual(message.content, "Analyze the data.")
        read.assert_not_called()

    def test_oversized_image_stays_available_without_loading_pixels(self):
        with (
            patch.object(terminal_agent, "VISION_MAX_IMAGE_BYTES", 10),
            patch.object(terminal_agent, "read_file_bytes") as read,
        ):
            message = self.make_agent()._user_message_with_attached_images(
                "Describe it.",
                [{
                    "name": "large.png",
                    "content_type": "image/png",
                    "size": 11,
                    "sandbox_path": "/workspace/uploads/file-1/large.png",
                }],
            )

        self.assertIsInstance(message.content, str)
        self.assertIn("was not included in model vision", message.content)
        read.assert_not_called()

    def test_multimodal_log_summary_omits_data_uri(self):
        message = HumanMessage(content=[
            {"type": "text", "text": "Describe it"},
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,PRIVATE-IMAGE-DATA",
                    "detail": "high",
                },
            },
        ])

        summary = terminal_agent._safe_message_log_content(message)

        self.assertIn("1 image(s)", summary)
        self.assertNotIn("PRIVATE-IMAGE-DATA", summary)


class InspectImageToolTests(unittest.TestCase):
    def test_factory_exposes_separate_inspect_and_show_tools(self):
        with patch.object(
            persistent_terminal,
            "file_exists",
            return_value=True,
        ):
            tools = persistent_terminal.make_agent_tools("user-1")
            by_name = {item.name: item for item in tools}

            self.assertIn("show_image_tool", by_name)
            self.assertIn("inspect_image_tool", by_name)
            self.assertEqual(
                by_name["inspect_image_tool"].invoke({
                    "filepath": "/workspace/figure.png",
                }),
                "✓ Image ready for model inspection: /workspace/figure.png",
            )

    def test_inspected_image_is_added_after_all_tool_results(self):
        inspect_tool = Mock()
        inspect_tool.invoke.return_value = (
            "✓ Image ready for model inspection: /workspace/figure.png"
        )
        first_response = AIMessage(
            content="",
            tool_calls=[{
                "name": "inspect_image_tool",
                "args": {"filepath": "/workspace/figure.png"},
                "id": "inspect-call",
                "type": "tool_call",
            }],
        )
        second_response = AIMessage(content="The figure contains a red line.")
        llm = Mock()
        llm.invoke.side_effect = [first_response, second_response]

        agent = terminal_agent.TerminalAgent.__new__(
            terminal_agent.TerminalAgent
        )
        agent.sandbox_id = "test-user"
        agent._shown_image_hashes = set()
        agent.assistant_id = None
        agent.assistant_system_prompt = None
        agent.builtin_skill_loader = Mock()
        agent.builtin_skill_loader.render_manifest.return_value = ""
        agent.inspect_image_tool = inspect_tool
        agent.tools_by_name = {"inspect_image_tool": inspect_tool}
        agent.llm = llm
        agent.max_iterations = 3
        agent._sync_outputs_to_openwebui = Mock(return_value=[])
        model_part = {
            "type": "image_url",
            "image_url": {
                "url": "data:image/png;base64,PRIVATE-IMAGE-DATA",
                "detail": "high",
            },
        }
        agent._model_image_part = Mock(return_value=model_part)

        output = io.StringIO()
        with (
            patch.object(
                terminal_agent,
                "list_file_metadata",
                return_value={},
            ),
            patch("sys.stdout", output),
        ):
            result = agent.run("Inspect the generated figure.")

        second_messages = llm.invoke.call_args_list[1].args[0]
        tool_index = next(
            index
            for index, message in enumerate(second_messages)
            if isinstance(message, ToolMessage)
        )
        vision_index = next(
            index
            for index, message in enumerate(second_messages)
            if isinstance(message, HumanMessage)
            and isinstance(message.content, list)
        )
        self.assertLess(tool_index, vision_index)
        self.assertEqual(
            second_messages[vision_index].content[-1],
            model_part,
        )
        self.assertNotIn("PRIVATE-IMAGE-DATA", output.getvalue())
        self.assertEqual(
            result["final_response"],
            "The figure contains a red line.",
        )


class LangGraphShowImageTests(unittest.TestCase):
    def make_runtime(self):
        show_tool = Mock()
        show_tool.invoke.return_value = "✓ Image ready to display: /outputs/plot.png"
        agent = Mock()
        agent.tools_by_name = {"show_image_tool": show_tool}
        agent._encode_image_to_base64.return_value = ("BASE64-PLOT", "png")
        agent._shown_image_hashes = set()

        runtime = TerminalGraphRuntime.__new__(TerminalGraphRuntime)
        runtime.agent = agent
        runtime.event_callback = Mock()
        runtime.displayed_image_paths = set()
        return runtime, agent, show_tool

    def test_show_image_tool_emits_a_lightweight_file_reference(self):
        runtime, agent, show_tool = self.make_runtime()

        outcome = runtime.execute_tool(
            {
                "name": "show_image_tool",
                "args": {"filepath": "/outputs/plot.png"},
            },
            {},
        )

        self.assertEqual(outcome.status, "completed")
        show_tool.invoke.assert_called_once_with({"filepath": "/outputs/plot.png"})
        agent._encode_image_to_base64.assert_called_once_with("/outputs/plot.png")
        runtime.event_callback.assert_called_once_with({
            "role": "assistant",
            "type": "image",
            "format": "png",
            "filename": "/outputs/plot.png",
            "start": True,
            "end": True,
        })
        self.assertEqual(runtime.displayed_image_paths, {"/outputs/plot.png"})

    def test_show_image_tool_deduplicates_identical_content(self):
        runtime, agent, _ = self.make_runtime()
        digest = hashlib.sha256(b"BASE64-PLOT").hexdigest()
        agent._shown_image_hashes.add(digest)

        outcome = runtime.execute_tool(
            {
                "name": "show_image_tool",
                "args": {"filepath": "/outputs/plot.png"},
            },
            {},
        )

        self.assertIn("already displayed", outcome.content)
        runtime.event_callback.assert_not_called()

    def test_workspace_image_also_references_default_published_path(self):
        runtime, _, show_tool = self.make_runtime()
        show_tool.invoke.return_value = (
            "✓ Image ready to display: /workspace/plots/plot.png"
        )

        outcome = runtime.execute_tool(
            {
                "name": "show_image_tool",
                "args": {"filepath": "/workspace/plots/plot.png"},
            },
            {},
        )

        self.assertEqual(outcome.status, "completed")
        self.assertEqual(runtime.displayed_image_paths, {
            "/workspace/plots/plot.png",
            "/outputs/plots/plot.png",
        })


class LangGraphKernelImageTests(unittest.TestCase):
    def make_runtime(self):
        runtime = TerminalGraphRuntime.__new__(TerminalGraphRuntime)
        runtime.agent = Mock()
        runtime.agent.sandbox_id = "sandbox-1"
        runtime.event_callback = Mock()
        runtime.outputs_dir = "/outputs"
        runtime.displayed_image_paths = set()
        return runtime

    def test_pending_generated_image_is_supplied_to_model_vision(self):
        runtime = self.make_runtime()
        runtime.system_prompt = "test prompt"
        runtime.agent._model_image_part.return_value = {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,PRIVATE", "detail": "high"},
        }

        messages = runtime.model_messages({
            "run_id": "run-1",
            "conversation_messages": [],
            "turn_messages": [],
            "vision_images": ["/outputs/.idea/kernel-images/run-1-1.png"],
            "vision_consumed_count": 0,
        })

        vision_message = next(
            message for message in messages
            if isinstance(message, HumanMessage) and isinstance(message.content, list)
        )
        self.assertIn("IDEA supplied an image", vision_message.content[0]["text"])
        self.assertEqual(vision_message.content[1]["type"], "image_url")

    def test_system_prompt_has_an_explicit_cache_breakpoint(self):
        runtime = self.make_runtime()
        runtime.system_prompt = "stable IDEA instructions"
        runtime.cacheable_system_message = terminal_agent._cacheable_system_message

        messages = runtime.model_messages({
            "conversation_messages": [
                {"role": "user", "content": "changing request"},
            ],
            "turn_messages": [],
        })

        self.assertEqual(messages[0].content, [{
            "type": "text",
            "text": "stable IDEA instructions",
            "prompt_cache_breakpoint": {"mode": "explicit"},
        }])

    def test_prompt_cache_key_is_stable_and_privacy_preserving(self):
        first = terminal_agent._prompt_cache_key("gpt-5.6-terra", "session-secret")
        second = terminal_agent._prompt_cache_key("gpt-5.6-terra", "session-secret")

        self.assertEqual(first, second)
        self.assertTrue(first.startswith("idea:"))
        self.assertNotIn("session-secret", first)
        self.assertNotEqual(
            first,
            terminal_agent._prompt_cache_key("gpt-5.6-terra", "other-session"),
        )

    @patch("tools.persistent_terminal.run_python")
    def test_namespace_inspection_returns_structure_without_values(self, run_python):
        run_python.return_value = [{
            "type": "console",
            "format": "output",
            "content": (
                "__IDEA_KERNEL_NAMESPACE_V1__:"
                '[{"name":"df","type":"DataFrame","shape":[3,2],"length":null}]\n'
            ),
        }]

        summary = persistent_terminal.inspect_python_namespace(
            session_id="sandbox-1",
            kernel_id="kernel-1",
            names=["df", "secret_value"],
            run_id="run-1",
        )

        self.assertEqual(summary[0]["shape"], [3, 2])
        self.assertNotIn("secret", str(summary[0]))
        submitted_code = run_python.call_args.args[0]
        compile(submitted_code, "<namespace-inspection>", "exec")

    @patch(
        "tools.persistent_terminal.inspect_python_namespace",
        return_value=[{"name": "df", "type": "DataFrame", "shape": [3, 2]}],
    )
    @patch("tools.persistent_terminal.write_file_stream")
    @patch("tools.persistent_terminal.run_python")
    def test_python_image_is_persisted_and_emitted_as_a_file_reference(
        self, run_python, write_file_stream, inspect_python_namespace
    ):
        run_python.return_value = [{
            "type": "image",
            "format": "base64.png",
            "content": base64.b64encode(PNG_BYTES).decode(),
        }]
        runtime = self.make_runtime()

        outcome = runtime.execute_tool(
            {"name": "run_python_tool", "args": {"code": "plt.show()"}},
            {"run_id": "run-1", "kernel_id": "kernel-1"},
        )

        image_path = "/outputs/.idea/kernel-images/run-1-1.png"
        self.assertEqual(outcome.status, "completed")
        self.assertIn("1 image(s) generated", outcome.content)
        self.assertEqual(outcome.artifacts, [image_path])
        self.assertEqual(outcome.vision_images, [image_path])
        self.assertEqual(
            outcome.kernel_namespace,
            [{"name": "df", "type": "DataFrame", "shape": [3, 2]}],
        )
        inspect_python_namespace.assert_called_once_with(
            session_id="sandbox-1",
            kernel_id="kernel-1",
            names=[],
            run_id="run-1",
        )
        write_file_stream.assert_called_once_with(
            image_path,
            [PNG_BYTES],
            session_id="sandbox-1",
            expected_size=len(PNG_BYTES),
        )
        self.assertEqual(runtime.displayed_image_paths, {image_path})
        emitted = [call.args[0] for call in runtime.event_callback.call_args_list]
        self.assertIn({
            "role": "assistant",
            "type": "image",
            "format": "png",
            "filename": image_path,
            "start": True,
            "end": True,
        }, emitted)
        self.assertNotIn("base64", str(emitted))
        self.assertNotIn(PNG_BYTES.decode("latin1"), str(emitted))

    @patch("tools.persistent_terminal.write_file_stream")
    @patch("tools.persistent_terminal.run_python")
    def test_invalid_python_image_never_falls_back_to_inline_base64(
        self, run_python, write_file_stream
    ):
        run_python.return_value = [{
            "type": "image",
            "format": "base64.png",
            "content": "not-base64!",
        }]
        runtime = self.make_runtime()

        outcome = runtime.execute_tool(
            {"name": "run_python_tool", "args": {"code": "plt.show()"}},
            {"run_id": "run-2", "kernel_id": "kernel-2"},
        )

        self.assertEqual(outcome.status, "failed")
        write_file_stream.assert_not_called()
        emitted = [call.args[0] for call in runtime.event_callback.call_args_list]
        self.assertNotIn("not-base64!", str(emitted))
        self.assertTrue(any(
            isinstance(event, dict)
            and "Could not save Python image" in str(event.get("content"))
            for event in emitted
        ))


if __name__ == "__main__":
    unittest.main()
