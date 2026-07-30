import sys
import tempfile
import unittest
from pathlib import Path


LANGGRAPH_DIR = Path(__file__).resolve().parents[1] / "langgraph"
sys.path.insert(0, str(LANGGRAPH_DIR))

from utils.pqa.my_pqa_settings import create_pqa_settings  # noqa: E402
from utils.tools.knowledge_base_tool import (  # noqa: E402
    make_query_knowledge_base_tool,
)


class PaperQAToolTests(unittest.TestCase):
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
