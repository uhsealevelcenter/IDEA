import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from langchain_openai import ChatOpenAI


LANGGRAPH_DIR = Path(__file__).resolve().parents[1] / "langgraph"
sys.path.insert(0, str(LANGGRAPH_DIR))

from agents import terminal_agent  # noqa: E402
from idea_graph.runtime import TerminalGraphRuntime  # noqa: E402


class PromptCacheSerializationTests(unittest.TestCase):
    def test_explicit_breakpoint_reaches_responses_http_body(self):
        self.assert_explicit_breakpoint_reaches_http_body(use_responses_api=True)

    def test_explicit_breakpoint_reaches_chat_completions_http_body(self):
        self.assert_explicit_breakpoint_reaches_http_body(use_responses_api=False)

    def test_manual_runtime_preserves_breakpoint_for_both_apis(self):
        for use_responses_api in (False, True):
            with self.subTest(use_responses_api=use_responses_api):
                self.assert_explicit_breakpoint_reaches_http_body(
                    use_responses_api=use_responses_api, manual=True,
                )

    def assert_explicit_breakpoint_reaches_http_body(self, *, use_responses_api, manual=False):
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
                    if not use_responses_api:
                        events = [{
                            "id": "chatcmpl_test",
                            "object": "chat.completion.chunk",
                            "created": 0,
                            "model": model,
                            "choices": [{
                                "index": 0,
                                "delta": {"role": "assistant", "content": "ok"},
                                "finish_reason": "stop",
                            }],
                        }]
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
                        runtime = TerminalGraphRuntime(
                            session_id="cache-test-session",
                            user_id="cache-test-user",
                            user_email=None,
                            model=model,
                            reasoning_effort=None,
                            use_responses_api=use_responses_api,
                            assistant_id=None,
                            assistant_system_prompt=None,
                            attached_files=[],
                            openwebui_authorization=None,
                            is_guest=False,
                            paperqa_enabled=False,
                        )
                    if not manual:
                        runtime.system_prompt = "stable IDEA instructions"
                    for text in ("first request", "different request"):
                        if manual:
                            runtime.agent.max_iterations = 1
                            with (
                                patch.object(terminal_agent, "list_file_metadata", return_value={}),
                                patch.object(runtime.agent, "_sync_outputs_to_openwebui", return_value=[]),
                                patch("sys.stdout", io.StringIO()),
                            ):
                                runtime.agent.run(text)
                        else:
                            runtime.agent.llm.invoke(runtime.model_messages({
                                "conversation_messages": [{"role": "user", "content": text}],
                                "turn_messages": [],
                            }))

                self.assertEqual(len(requests), 2)
                for request in requests:
                    self.assertEqual(
                        request.url.path,
                        "/v1/responses" if use_responses_api else "/v1/chat/completions",
                    )
                    body = json.loads(request.content)
                    self.assertTrue(body["stream"])
                    self.assertEqual(body["prompt_cache_options"], {"mode": "explicit"})
                    self.assertEqual(
                        body["prompt_cache_key"],
                        terminal_agent._prompt_cache_key(model, "cache-test-session"),
                    )
                    messages = body["input" if use_responses_api else "messages"]
                    self.assertEqual(messages[0], {
                        "role": "system",
                        "content": [{
                            "type": "input_text" if use_responses_api else "text",
                            "text": runtime.system_prompt,
                            "prompt_cache_breakpoint": {"mode": "explicit"},
                        }],
                    })
                    self.assertTrue(body["tools"])

    def test_unsupported_model_keeps_plain_system_message(self):
        for use_responses_api in (False, True):
            message = terminal_agent._cacheable_system_message(
                "instructions", "gpt-4o", use_responses_api=use_responses_api,
            )
            self.assertEqual(message.content, "instructions")


if __name__ == "__main__":
    unittest.main()
