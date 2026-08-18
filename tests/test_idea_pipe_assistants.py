import importlib.util
import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


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
    @staticmethod
    def _chat_run_client(aiter_lines):
        """Adapt legacy SSE fixtures to the durable chat-run polling API."""
        client = Mock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        start_response = Mock()
        start_response.raise_for_status.return_value = None
        start_response.json.return_value = {"run_id": "run-1", "status": "queued"}
        client.post = AsyncMock(return_value=start_response)
        source = aiter_lines()
        sequence = 0

        async def get(*args, **kwargs):
            nonlocal sequence
            response = Mock()
            response.raise_for_status.return_value = None
            try:
                line = await anext(source)
            except StopAsyncIteration:
                response.json.return_value = {
                    "run_id": "run-1", "status": "completed",
                    "events": [], "next_after": sequence,
                }
                return response
            sequence += 1
            raw = line[len("data: "):] if line.startswith("data: ") else line
            response.json.return_value = {
                "run_id": "run-1", "status": "running",
                "events": [{"seq": sequence, "chunk": json.loads(raw)}],
                "next_after": sequence,
            }
            return response

        client.get = AsyncMock(side_effect=get)
        return client

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
        self.assertEqual(
            idea_pipe._structured_messages(messages),
            [
                {"id": "", "role": "user", "content": "First question"},
                {"id": "", "role": "assistant", "content": "First answer"},
                {"id": "", "role": "user", "content": "Latest question"},
            ],
        )

    def test_separates_compaction_summary_from_assistant_policy(self):
        messages = [
            {
                "role": "system",
                "content": (
                    "You are SEA.\n\n[CONVERSATION SUMMARY]\n"
                    "The user plotted RONI and asked to revise it."
                ),
            },
            {"id": "user-2", "role": "user", "content": "Make it red."},
        ]

        self.assertEqual(
            idea_pipe._assistant_system_prompt(messages),
            "You are SEA.",
        )
        self.assertEqual(
            idea_pipe._structured_messages(messages),
            [
                {
                    "id": "",
                    "role": "system",
                    "content": "The user plotted RONI and asked to revise it.",
                },
                {"id": "user-2", "role": "user", "content": "Make it red."},
            ],
        )
    def test_structured_messages_strip_legacy_assistant_images_only(self):
        generated = "data:image/png;base64," + ("A" * 1000)
        user_image = "data:image/png;base64,USERIMAGE"

        structured = idea_pipe._structured_messages([
            {
                "role": "assistant",
                "content": f"Result\n\n![plot]({generated})\n\n[file](preview)",
            },
            {
                "role": "user",
                "content": [{
                    "type": "image_url",
                    "image_url": {"url": user_image},
                }],
            },
        ])

        self.assertNotIn("base64", structured[0]["content"])
        self.assertIn("Generated image omitted", structured[0]["content"])
        self.assertIn("[file](preview)", structured[0]["content"])
        self.assertEqual(
            structured[1]["content"][0]["image_url"]["url"],
            user_image,
        )

    def test_preserves_complete_openwebui_skill_system_context(self):
        messages = [
            {"role": "system", "content": "Assistant instructions"},
            {
                "role": "system",
                "content": (
                    '<skill name="private-workflow">\n'
                    "BEGIN\nPRIVATE-MIDDLE\nEND\n"
                    "</skill>"
                ),
            },
            {
                "role": "system",
                "content": (
                    "<available_skills>\n"
                    "<skill><id>lazy-skill</id></skill>\n"
                    "</available_skills>"
                ),
            },
            {"role": "user", "content": "Use the skills"},
        ]

        system_context = idea_pipe._assistant_system_prompt(messages)

        self.assertIn("PRIVATE-MIDDLE", system_context)
        self.assertIn("<id>lazy-skill</id>", system_context)

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

    def test_extracts_user_authorization_from_header_or_cookie(self):
        self.assertEqual(
            idea_pipe._request_authorization(
                SimpleNamespace(
                    headers={"authorization": "Bearer header-token"},
                    cookies={"token": "cookie-token"},
                )
            ),
            "Bearer header-token",
        )
        self.assertEqual(
            idea_pipe._request_authorization(
                SimpleNamespace(headers={}, cookies={"token": "cookie-token"})
            ),
            "Bearer cookie-token",
        )
        self.assertIsNone(
            idea_pipe._request_authorization(
                SimpleNamespace(
                    headers={"authorization": "Basic credentials"},
                    cookies={},
                )
            )
        )

    def test_collects_only_safe_deduplicated_attachment_ids(self):
        descriptors = idea_pipe._attached_file_descriptors(
            [
                {"type": "file", "id": "file-1", "name": "data.nc"},
                {"type": "file", "id": "file-1", "name": "duplicate.nc"},
                {"type": "collection", "id": "knowledge-1"},
                {"type": "file", "url": "https://example.org/file.csv"},
                {"type": "file", "id": "../unsafe"},
            ],
            {
                "user_message": {
                    "files": [
                        {
                            "type": "image",
                            "id": "image-2",
                            "name": "map.png",
                            "size": 42,
                        }
                    ]
                }
            },
        )

        self.assertEqual(
            descriptors,
            [
                {"id": "file-1", "type": "file", "name": "data.nc"},
                {
                    "id": "knowledge-1",
                    "type": "collection",
                },
                {
                    "id": "image-2",
                    "type": "image",
                    "name": "map.png",
                    "size": 42,
                },
            ],
        )

    def test_collects_nested_attachment_from_latest_user_message(self):
        descriptors = idea_pipe._attached_resource_descriptors(
            None,
            {},
            None,
            [{
                "role": "user",
                "content": "Inspect this dataset.",
                "files": [{
                    "type": "file",
                    "file": {
                        "id": "file-nested-123",
                        "filename": "observations.nc",
                        "meta": {
                            "name": "observations.nc",
                            "content_type": "application/x-netcdf",
                            "size": 42,
                        },
                    },
                }],
            }],
        )

        self.assertEqual(descriptors, [{
            "id": "file-nested-123",
            "type": "file",
            "name": "observations.nc",
            "content_type": "application/x-netcdf",
            "size": 42,
        }])

    def test_builds_public_origin_from_forwarded_request_headers(self):
        request = SimpleNamespace(
            headers={
                "host": "openwebui:8080",
                "x-forwarded-host": "localhost",
                "x-forwarded-proto": "http",
            },
            url=SimpleNamespace(scheme="http"),
            base_url="http://openwebui:8080/",
        )

        self.assertEqual(
            idea_pipe._request_public_base_url(request),
            "http://localhost",
        )

    def test_pipe_forwards_prompt_and_isolates_assistant_history(self):
        response = Mock()
        response.raise_for_status.return_value = None

        async def aiter_lines():
            if False:
                yield ""

        response.aiter_lines = aiter_lines
        client = self._chat_run_client(aiter_lines)
        body = {
            "messages": [
                {"role": "system", "content": "You are SEA."},
                {"role": "user", "content": "Analyze Honolulu."},
            ],
            # OpenWebUI's legacy model-Knowledge handling injects the
            # persistent collection here, while the direct PDF also arrives
            # separately through __files__.
            "files": [
                {
                    "type": "file",
                    "id": "file-123",
                    "name": "article.pdf",
                    "content_type": "application/pdf",
                },
            ],
        }
        metadata = {
            "chat_id": "chat-123",
            "message_id": "assistant-response-1",
            "model": {
                "id": "sea",
                "name": "SEA",
                "info": {
                    "meta": {
                        "knowledge": [
                            {
                                "type": "collection",
                                "id": "knowledge-123",
                                "name": "Literature",
                            }
                        ]
                    }
                },
            },
        }

        async def collect():
            return [
                chunk
                async for chunk in idea_pipe.Pipe().pipe(
                    body,
                    __user__={
                        "id": "user-1",
                        "email": "scientist@example.org",
                        "role": "user",
                    },
                    __files__=[
                        {
                            "type": "file",
                            "id": "file-123",
                            "name": "article.pdf",
                            "content_type": "application/pdf",
                        }
                    ],
                    __metadata__=metadata,
                    __request__=SimpleNamespace(
                        headers={},
                        cookies={"token": "user-session-token"},
                    ),
                )
            ]

        with patch.object(
            idea_pipe.httpx,
            "AsyncClient",
            return_value=client,
        ):
            result = asyncio.run(collect())

        self.assertEqual(result, [])
        payload = client.post.call_args.kwargs["json"]
        self.assertEqual(payload["assistant_id"], "sea")
        self.assertEqual(payload["assistant_system_prompt"], "You are SEA.")
        self.assertEqual(payload["session_id"], "chat-123")
        self.assertNotIn("model", payload)
        self.assertEqual(
            payload["response_message_id"], "assistant-response-1"
        )
        self.assertEqual(
            payload["messages"],
            [
                {"id": "", "role": "user", "content": "Analyze Honolulu."},
            ],
        )
        self.assertEqual(
            payload["attached_files"],
            [
                {
                    "id": "file-123",
                    "type": "file",
                    "name": "article.pdf",
                    "content_type": "application/pdf",
                },
                {
                    "id": "knowledge-123",
                    "type": "collection",
                    "name": "Literature",
                },
            ],
        )
        self.assertTrue(payload["paperqa_enabled"])
        self.assertEqual(
            payload["openwebui_authorization"],
            "Bearer user-session-token",
        )

    def test_paperqa_is_enabled_for_official_assistants_but_disabled_for_guests(self):
        pipe = idea_pipe.Pipe()
        for assistant_id in (
            "welcome-assistant",
            "sea",
            "mars-assistant",
        ):
            self.assertTrue(
                idea_pipe._paperqa_enabled(
                    assistant_id,
                    False,
                    pipe.valves.PAPERQA_ASSISTANT_IDS,
                )
            )
        self.assertFalse(
            idea_pipe._paperqa_enabled(
                "sea",
                True,
                pipe.valves.PAPERQA_ASSISTANT_IDS,
            )
        )
        self.assertTrue(
            idea_pipe._paperqa_enabled(
                "welcome-assistant",
                False,
                pipe.valves.PAPERQA_ASSISTANT_IDS,
            )
        )

    def test_sanitizes_model_authored_sandbox_links(self):
        self.assertEqual(
            idea_pipe._sanitize_sandbox_links(
                "[Download the figure](sandbox:/outputs/report/figure.png)"
            ),
            "Download the figure: `/outputs/report/figure.png`",
        )
        self.assertEqual(
            idea_pipe._sanitize_sandbox_links(
                "Saved at sandbox:/outputs/report/data.csv"
            ),
            "Saved at `/outputs/report/data.csv`",
        )

    def test_resolves_sandbox_link_to_synced_file_url(self):
        resolved, referenced = idea_pipe._resolve_output_links(
            "[Download](sandbox:/outputs/report/figure.png)",
            [
                {
                    "filename": "/outputs/report/figure.png",
                    "openwebui_file_id": "file-123",
                }
            ],
        )

        self.assertEqual(
            resolved,
            "[figure.png](/api/v1/files/file-123/content)",
        )
        self.assertEqual(referenced, {"file-123"})

    def test_resolves_raw_output_markdown_link_to_synced_file_url(self):
        resolved, referenced = idea_pipe._resolve_output_links(
            "[Download](/outputs/report/figure.png)",
            [
                {
                    "filename": "/outputs/report/figure.png",
                    "openwebui_file_id": "file-123",
                }
            ],
        )

        self.assertEqual(
            resolved,
            "[figure.png](/api/v1/files/file-123/content)",
        )
        self.assertEqual(referenced, {"file-123"})

    def test_resolves_raw_sandbox_url_to_clickable_filename(self):
        resolved, referenced = idea_pipe._resolve_output_links(
            "Saved at sandbox:/outputs/report/data.csv",
            [
                {
                    "filename": "/outputs/report/data.csv",
                    "openwebui_file_id": "file-456",
                }
            ],
        )

        self.assertEqual(
            resolved,
            "Saved at "
            "[data.csv](/api/v1/files/file-456/content)",
        )
        self.assertEqual(referenced, {"file-456"})

    def test_unresolved_output_link_is_explicitly_unavailable(self):
        resolved, referenced = idea_pipe._resolve_output_links(
            "[Open the page](sandbox:/outputs/report/page.html)",
            [],
        )

        self.assertEqual(
            resolved,
            "⚠️ Open the page (output link unavailable)",
        )
        self.assertEqual(referenced, set())

    def test_resolves_url_encoded_output_path(self):
        resolved, referenced = idea_pipe._resolve_output_links(
            "[Open](sandbox:/outputs/report/interactive%20plot.html)",
            [{
                "filename": "/outputs/report/interactive plot.html",
                "openwebui_file_id": "file-encoded",
            }],
        )

        self.assertEqual(
            resolved,
            "[interactive plot.html]"
            "(/idea-file-preview/file-encoded/interactive%20plot.html)",
        )
        self.assertEqual(referenced, {"file-encoded"})

    def test_pipe_replaces_final_sandbox_link_without_duplicate_attachment(self):
        response = Mock()
        response.raise_for_status.return_value = None

        async def aiter_lines():
            yield (
                'data: {"type":"message","content":"[Download]'
                '(sandbox:/outputs/report/figure.png)"}'
            )
            yield (
                'data: {"type":"file",'
                '"filename":"/outputs/report/figure.png",'
                '"openwebui_file_id":"file-123"}'
            )

        response.aiter_lines = aiter_lines
        client = self._chat_run_client(aiter_lines)

        async def collect():
            return [
                chunk
                async for chunk in idea_pipe.Pipe().pipe(
                    {"messages": [{"role": "user", "content": "Create it"}]},
                    __user__={"id": "user-1", "role": "user"},
                    __metadata__={"chat_id": "chat-1"},
                    __request__=SimpleNamespace(
                        headers={"authorization": "Bearer user-token"},
                        cookies={},
                    ),
                )
            ]

        with patch.object(
            idea_pipe.httpx,
            "AsyncClient",
            return_value=client,
        ):
            result = asyncio.run(collect())

        self.assertEqual(
            result,
            [
                "[figure.png](/api/v1/files/file-123/content)"
            ],
        )

    def test_pipe_renders_image_event_from_uploaded_file_without_base64(self):
        async def aiter_lines():
            yield (
                'data: {"type":"image","format":"png",'
                '"filename":"/workspace/oni/oni.png"}'
            )
            yield 'data: {"type":"message","content":"Here is the ONI."}'
            yield (
                'data: {"type":"file",'
                '"filename":"/outputs/oni/oni.png",'
                '"openwebui_file_id":"file-oni"}'
            )

        client = self._chat_run_client(aiter_lines)

        async def collect():
            return [
                chunk
                async for chunk in idea_pipe.Pipe().pipe(
                    {"messages": [{"role": "user", "content": "Plot ONI"}]},
                    __user__={"id": "user-1", "role": "user"},
                    __metadata__={"chat_id": "chat-1"},
                )
            ]

        with patch.object(idea_pipe.httpx, "AsyncClient", return_value=client):
            result = asyncio.run(collect())

        self.assertEqual(result, [
            "Here is the ONI.",
            "\n\n![generated image]"
            "(/api/v1/files/file-oni/content)\n\n",
        ])
        self.assertNotIn("data:image", "".join(result))

    def test_pipe_yields_ordinary_assistant_text_before_stream_finishes(self):
        response = Mock()
        response.raise_for_status.return_value = None
        release_second_chunk = asyncio.Event()

        async def aiter_lines():
            yield 'data: {"type":"message","content":"Working now"}'
            await release_second_chunk.wait()
            yield 'data: {"type":"message","content":" and done"}'

        response.aiter_lines = aiter_lines
        client = self._chat_run_client(aiter_lines)

        async def collect_incrementally():
            stream = idea_pipe.Pipe().pipe(
                {"messages": [{"role": "user", "content": "Create it"}]},
                __user__={"id": "user-1", "role": "user"},
                __metadata__={"chat_id": "chat-1"},
            )
            first = await asyncio.wait_for(anext(stream), timeout=0.1)
            release_second_chunk.set()
            remaining = [chunk async for chunk in stream]
            return first, remaining

        with patch.object(
            idea_pipe.httpx,
            "AsyncClient",
            return_value=client,
        ):
            first, remaining = asyncio.run(collect_incrementally())

        self.assertEqual(first, "Working now")
        self.assertEqual(remaining, [" and done"])

    def test_pipe_emits_native_openwebui_progress_statuses(self):
        response = Mock()
        response.raise_for_status.return_value = None

        async def aiter_lines():
            yield (
                'data: {"type":"status","action":"idea_agent",'
                '"phase":"thinking","description":"Thinking…",'
                '"done":false}'
            )
            yield (
                'data: {"type":"status","action":"idea_agent",'
                '"phase":"preparing_tool",'
                '"description":"Preparing a file…","done":false,'
                '"tool_name":"write_file_tool"}'
            )
            yield 'data: {"type":"message","content":"Done"}'
            yield (
                'data: {"type":"status","action":"idea_agent",'
                '"phase":"completed","description":"Finished",'
                '"done":true}'
            )

        response.aiter_lines = aiter_lines
        client = self._chat_run_client(aiter_lines)
        event_emitter = AsyncMock()

        async def collect():
            return [
                chunk
                async for chunk in idea_pipe.Pipe().pipe(
                    {"messages": [{"role": "user", "content": "Create it"}]},
                    __user__={"id": "user-1", "role": "user"},
                    __metadata__={"chat_id": "chat-1"},
                    __event_emitter__=event_emitter,
                )
            ]

        with patch.object(
            idea_pipe.httpx,
            "AsyncClient",
            return_value=client,
        ):
            result = asyncio.run(collect())

        self.assertEqual(result, ["Done"])
        self.assertEqual(
            [call.args[0] for call in event_emitter.await_args_list],
            [
                {
                    "type": "status",
                    "data": {
                        "action": "idea_agent",
                        "phase": "starting",
                        "description": "Working on your request…",
                        "done": False,
                    },
                },
                {
                    "type": "status",
                    "data": {
                        "action": "idea_agent",
                        "phase": "thinking",
                        "description": "Thinking…",
                        "done": False,
                    },
                },
                {
                    "type": "status",
                    "data": {
                        "action": "idea_agent",
                        "phase": "preparing_tool",
                        "description": "Preparing a file…",
                        "done": False,
                        "tool_name": "write_file_tool",
                    },
                },
                {
                    "type": "status",
                    "data": {
                        "action": "idea_agent",
                        "phase": "completed",
                        "description": "Finished",
                        "done": True,
                    },
                },
            ],
        )

    def test_pipe_renders_streamed_run_error_only_once(self):
        client = Mock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        start_response = Mock()
        start_response.raise_for_status.return_value = None
        start_response.json.return_value = {"run_id": "run-timeout"}
        failed_response = Mock()
        failed_response.raise_for_status.return_value = None
        failed_response.json.return_value = {
            "run_id": "run-timeout",
            "status": "failed",
            "error": "Request timed out.",
            "events": [{
                "seq": 1,
                "chunk": {"error": "Request timed out."},
            }],
        }
        client.post = AsyncMock(return_value=start_response)
        client.get = AsyncMock(return_value=failed_response)

        async def collect():
            return [
                chunk
                async for chunk in idea_pipe.Pipe().pipe(
                    {"messages": [{"role": "user", "content": "Complex task"}]},
                    __user__={"id": "user-1", "role": "user"},
                    __metadata__={"chat_id": "chat-1"},
                )
            ]

        with patch.object(idea_pipe.httpx, "AsyncClient", return_value=client):
            result = asyncio.run(collect())

        self.assertEqual(result, ["\n\n**Error:** Request timed out.\n\n"])

    def test_pipe_uses_terminal_error_when_error_event_is_missing(self):
        client = Mock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        start_response = Mock()
        start_response.raise_for_status.return_value = None
        start_response.json.return_value = {"run_id": "run-timeout"}
        failed_response = Mock()
        failed_response.raise_for_status.return_value = None
        failed_response.json.return_value = {
            "run_id": "run-timeout",
            "status": "failed",
            "error": "Request timed out.",
            "events": [],
        }
        client.post = AsyncMock(return_value=start_response)
        client.get = AsyncMock(return_value=failed_response)

        async def collect():
            return [
                chunk
                async for chunk in idea_pipe.Pipe().pipe(
                    {"messages": [{"role": "user", "content": "Complex task"}]},
                    __user__={"id": "user-1", "role": "user"},
                    __metadata__={"chat_id": "chat-1"},
                )
            ]

        with patch.object(idea_pipe.httpx, "AsyncClient", return_value=client):
            result = asyncio.run(collect())

        self.assertEqual(result, ["\n\n**Error:** Request timed out.\n\n"])

    def test_pipe_preserves_classified_timeout_status(self):
        client = Mock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        start_response = Mock()
        start_response.raise_for_status.return_value = None
        start_response.json.return_value = {"run_id": "run-timeout"}
        timeout_message = (
            "The model did not respond within 180 seconds. Any completed "
            "tool operations were retained; please retry."
        )
        failed_response = Mock()
        failed_response.raise_for_status.return_value = None
        failed_response.json.return_value = {
            "run_id": "run-timeout",
            "status": "failed",
            "error": timeout_message,
            "events": [
                {
                    "seq": 1,
                    "chunk": {
                        "type": "status",
                        "phase": "model_timeout",
                        "description": timeout_message,
                        "done": True,
                        "error": True,
                    },
                },
                {"seq": 2, "chunk": {"error": timeout_message}},
            ],
        }
        client.post = AsyncMock(return_value=start_response)
        client.get = AsyncMock(return_value=failed_response)
        event_emitter = AsyncMock()

        async def collect():
            return [
                chunk
                async for chunk in idea_pipe.Pipe().pipe(
                    {"messages": [{"role": "user", "content": "Complex task"}]},
                    __user__={"id": "user-1", "role": "user"},
                    __metadata__={"chat_id": "chat-1"},
                    __event_emitter__=event_emitter,
                )
            ]

        with patch.object(idea_pipe.httpx, "AsyncClient", return_value=client):
            result = asyncio.run(collect())

        self.assertEqual(result, [f"\n\n**Error:** {timeout_message}\n\n"])
        emitted_statuses = [
            call.args[0]["data"] for call in event_emitter.await_args_list
        ]
        self.assertEqual(emitted_statuses[-1]["phase"], "model_timeout")
        self.assertEqual(emitted_statuses[-1]["description"], timeout_message)

    def test_pipe_stop_clears_thinking_status_and_requests_backend_stop(self):
        client = Mock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        start_response = Mock()
        start_response.raise_for_status.return_value = None
        start_response.json.return_value = {"run_id": "run-stop", "status": "queued"}
        stop_response = Mock()
        stop_response.raise_for_status.return_value = None
        client.post = AsyncMock(side_effect=[start_response, stop_response])
        polling = asyncio.Event()

        async def blocked_get(*args, **kwargs):
            polling.set()
            await asyncio.Event().wait()

        client.get = AsyncMock(side_effect=blocked_get)
        event_emitter = AsyncMock()

        async def cancel_while_polling():
            stream = idea_pipe.Pipe().pipe(
                {"messages": [{"role": "user", "content": "Run Python"}]},
                __user__={"id": "user-1", "role": "user"},
                __metadata__={"chat_id": "chat-1"},
                __event_emitter__=event_emitter,
            )
            pending = asyncio.create_task(anext(stream))
            await asyncio.wait_for(polling.wait(), timeout=0.1)
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending

        with patch.object(idea_pipe.httpx, "AsyncClient", return_value=client):
            asyncio.run(cancel_while_polling())

        self.assertEqual(client.post.await_count, 2)
        self.assertTrue(
            client.post.await_args_list[-1].args[0].endswith(
                "/chat-runs/run-stop/stop"
            )
        )
        self.assertEqual(
            event_emitter.await_args_list[-1].args[0],
            {
                "type": "status",
                "data": {
                    "action": "idea_agent",
                    "phase": "stopped",
                    "description": "Stopped",
                    "done": True,
                },
            },
        )

    def test_pipe_resolves_artifact_link_split_across_message_chunks(self):
        response = Mock()
        response.raise_for_status.return_value = None

        async def aiter_lines():
            yield (
                'data: {"type":"message",'
                '"content":"Created it. [Download]("}'
            )
            yield 'data: {"type":"message","content":"sandbox:/out"}'
            yield (
                'data: {"type":"message",'
                '"content":"puts/report/figure.png)"}'
            )
            yield (
                'data: {"type":"file",'
                '"filename":"/outputs/report/figure.png",'
                '"openwebui_file_id":"file-123"}'
            )

        response.aiter_lines = aiter_lines
        client = self._chat_run_client(aiter_lines)

        async def collect():
            return [
                chunk
                async for chunk in idea_pipe.Pipe().pipe(
                    {"messages": [{"role": "user", "content": "Create it"}]},
                    __user__={"id": "user-1", "role": "user"},
                    __metadata__={"chat_id": "chat-1"},
                )
            ]

        with patch.object(
            idea_pipe.httpx,
            "AsyncClient",
            return_value=client,
        ):
            result = asyncio.run(collect())

        self.assertEqual(
            result,
            [
                "Created it. ",
                "[figure.png](/api/v1/files/file-123/content)",
            ],
        )

    def test_resolves_to_visible_absolute_public_url(self):
        resolved, referenced = idea_pipe._resolve_output_links(
            "[random_scatter.png](sandbox:/outputs/random_scatter.png)",
            [
                {
                    "filename": "/outputs/random_scatter.png",
                    "openwebui_file_id": "file-123",
                }
            ],
            "http://localhost",
        )

        self.assertEqual(
            resolved,
            "[random_scatter.png]"
            "(http://localhost/api/v1/files/file-123/content)",
        )
        self.assertEqual(referenced, {"file-123"})

    def test_html_uses_safe_inline_preview_link(self):
        self.assertEqual(
            idea_pipe._file_link(
                "file-456",
                "/outputs/report/interactive plot.html",
                "http://localhost",
            ),
            "[interactive plot.html]"
            "(http://localhost/idea-file-preview/file-456/"
            "interactive%20plot.html)",
        )

    def test_non_previewable_file_uses_download_link(self):
        self.assertEqual(
            idea_pipe._file_link(
                "file-789",
                "/outputs/report/data.nc",
                "http://localhost",
            ),
            "[data.nc]"
            "(http://localhost/api/v1/files/file-789/"
            "content?attachment=true)",
        )

    def test_file_link_escapes_markdown_display_name(self):
        self.assertEqual(
            idea_pipe._file_link("file-123", "/outputs/a[b].png"),
            "[a\\[b\\].png](/api/v1/files/file-123/content)",
        )

    def test_translates_file_chunk_to_openwebui_preview_link(self):
        chunks = list(
            idea_pipe.Pipe._translate_chunk(
                {
                    "type": "file",
                    "filename": "/outputs/report/final.csv",
                    "openwebui_file_id": "file-123",
                }
            )
        )

        self.assertEqual(
            chunks,
            [
                "\n\n📎 [final.csv]"
                "(/api/v1/files/file-123/content)\n\n"
            ],
        )

    def test_translates_streamed_python_code_to_one_markdown_fence(self):
        chunks = []
        for event in (
            {"type": "python_code_start", "format": "python"},
            {"type": "python_code_delta", "content": "print("},
            {"type": "python_code_delta", "content": "1)"},
            {"type": "python_code_end", "complete": True},
        ):
            chunks.extend(idea_pipe.Pipe._translate_chunk(event))

        self.assertEqual(
            "".join(chunks),
            f"\n\n{idea_pipe.TOOL_OUTPUT_START}\n"
            "````python\nprint(1)\n````\n"
            f"{idea_pipe.TOOL_OUTPUT_END}\n\n",
        )

    def test_translates_python_error_to_labeled_traceback_block(self):
        rendered = "".join(idea_pipe.Pipe._translate_chunk({
            "type": "console",
            "format": "error",
            "content": "Traceback (most recent call last):\nNameError: missing",
        }))

        self.assertIn("⚠️ **Python execution error**", rendered)
        self.assertIn("````text\nTraceback", rendered)
        self.assertIn("NameError: missing\n````", rendered)
        self.assertIn(idea_pipe.TOOL_OUTPUT_START, rendered)
        self.assertIn(idea_pipe.TOOL_OUTPUT_END, rendered)

    def test_streamed_console_deltas_render_in_one_markdown_block(self):
        rendered = "".join(
            part
            for event in (
                {
                    "type": "console", "format": "output",
                    "content": "first\n", "start": True, "end": False,
                },
                {
                    "type": "console", "format": "output",
                    "content": "second\n", "start": False, "end": False,
                },
                {
                    "type": "console", "format": "output",
                    "content": "", "start": False, "end": True,
                },
            )
            for part in idea_pipe.Pipe._translate_chunk(event)
        )

        self.assertEqual(rendered.count(idea_pipe.TOOL_OUTPUT_START), 1)
        self.assertEqual(rendered.count(idea_pipe.TOOL_OUTPUT_END), 1)
        self.assertEqual(rendered.count("````text"), 1)
        self.assertIn("first\nsecond\n", rendered)

    def test_suppresses_only_matching_completed_python_replay(self):
        completed = set()
        self.assertFalse(idea_pipe._is_streamed_python_replay(
            {
                "type": "python_code_end",
                "stream_id": "call-1",
                "complete": True,
            },
            completed,
        ))
        self.assertFalse(idea_pipe._is_streamed_python_replay(
            {
                "type": "code",
                "format": "python",
                "tool_call_id": "different-call",
            },
            completed,
        ))
        self.assertTrue(idea_pipe._is_streamed_python_replay(
            {
                "type": "code",
                "format": "python",
                "tool_call_id": "call-1",
            },
            completed,
        ))
        self.assertEqual(completed, set())

    def test_resolves_displayed_image_to_durable_preview(self):
        rendered, referenced = idea_pipe._resolve_displayed_images(
            ["/workspace/oni/oni plot.png"],
            [{
                "filename": "/outputs/oni/oni plot.png",
                "openwebui_file_id": "file-oni",
            }],
            "http://localhost",
        )

        self.assertEqual(
            rendered,
            "\n\n![generated image]"
            "(http://localhost/api/v1/files/file-oni/content)\n\n",
        )
        self.assertEqual(referenced, {"file-oni"})

    def test_displayed_image_basename_fallback_rejects_ambiguity(self):
        rendered, referenced = idea_pipe._resolve_displayed_images(
            ["/workspace/source/plot.png"],
            [
                {
                    "filename": "/outputs/first/plot.png",
                    "openwebui_file_id": "file-first",
                },
                {
                    "filename": "/outputs/second/plot.png",
                    "openwebui_file_id": "file-second",
                },
            ],
        )

        self.assertIn("image preview unavailable", rendered)
        self.assertEqual(referenced, set())

    def test_missing_displayed_image_never_falls_back_to_base64(self):
        rendered, referenced = idea_pipe._resolve_displayed_images(
            ["/outputs/plot.png"], []
        )

        self.assertEqual(
            rendered,
            "\n\n⚠️ plot.png (image preview unavailable)\n\n",
        )
        self.assertEqual(referenced, set())
        self.assertNotIn("data:image", rendered)

    def test_generated_console_stays_visible_but_is_omitted_from_model_history(self):
        rendered = "".join(idea_pipe.Pipe._translate_chunk({
            "type": "console",
            "content": "Output:\n" + "large-value " * 2000,
        }))
        self.assertIn("large-value", rendered)
        self.assertNotIn("IDEA_TOOL", rendered)

        structured = idea_pipe._structured_messages([
            {"role": "assistant", "content": rendered + "Final finding."},
            {"role": "user", "content": "Continue"},
        ])

        assistant = structured[0]["content"]
        self.assertNotIn("large-value", assistant)
        self.assertIn("tool display omitted", assistant)
        self.assertIn("Final finding", assistant)

    def test_legacy_visible_tool_markers_are_still_removed_from_context(self):
        content = (
            "Before\n<!-- IDEA_TOOL_OUTPUT_START -->\n```text\nsecret\n```\n"
            "<!-- IDEA_TOOL_OUTPUT_END -->\nAfter"
        )
        structured = idea_pipe._structured_messages([
            {"role": "assistant", "content": content},
        ])

        self.assertNotIn("secret", structured[0]["content"])
        self.assertIn("Before", structured[0]["content"])
        self.assertIn("After", structured[0]["content"])

    def test_legacy_large_assistant_history_retains_final_answer(self):
        content = "prefix\n" + ("tool noise " * 5000) + "\nFINAL ANSWER"
        structured = idea_pipe._structured_messages([
            {"role": "assistant", "content": content},
        ])

        assistant = structured[0]["content"]
        self.assertLessEqual(
            len(assistant.encode("utf-8")),
            idea_pipe.MAX_ASSISTANT_MODEL_CONTEXT_BYTES + 10,
        )
        self.assertIn("FINAL ANSWER", assistant)


if __name__ == "__main__":
    unittest.main()
