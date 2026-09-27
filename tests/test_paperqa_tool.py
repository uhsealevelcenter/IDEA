import json
import sys
import threading
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace


LANGGRAPH_DIR = Path(__file__).resolve().parents[1] / "langgraph"
sys.path.insert(0, str(LANGGRAPH_DIR))

from utils.pqa.my_pqa_settings import create_pqa_settings  # noqa: E402
from litellm import Router  # noqa: E402
from utils.tools.knowledge_base_tool import (  # noqa: E402
    _select_knowledge_scope,
    _selected_media_context_ids,
    make_query_knowledge_base_tool,
)
from utils.tools import knowledge_base_tool  # noqa: E402


class PaperQAToolTests(unittest.TestCase):
    def test_attached_file_query_uses_direct_scope(self):
        self.assertEqual(
            _select_knowledge_scope(
                "Summarize the attached Test_Paper.pdf.",
                "combined",
                "direct",
                ("Test_Paper.pdf",),
            ),
            "direct",
        )

    def test_named_direct_file_uses_direct_scope(self):
        self.assertEqual(
            _select_knowledge_scope(
                "What are the conclusions of Test_Paper?",
                "combined",
                "direct",
                ("Test_Paper.pdf",),
            ),
            "direct",
        )

    def test_literature_query_uses_combined_scope(self):
        self.assertEqual(
            _select_knowledge_scope(
                "Summarize the IDEA literature article.",
                "combined",
                "direct",
                ("Test_Paper.pdf",),
            ),
            "combined",
        )

    def test_comparison_query_keeps_both_sources(self):
        self.assertEqual(
            _select_knowledge_scope(
                "Compare the attached Test_Paper.pdf with the IDEA article.",
                "combined",
                "direct",
                ("Test_Paper.pdf",),
            ),
            "combined",
        )

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

    def test_tool_returns_and_surfaces_preparation_warnings(self):
        paperqa_tool = make_query_knowledge_base_tool(
            lambda: "trusted-scope",
            session_id="trusted-chat",
            end_user_id="scientist@example.org",
            warnings_getter=lambda: (
                "Skipped 'notes.txt': unsupported document type.",
            ),
        )
        result = {
            "answer": "No papers found in your Knowledge base. Please upload papers first.",
            "images": [],
        }

        with patch.object(
            knowledge_base_tool,
            "_query_knowledge_base_async",
            new=AsyncMock(return_value=result),
        ):
            payload = paperqa_tool.invoke({"query": "Summarize the attachment"})

        self.assertIn("notes.txt", payload)
        self.assertIn('"warnings"', payload)

    def test_luna_reasoning_parameter_reaches_the_chat_endpoint(self):
        """Catch LiteLLM releases that reject PaperQA's GPT-6 request."""
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                requests.append((self.path, json.loads(body)))
                response = json.dumps({
                    "id": "chatcmpl-paperqa-test",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "gpt-6-luna",
                    "choices": [{
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                settings = create_pqa_settings(root / "papers", root / "index")
            params = dict(settings.llm_config["model_list"][0]["litellm_params"])
            params["api_base"] = f"http://127.0.0.1:{server.server_port}/v1"
            params["api_key"] = "local-test-key"
            router = Router(model_list=[{
                "model_name": settings.llm,
                "litellm_params": params,
            }])
            router.completion(
                model=settings.llm,
                messages=[{"role": "user", "content": "Summarize the paper"}],
                tools=[{
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "description": "Look up a paper",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }],
            )
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(requests[0][0], "/v1/chat/completions")
        self.assertEqual(requests[0][1]["model"], "gpt-6-luna")
        self.assertEqual(requests[0][1]["reasoning_effort"], "none")
        self.assertEqual(requests[0][1]["temperature"], 1)
        self.assertEqual(requests[0][1]["tools"][0]["function"]["name"], "lookup")

    def test_all_roles_and_embedding_use_the_litellm_proxy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = create_pqa_settings(
                root / "papers",
                root / "index",
                end_user_id="scientist@example.org",
            )

        self.assertEqual(settings.llm, "gpt-6-luna")
        self.assertEqual(settings.summary_llm, "gpt-6-luna")
        self.assertEqual(settings.agent.agent_llm, "gpt-6-luna")
        self.assertEqual(
            settings.embedding,
            "text-embedding-3-small",
        )
        llm_params = settings.llm_config["model_list"][0][
            "litellm_params"
        ]
        self.assertEqual(llm_params["reasoning_effort"], "none")
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
