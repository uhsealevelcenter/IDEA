import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


LANGGRAPH_DIR = Path(__file__).resolve().parents[1] / "langgraph"
sys.path.insert(0, str(LANGGRAPH_DIR))

from progress import (  # noqa: E402
    progress_chunk,
    tool_call_chunk_names,
    tool_status_description,
)


class AgentProgressTests(unittest.TestCase):
    def test_progress_chunk_contains_no_tool_arguments(self):
        chunk = progress_chunk(
            "preparing_tool",
            "Preparing a file…",
            tool_name="write_file_tool",
        )

        self.assertEqual(chunk["type"], "status")
        self.assertEqual(chunk["tool_name"], "write_file_tool")
        self.assertNotIn("args", chunk)
        self.assertNotIn("content", chunk)

    def test_extracts_tool_names_from_dict_and_object_chunks(self):
        chunk = SimpleNamespace(tool_call_chunks=[
            {"name": "write_file_tool", "args": '{"content":"secret"}'},
            SimpleNamespace(name="run_terminal_tool", args="private"),
            {"name": "write_file_tool", "args": "duplicate"},
            {"name": None, "args": "continuation"},
        ])

        self.assertEqual(
            tool_call_chunk_names(chunk),
            ["write_file_tool", "run_terminal_tool"],
        )

    def test_tool_descriptions_are_user_facing(self):
        self.assertEqual(
            tool_status_description("write_file_tool", preparing=True),
            "Preparing a file…",
        )
        self.assertEqual(
            tool_status_description("write_file_tool", preparing=False),
            "Writing a file…",
        )
        self.assertEqual(
            tool_status_description("unknown_tool", preparing=False),
            "Using a tool…",
        )


if __name__ == "__main__":
    unittest.main()
