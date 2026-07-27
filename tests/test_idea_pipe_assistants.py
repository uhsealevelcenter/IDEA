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
