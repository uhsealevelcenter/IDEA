import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "langgraph"))

from langchain_core.messages import AIMessage, ToolMessage
from idea_config import IDEA_MAX_MODEL_TOOL_OBSERVATION_BYTES
from idea_graph.graph import build_idea_graph
from idea_graph.runtime import ToolOutcome
from utils.skill_loader import (
    SkillDocument, SkillBundle, SkillBundleDocument, SkillLoadError,
    make_view_skill_tool, summarize_skill_result,
)


class SkillReadingTests(unittest.TestCase):
    def setUp(self):
        self.text = (
            "# Overview\nUse the supplied data.\n## Background\n"
            + 'Reference text: "quoted" \\ °C 🌊\n' * 400
            + "## Current task\nCompute the mean of the supplied observations.\n"
            + "## Other tasks\nUnrelated instructions.\n"
        )
        self.document = SkillDocument("workspace", "science", "Science", "Analysis", self.text)
        self.workspace = Mock()
        self.workspace.load.return_value = self.document
        self.builtin = Mock()
        self.tool = make_view_skill_tool(self.builtin, self.workspace)

    def read(self, **kwargs):
        raw = self.tool.invoke({"source": "workspace", "id": "science", **kwargs})
        self.assertLessEqual(len(raw.encode("utf-8")), IDEA_MAX_MODEL_TOOL_OBSERVATION_BYTES)
        self.assertNotIn("[…truncated…]", raw)
        return json.loads(raw)

    def test_reaches_needed_section_beyond_observation_limit(self):
        first = self.read()
        self.assertNotIn("Compute the mean", first["content"])
        self.assertIn("Current task", [item["heading"] for item in first["sections"]])
        needed = self.read(section="Current task", expected_sha256=first["sha256"])
        self.assertIn("Compute the mean", needed["content"])
        self.assertNotIn("Unrelated instructions", needed["content"])
        self.assertTrue(needed["range_complete"])
        self.assertEqual(needed["sha256"], self.document.sha256)
        self.assertGreater(needed["offset"], IDEA_MAX_MODEL_TOOL_OBSERVATION_BYTES)

    def test_pages_reconstruct_original_unicode_without_gaps(self):
        page = self.read()
        pieces = [page["content"]]
        while page["next_offset"] is not None:
            position = page["next_offset"]
            page = self.read(offset=position, expected_sha256=page["sha256"])
            self.assertEqual(page["offset"], position)
            self.assertGreater(page["end_offset"], position)
            pieces.append(page["content"])
        self.assertEqual("".join(pieces), self.text)

    def test_every_page_rechecks_access_and_detects_document_changes(self):
        first = self.read()
        self.workspace.load.side_effect = SkillLoadError("Access denied")
        with self.assertRaisesRegex(SkillLoadError, "Access denied"):
            self.read(offset=first["next_offset"])
        self.workspace.load.side_effect = None
        self.workspace.load.return_value = SkillDocument(
            "workspace", "science", "Science", "Analysis", self.text + "Changed"
        )
        with self.assertRaisesRegex(SkillLoadError, "content changed"):
            self.read(offset=first["next_offset"], expected_sha256=first["sha256"])
        self.builtin.load.assert_not_called()

    def test_bad_ranges_and_ambiguous_headings_fail_explicitly(self):
        for kwargs in ({"offset": -1}, {"offset": len(self.text) + 1}, {"section": "Missing"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(SkillLoadError):
                self.read(**kwargs)
        self.workspace.load.return_value = SkillDocument(
            "workspace", "science", "Science", "Analysis",
            "# Root\n```markdown\n## Example only\n```\n## Duplicate\nA\n## Duplicate\nB\n"
        )
        for section in ("Example only", "Duplicate"):
            with self.assertRaises(SkillLoadError):
                self.read(section=section)

    def test_package_selection_keeps_root_and_dependency_navigation(self):
        bundle = SkillBundle(
            source="builtin", skill_id="package", name="Package", description="Example",
            status="", route="analysis", requested_components=("analysis",),
            documents=(
                SkillBundleDocument("root", "root", "Root", "# Root\n" + "Policy.\n" * 1500),
                SkillBundleDocument("policy", "reference", "Policy", "Respect the data units."),
                SkillBundleDocument("analysis", "skill", "Analysis", "Compute the mean."),
            ),
        )
        self.builtin.load_bundle.return_value = bundle
        selection = {"source": "builtin", "id": "package", "route": "analysis", "detail": "full"}
        root = json.loads(self.tool.invoke(selection))
        self.assertEqual(root["document"], "root")
        self.assertEqual(root["next_document"], "policy")
        policy = json.loads(self.tool.invoke({**selection, "document": "policy", "expected_sha256": root["sha256"]}))
        self.assertEqual(policy["content"], "Respect the data units.")
        self.assertEqual(policy["next_document"], "analysis")
        self.assertEqual(policy["document_sha256"], bundle.documents[1].sha256)
        self.assertEqual(policy["sha256"], bundle.sha256)
        with self.assertRaisesRegex(SkillLoadError, "not in this package selection"):
            self.tool.invoke({**selection, "document": "unselected"})
        self.builtin.load_bundle.assert_called_with("package", route="analysis", components=None)

    def test_large_package_plan_is_paged_as_metadata(self):
        bundle = SkillBundle(
            source="builtin", skill_id="package", name="Package", description="Example",
            status="", route="analysis", requested_components=("analysis",),
            documents=tuple(
                SkillBundleDocument(f"component-{i}", "skill", "Description " * 90, "Do work.")
                for i in range(12)
            ),
        )
        self.builtin.load_bundle.return_value = bundle
        request = {"source": "builtin", "id": "package", "route": "analysis"}
        fragments = []
        while True:
            raw = self.tool.invoke(request)
            self.assertLessEqual(len(raw.encode()), IDEA_MAX_MODEL_TOOL_OBSERVATION_BYTES)
            page = json.loads(raw)
            self.assertEqual(page["detail"], "plan")
            self.assertEqual(page["content_format"], "json metadata")
            fragments.append(page["content"])
            if page["next_offset"] is None:
                break
            request.update(offset=page["next_offset"], expected_sha256=page["sha256"])
        self.assertEqual(json.loads("".join(fragments)), json.loads(bundle.to_plan_result()))

    def test_page_log_summary_excludes_private_instructions(self):
        raw = self.tool.invoke({"source": "workspace", "id": "science", "section": "Current task"})
        summary = summarize_skill_result(raw)
        self.assertIn("characters", summary)
        self.assertIn(self.document.sha256, summary)
        self.assertNotIn("Compute the mean", summary)

    def test_graph_can_read_a_needed_section_then_act(self):
        tool = self.tool
        seen = []

        class Runtime:
            def prepare(self, state):
                return {}

            def emit(self, chunk):
                pass

            def model_messages(self, state):
                return state.get("turn_messages", [])

            def call_model(self, messages, **kwargs):
                observations = [m for m in messages if isinstance(m, ToolMessage)]
                step = len(observations)
                if step < 2:
                    args = {"source": "workspace", "id": "science"}
                    if step:
                        page = json.loads(observations[-1].content)
                        args.update(section="Current task", expected_sha256=page["sha256"])
                    return AIMessage(content="", tool_calls=[{
                        "name": "view_skill", "args": args, "id": f"read-{step}",
                    }])
                if step == 2:
                    assert "Compute the mean" in json.loads(observations[-1].content)["content"]
                    return AIMessage(content="", tool_calls=[{
                        "name": "run_terminal_tool", "args": {"command": "analyze"}, "id": "act",
                    }])
                return AIMessage(content="Analysis complete.")

            def execute_tool(self, call, state):
                seen.append(call["name"])
                return ToolOutcome(content=tool.invoke(call["args"]) if call["name"] == "view_skill" else "Mean: 2")

            def finalize(self, state):
                return []

        result = build_idea_graph(Runtime()).invoke({
            "conversation_messages": [{"role": "user", "content": "Analyze the observations."}],
            "run_id": "reading-test", "thread_id": "reading-thread",
        }, config={"recursion_limit": 50})
        self.assertEqual(result["final_status"], "completed")
        self.assertEqual(seen, ["view_skill", "view_skill", "run_terminal_tool"])


if __name__ == "__main__":
    unittest.main()
