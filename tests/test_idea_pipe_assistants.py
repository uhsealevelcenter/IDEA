import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "openwebui"
    / "functions"
    / "idea_pipe.py"
)
SPEC = importlib.util.spec_from_file_location("idea_pipe", SCRIPT_PATH)
idea_pipe = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(idea_pipe)


class IdeaPipeAssistantTests(unittest.TestCase):
    def test_extracts_assistant_system_prompt_and_latest_user_message(self):
        messages = [
            {"role": "system", "content": "You are SEA."},
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Latest question"},
        ]

        self.assertEqual(
            idea_pipe._assistant_system_prompt(messages),
            "You are SEA.",
        )
        self.assertEqual(
            idea_pipe._latest_user_content(messages),
            "Latest question",
        )

    def test_reads_selected_assistant_from_openwebui_metadata(self):
        metadata = {"model": {"id": "mars-assistant", "name": "Mars Assistant"}}

        self.assertEqual(
            idea_pipe._selected_assistant_id(metadata),
            "mars-assistant",
        )
        self.assertIsNone(
            idea_pipe._selected_assistant_id(
                {"model": {"id": "idea-terminal-agent"}}
            )
        )
        self.assertIsNone(
            idea_pipe._selected_assistant_id(
                {"model": {"id": "idea_terminal_agent.idea-terminal-agent"}}
            )
        )

    def test_supports_multipart_user_text(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analyze this"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            }
        ]

        self.assertEqual(
            idea_pipe._latest_user_content(messages),
            "Analyze this",
        )

    def test_pipe_forwards_prompt_and_isolates_assistant_history(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.iter_lines.return_value = []
        body = {
            "messages": [
                {"role": "system", "content": "You are SEA."},
                {"role": "user", "content": "Analyze Honolulu."},
            ]
        }
        metadata = {
            "chat_id": "chat-123",
            "model": {"id": "sea", "name": "SEA"},
        }

        with patch.object(idea_pipe.requests, "post", return_value=response) as post:
            result = list(
                idea_pipe.Pipe().pipe(
                    body,
                    __user__={
                        "id": "user-1",
                        "email": "scientist@example.org",
                        "role": "user",
                    },
                    __metadata__=metadata,
                )
            )

        self.assertEqual(result, [])
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["assistant_id"], "sea")
        self.assertEqual(payload["assistant_system_prompt"], "You are SEA.")
        self.assertEqual(payload["session_key"], "user-1:chat-123:sea")
        self.assertEqual(payload["message"], "Analyze Honolulu.")


if __name__ == "__main__":
    unittest.main()
