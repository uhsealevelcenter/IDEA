import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


LANGGRAPH_DIR = Path(__file__).resolve().parents[1] / "langgraph"
sys.path.insert(0, str(LANGGRAPH_DIR))

from utils.pqa.my_pqa_settings import create_pqa_settings  # noqa: E402
from utils.tools.knowledge_base_tool import (  # noqa: E402
    _selected_media_context_ids,
    make_query_knowledge_base_tool,
)


class PaperQAToolTests(unittest.TestCase):
    def test_ordinary_query_does_not_select_images(self):
        session = SimpleNamespace(
            raw_answer="Sea level rose [context-1].",
            answer="Sea level rose.",
            formatted_answer="Sea level rose [1].",
            used_contexts={"context-1"},
        )

        self.assertEqual(
            _selected_media_context_ids(
                "What was the measured rate of sea-level rise?",
                session,
            ),
            set(),
        )

    def test_figure_query_selects_only_cited_contexts(self):
        session = SimpleNamespace(
            raw_answer="Figure 2 shows the trend [context-1].",
            answer="Figure 2 shows the trend.",
            formatted_answer="Figure 2 shows the trend [1].",
            used_contexts={"context-1"},
        )

        self.assertEqual(
            _selected_media_context_ids(
                "What does Figure 2 show?",
                session,
            ),
            {"context-1"},
        )

    def test_answer_can_trigger_images_for_cited_contexts(self):
        session = SimpleNamespace(
            raw_answer="The result appears in Table 3 [context-2].",
            answer="The result appears in Table 3.",
            formatted_answer="The result appears in Table 3 [2].",
            used_contexts={"context-2"},
        )

        self.assertEqual(
            _selected_media_context_ids(
                "Where are the regional results summarized?",
                session,
            ),
            {"context-2"},
        )

    def test_media_query_with_no_cited_contexts_selects_nothing(self):
        session = SimpleNamespace(
            raw_answer="I could not answer the question.",
            answer="I could not answer the question.",
            formatted_answer="I could not answer the question.",
            used_contexts=set(),
        )

        self.assertEqual(
            _selected_media_context_ids(
                "Describe the figure.",
                session,
            ),
            set(),
        )

    def test_tool_schema_exposes_only_the_research_query(self):
        paperqa_tool = make_query_knowledge_base_tool(
            lambda: "trusted-scope",
            session_id="trusted-chat",
            end_user_id="scientist@example.org",
        )

        schema = paperqa_tool.args_schema.model_json_schema()

        self.assertEqual(set(schema["properties"]), {"query"})
        self.assertEqual(schema["required"], ["query"])

    def test_all_roles_and_embedding_use_the_litellm_proxy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = create_pqa_settings(
                root / "papers",
                root / "index",
                end_user_id="scientist@example.org",
            )

        self.assertEqual(settings.llm, "gpt-5.6-luna")
        self.assertEqual(settings.summary_llm, "gpt-5.6-luna")
        self.assertEqual(settings.agent.agent_llm, "gpt-5.6-luna")
        self.assertEqual(
            settings.embedding,
            "text-embedding-3-small",
        )
        llm_params = settings.llm_config["model_list"][0][
            "litellm_params"
        ]
        self.assertEqual(
            llm_params["api_base"],
            "http://litellm:8080/v1",
        )
        self.assertEqual(
            llm_params["extra_headers"],
            {
                "x-litellm-end-user-id": "scientist@example.org",
            },
        )
        self.assertEqual(
            settings.embedding_config["kwargs"]["api_base"],
            "http://litellm:8080/v1",
        )


if __name__ == "__main__":
    unittest.main()
