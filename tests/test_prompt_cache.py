import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI


LANGGRAPH_DIR = Path(__file__).resolve().parents[1] / "langgraph"
sys.path.insert(0, str(LANGGRAPH_DIR))

from agents import terminal_agent  # noqa: E402


class PromptCacheSerializationTests(unittest.TestCase):
    def test_explicit_breakpoint_reaches_responses_http_body(self):
        """Exercise the agent's settings and real LangChain/OpenAI serializers."""
        for model in ("gpt-5.6-terra", "gpt-6-astra", "gpt-6.1-sol"):
            with self.subTest(model=model):
                requests = []

                def respond(request):
                    requests.append(request)
                    response = {
                        "id": "resp_test",
                        "object": "response",
                        "created_at": 0,
                        "model": model,
                        "status": "completed",
                        "output": [{
                            "id": "msg_test",
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{
                                "type": "output_text",
                                "text": "ok",
                                "annotations": [],
                            }],
                        }],
                    }
                    events = [
                        {
                            "type": "response.output_text.delta",
                            "item_id": "msg_test",
                            "output_index": 0,
                            "content_index": 0,
                            "delta": "ok",
                            "sequence_number": 0,
                        },
                        {
                            "type": "response.completed",
                            "response": response,
                            "sequence_number": 1,
                        },
                    ]
                    return httpx.Response(
                        200,
                        headers={"content-type": "text/event-stream"},
                        text="".join(f"data: {json.dumps(event)}\n\n" for event in events),
                    )

                with httpx.Client(transport=httpx.MockTransport(respond)) as client:
                    def make_llm(**kwargs):
                        kwargs.update(
                            api_key="test-only",
                            base_url="https://idea.invalid/v1",
                            http_client=client,
                        )
                        return ChatOpenAI(**kwargs)

                    with (
                        patch.object(terminal_agent, "ChatOpenAI", side_effect=make_llm),
                        patch.object(terminal_agent, "LITELLM_VIRTUAL_KEY", "test-only"),
                        patch.object(terminal_agent, "IDEA_CODEX_ENABLED", False),
                    ):
                        agent = terminal_agent.TerminalAgent(
                            session_id="cache-test-session",
                            user_id="cache-test-user",
                            model=model,
                            use_responses_api=True,
                        )
                    for text in ("first request", "different request"):
                        agent.llm.invoke([
                            terminal_agent._cacheable_system_message(
                                "stable IDEA instructions", model,
                            ),
                            HumanMessage(content=text),
                        ])

                self.assertEqual(len(requests), 2)
                for request in requests:
                    self.assertEqual(request.url.path, "/v1/responses")
                    body = json.loads(request.content)
                    self.assertTrue(body["stream"])
                    self.assertEqual(body["prompt_cache_options"], {"mode": "explicit"})
                    self.assertEqual(
                        body["prompt_cache_key"],
                        terminal_agent._prompt_cache_key(model, "cache-test-session"),
                    )
                    self.assertEqual(body["input"][0], {
                        "role": "system",
                        "content": [{
                            "type": "input_text",
                            "text": "stable IDEA instructions",
                            "prompt_cache_breakpoint": {"mode": "explicit"},
                        }],
                    })
                    self.assertTrue(body["tools"])

    def test_unsupported_model_keeps_plain_system_message(self):
        message = terminal_agent._cacheable_system_message("instructions", "gpt-4o")
        self.assertEqual(message.content, "instructions")


if __name__ == "__main__":
    unittest.main()
