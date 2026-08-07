import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "langgraph"))

from agents.terminal_agent import TerminalAgent  # noqa: E402
from idea_graph.runtime import TerminalGraphRuntime  # noqa: E402


class LangGraphPaperQAPrepareTests(unittest.TestCase):
    def test_turn_prepare_does_not_eagerly_prepare_paperqa(self):
        runtime = TerminalGraphRuntime.__new__(TerminalGraphRuntime)
        runtime.agent = SimpleNamespace(
            paperqa_enabled=True,
            attached_files=[{"type": "collection", "id": "literature"}],
            paperqa_scope_id=None,
            sandbox_id="user-1",
            _sync_inputs_from_openwebui=Mock(return_value=[]),
        )
        runtime.outputs_dir = "/outputs"
        runtime.outputs_before = None
        runtime.event_callback = Mock()

        with (
            patch(
                "utils.pqa.openwebui_library.prepare_paperqa_library",
                side_effect=AssertionError("PaperQA must remain lazy"),
            ) as prepare,
            patch("tools.persistent_terminal.list_file_metadata", return_value={}),
        ):
            runtime.prepare({})

        prepare.assert_not_called()
        self.assertIsNone(runtime.agent.paperqa_scope_id)

    def test_first_tool_use_prepares_and_caches_paperqa_scope(self):
        agent = TerminalAgent.__new__(TerminalAgent)
        agent.paperqa_enabled = True
        agent.paperqa_scope_id = None
        agent.paperqa_direct_scope_id = None
        agent.paperqa_direct_file_names = ()
        agent.user_id = "user-1"
        agent.assistant_id = "sea"
        agent.session_id = "user-1:chat-1"
        agent.attached_files = [{"type": "collection", "id": "literature"}]
        agent.openwebui_authorization = "Bearer current-user"
        library = SimpleNamespace(
            scope_id="collection-scope",
            direct_scope_id="direct-scope",
            direct_file_names=("supplement.pdf",),
        )

        with patch(
            "agents.terminal_agent.prepare_paperqa_library",
            return_value=library,
        ) as prepare:
            first = agent._ensure_paperqa_library()
            second = agent._ensure_paperqa_library()

        prepare.assert_called_once_with(
            user_id="user-1",
            assistant_id="sea",
            session_id="user-1:chat-1",
            resources=[{"type": "collection", "id": "literature"}],
            authorization="Bearer current-user",
        )
        self.assertEqual(first, "collection-scope")
        self.assertEqual(second, "collection-scope")
        self.assertEqual(agent.paperqa_direct_scope_id, "direct-scope")
        self.assertEqual(agent.paperqa_direct_file_names, ("supplement.pdf",))


if __name__ == "__main__":
    unittest.main()
