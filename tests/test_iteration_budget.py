import io
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "langgraph"))

from langchain_core.messages import AIMessage, AIMessageChunk
from agents.terminal_agent import TerminalAgent
from iteration_budget import iteration_budget_message


class IterationBudgetTests(unittest.TestCase):
    def test_warning_thresholds_and_small_budgets(self):
        self.assertIsNone(iteration_budget_message(15, 20))
        self.assertIn("5 call(s) remain", iteration_budget_message(16, 20))
        self.assertIn("Wrap up now", iteration_budget_message(18, 20))
        self.assertIn("Use no tools", iteration_budget_message(20, 20))
        self.assertIn("Use no tools", iteration_budget_message(1, 1))
        self.assertIn("Wrap up now", iteration_budget_message(1, 2))

    def make_agent(self):
        agent = TerminalAgent.__new__(TerminalAgent)
        agent.model = "gpt-6.1-sol"
        agent.sandbox_id = "test-user"
        agent._shown_image_hashes = set()
        agent.assistant_id = None
        agent.assistant_system_prompt = None
        agent.builtin_skill_loader = Mock()
        agent.builtin_skill_loader.render_manifest.return_value = ""
        agent.llm = Mock()
        agent.max_iterations = 1
        agent._sync_outputs_to_openwebui = Mock(return_value=[])
        return agent

    def test_manual_runtime_check_in_and_failure_preserve_outputs(self):
        for stream in (False, True):
            for failure in (False, True):
                with self.subTest(stream=stream, failure=failure):
                    agent = self.make_agent()
                    final_llm = agent.llm.bind.return_value
                    text = "Saved the analysis; the report remains. Continue?"
                    final_llm.invoke.return_value = AIMessage(content=text)
                    final_llm.stream.return_value = [AIMessageChunk(content=text)]
                    if failure:
                        final_llm.invoke.side_effect = TimeoutError("timeout")

                        def failed_stream(_messages):
                            raise TimeoutError("timeout")
                            yield

                        final_llm.stream.side_effect = failed_stream
                    events = []
                    with (
                        patch("agents.terminal_agent.list_file_metadata", return_value={}),
                        patch("sys.stdout", io.StringIO()),
                    ):
                        result = agent.run("Create a report", events.append if stream else None)
                    agent.llm.bind.assert_called_once_with(tool_choice="none")
                    agent._sync_outputs_to_openwebui.assert_called_once()
                    self.assertEqual(result["iterations"], 1)
                    self.assertFalse(result["task_complete"])
                    self.assertFalse(result["success"])
                    self.assertIn("continue", result["final_response"].lower())
                    if stream:
                        self.assertEqual(events[-1]["phase"], "stopped")
                    self.assertFalse(any(
                        "IDEA execution budget" in str(message.content)
                        for message in result["messages"]
                    ))


if __name__ == "__main__":
    unittest.main()
