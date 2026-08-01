import base64
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


LANGGRAPH_DIR = Path(__file__).resolve().parents[1] / "langgraph"
sys.path.insert(0, str(LANGGRAPH_DIR))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from agents import terminal_agent  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
