import importlib.util
import asyncio
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
        response_context = Mock()
        response_context.__aenter__ = AsyncMock(return_value=response)
        response_context.__aexit__ = AsyncMock(return_value=None)
        client = Mock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.stream.return_value = response_context
        body = {
            "messages": [
                {"role": "system", "content": "You are SEA."},
                {"role": "user", "content": "Analyze Honolulu."},
            ]
        }
        metadata = {
            "chat_id": "chat-123",
            "model": {"id": "sea", "name": "SEA"},
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
        payload = client.stream.call_args.kwargs["json"]
        self.assertEqual(payload["assistant_id"], "sea")
        self.assertEqual(payload["assistant_system_prompt"], "You are SEA.")
        self.assertEqual(payload["session_key"], "user-1:chat-123:sea")
        self.assertEqual(payload["message"], "Analyze Honolulu.")
        self.assertEqual(
            payload["openwebui_authorization"],
            "Bearer user-session-token",
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
            "[figure.png](/idea-file-preview/file-123/figure.png)",
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
            "[figure.png](/idea-file-preview/file-123/figure.png)",
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
            "[data.csv](/idea-file-preview/file-456/data.csv)",
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
        response_context = Mock()
        response_context.__aenter__ = AsyncMock(return_value=response)
        response_context.__aexit__ = AsyncMock(return_value=None)
        client = Mock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.stream.return_value = response_context

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
                "[figure.png](/idea-file-preview/file-123/figure.png)"
            ],
        )

    def test_pipe_yields_ordinary_assistant_text_before_stream_finishes(self):
        response = Mock()
        response.raise_for_status.return_value = None
        release_second_chunk = asyncio.Event()

        async def aiter_lines():
            yield 'data: {"type":"message","content":"Working now"}'
            await release_second_chunk.wait()
            yield 'data: {"type":"message","content":" and done"}'

        response.aiter_lines = aiter_lines
        response_context = Mock()
        response_context.__aenter__ = AsyncMock(return_value=response)
        response_context.__aexit__ = AsyncMock(return_value=None)
        client = Mock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.stream.return_value = response_context

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
        response_context = Mock()
        response_context.__aenter__ = AsyncMock(return_value=response)
        response_context.__aexit__ = AsyncMock(return_value=None)
        client = Mock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.stream.return_value = response_context
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
        response_context = Mock()
        response_context.__aenter__ = AsyncMock(return_value=response)
        response_context.__aexit__ = AsyncMock(return_value=None)
        client = Mock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.stream.return_value = response_context

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
                "[figure.png](/idea-file-preview/file-123/figure.png)",
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
            "(http://localhost/idea-file-preview/file-123/random_scatter.png)",
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
            "[a\\[b\\].png](/idea-file-preview/file-123/a%5Bb%5D.png)",
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
                "(/idea-file-preview/file-123/final.csv)\n\n"
            ],
        )


if __name__ == "__main__":
    unittest.main()
