"""
title: IDEA Terminal Agent (LangGraph)
author: IDEA
description: >
    Open WebUI Pipe function that bridges to IDEA's existing langgraph
    microservice (see /langgraph/langgraph_service.py). Reuses
    ConversationOrchestrator / TerminalAgent / sandbox_service exactly as-is -
    this file only translates between Open WebUI's chat protocol and
    langgraph_service's SSE chunk format ({role, type, content, format,
    start, end}, see multi_agent.py: ConversationOrchestrator.chat()).
version: 0.1.0
"""

import json
import posixpath
import re
import httpx
from pydantic import BaseModel, Field
from pathlib import PurePosixPath
from typing import AsyncGenerator, Generator
from urllib.parse import quote, unquote


BASE_MODEL_ID = "idea_terminal_agent.idea-terminal-agent"
BASE_MODEL_ALIASES = {BASE_MODEL_ID, "idea-terminal-agent"}
SANDBOX_MARKDOWN_LINK_RE = re.compile(
    r"\[([^\]]+)\]\((?:(?:sandbox|file):)?(/outputs/[^)\s]+)\)"
)
SANDBOX_URL_RE = re.compile(r"(?:sandbox|file):(/outputs/[^\s)]+)")
INLINE_PREVIEW_EXTENSIONS = {
    ".bmp",
    ".csv",
    ".gif",
    ".htm",
    ".html",
    ".jpeg",
    ".jpg",
    ".json",
    ".md",
    ".pdf",
    ".png",
    ".svg",
    ".txt",
    ".webp",
    ".xml",
}


def _message_content(message: dict) -> str:
    """Return OpenAI message content as text, including multipart text."""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict)
            and part.get("type") in {"text", "input_text"}
            and part.get("text")
        )
    return str(content)


def _latest_user_content(messages: list) -> str:
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            return _message_content(message)
    return ""


def _assistant_system_prompt(messages: list) -> str | None:
    """Collect system messages Open WebUI injected for the selected Assistant."""
    prompts = [
        _message_content(message).strip()
        for message in messages
        if isinstance(message, dict) and message.get("role") == "system"
    ]
    prompts = [prompt for prompt in prompts if prompt]
    return "\n\n".join(prompts) or None


def _selected_assistant_id(metadata: dict | None) -> str | None:
    model = (metadata or {}).get("model")
    if isinstance(model, dict):
        model_id = model.get("id")
        if isinstance(model_id, str) and model_id and model_id not in BASE_MODEL_ALIASES:
            return model_id
    return None


def _request_authorization(request: object | None) -> str | None:
    """Return the current Open WebUI user's bearer credential, if available."""
    if request is None:
        return None

    headers = getattr(request, "headers", {}) or {}
    authorization = headers.get("authorization")
    if (
        isinstance(authorization, str)
        and authorization.lower().startswith("bearer ")
        and authorization[7:].strip()
    ):
        return authorization

    cookies = getattr(request, "cookies", {}) or {}
    token = cookies.get("token")
    if isinstance(token, str) and token.strip():
        return f"Bearer {token.strip()}"
    return None


def _request_public_base_url(request: object | None) -> str:
    """Return the browser-facing Open WebUI origin for absolute file links."""
    if request is None:
        return ""

    headers = getattr(request, "headers", {}) or {}
    forwarded_proto = (headers.get("x-forwarded-proto") or "").split(",", 1)[0]
    scheme = forwarded_proto.strip().lower()
    if scheme not in {"http", "https"}:
        request_url = getattr(request, "url", None)
        scheme = getattr(request_url, "scheme", "") or "http"

    forwarded_host = (headers.get("x-forwarded-host") or "").split(",", 1)[0]
    host = forwarded_host.strip() or (headers.get("host") or "").strip()
    if host:
        return f"{scheme}://{host}".rstrip("/")

    base_url = str(getattr(request, "base_url", "") or "")
    return base_url.rstrip("/")


def _sanitize_sandbox_links(content: str) -> str:
    """Render sandbox-only output paths as text; real attachments follow."""
    content = SANDBOX_MARKDOWN_LINK_RE.sub(
        lambda match: f"{match.group(1)}: `{match.group(2)}`",
        content,
    )
    return SANDBOX_URL_RE.sub(
        lambda match: f"`{match.group(1)}`",
        content,
    )


def _download_url(file_id: str, public_base_url: str = "") -> str:
    path = f"/api/v1/files/{file_id}/content?attachment=true"
    return f"{public_base_url.rstrip('/')}{path}" if public_base_url else path


def _preview_url(
    file_id: str,
    filename: str,
    public_base_url: str = "",
) -> str:
    display_name = PurePosixPath(filename).name or "file"
    path = f"/idea-file-preview/{quote(file_id, safe='')}/{quote(display_name)}"
    return f"{public_base_url.rstrip('/')}{path}" if public_base_url else path


def _file_url(
    file_id: str,
    filename: str,
    public_base_url: str = "",
) -> str:
    if PurePosixPath(filename).suffix.lower() in INLINE_PREVIEW_EXTENSIONS:
        return _preview_url(file_id, filename, public_base_url)
    return _download_url(file_id, public_base_url)


def _file_link(
    file_id: str,
    filename: str,
    public_base_url: str = "",
) -> str:
    """Return a filename-only link that opens the output in a new tab."""
    display_name = PurePosixPath(filename).name or "file"
    url = _file_url(file_id, filename, public_base_url)
    markdown_label = (
        display_name.replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )
    # Open WebUI's MarkdownInlineTokens component renders Markdown links
    # with target="_blank"; keeping this standard Markdown also avoids raw
    # HTML sanitization differences across Open WebUI releases.
    return f"[{markdown_label}]({url})"


def _normalized_output_path(filepath: str) -> str:
    """Normalize URL-encoded model placeholders for exact file-map lookup."""
    return posixpath.normpath(unquote(filepath))


def _resolve_output_links(
    content: str,
    synced_files: list[dict],
    public_base_url: str = "",
) -> tuple[str, set[str]]:
    """Replace sandbox output URLs with their real Open WebUI file URLs."""
    files_by_path = {
        item.get("filename"): item.get("openwebui_file_id")
        for item in synced_files
        if item.get("filename") and item.get("openwebui_file_id")
    }
    referenced_file_ids: set[str] = set()

    def replace_markdown(match: re.Match) -> str:
        label, filepath = match.groups()
        normalized_path = _normalized_output_path(filepath)
        file_id = files_by_path.get(normalized_path)
        if not file_id:
            return f"⚠️ {label} (output link unavailable)"
        referenced_file_ids.add(file_id)
        return _file_link(file_id, normalized_path, public_base_url)

    content = SANDBOX_MARKDOWN_LINK_RE.sub(replace_markdown, content)

    def replace_url(match: re.Match) -> str:
        filepath = match.group(1)
        normalized_path = _normalized_output_path(filepath)
        file_id = files_by_path.get(normalized_path)
        if not file_id:
            display_name = PurePosixPath(filepath).name or filepath
            return f"⚠️ {display_name} (output link unavailable)"
        referenced_file_ids.add(file_id)
        return _file_link(file_id, normalized_path, public_base_url)

    return SANDBOX_URL_RE.sub(replace_url, content), referenced_file_ids


class Pipe:
    class Valves(BaseModel):
        LANGGRAPH_SERVICE_URL: str = Field(
            default="http://langgraph:8010",
            description="Base URL of the langgraph_service.py microservice.",
        )
        MODEL: str = Field(
            default="gpt-5.6-sol",
            description="Model name passed through to ConversationOrchestrator.",
        )
        REQUEST_TIMEOUT_SECONDS: int = Field(
            default=1800,
            description="Matches the terminal agent's own 30-minute exec timeout.",
        )
        INTERNAL_SERVICE_TOKEN: str = Field(
            default="",
            description=(
                "Must match INTERNAL_SERVICE_TOKEN in the langgraph service's "
                "own .env (docker-compose.yml) - sent as a Bearer token on "
                "every request. Leave blank only if langgraph_service.py's "
                "own copy is also unset (dev-only; see example.env)."
            ),
        )

    def __init__(self):
        self.valves = self.Valves()

    def pipes(self) -> list[dict]:
        """Registers this as a single selectable model in Open WebUI's model dropdown."""
        return [{"id": "idea-terminal-agent", "name": "IDEA Terminal Agent"}]

    async def pipe(
        self,
        body: dict,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
        __request__: object | None = None,
    ) -> AsyncGenerator[str, None]:
        messages = body.get("messages", [])
        if not messages:
            return

        user_content = _latest_user_content(messages)
        if not user_content:
            return
        assistant_system_prompt = _assistant_system_prompt(messages)
        assistant_id = _selected_assistant_id(__metadata__)
        public_base_url = _request_public_base_url(__request__)

        user = __user__ or {}
        # Open WebUI's own user id becomes the sandbox/session identity that
        # langgraph_service.py + sandbox_service key everything off of - see
        # IMPLEMENTATION_STATUS.md "Stage 1 - Dedicated per-user sandbox".
        user_id = str(user.get("id") or "anonymous")
        # Used only for LiteLLM per-end-user spend tracking (see
        # agents/terminal_agent.py's LITELLM_END_USER_HEADER) - not used
        # for sandbox/session identity, which stays keyed off user_id above.
        user_email = user.get("email") or None
        # Open WebUI's per-conversation chat_id keeps history scoped per chat
        # the same way the existing frontend's browser_session_id does.
        session_id = str(
            body.get("chat_id")
            or (__metadata__ or {}).get("chat_id")
            or "default"
        )
        # Open WebUI's built-in "pending" role covers users awaiting admin
        # approval; treat anyone without a full "user"/"admin" role as guest,
        # matching this repo's existing guest-vs-registered distinction.
        is_guest = user.get("role") not in ("user", "admin")

        payload = {
            # Keep Redis conversation history separate when the same Open
            # WebUI chat is deliberately switched to another Assistant.
            "session_key": f"{user_id}:{session_id}:{assistant_id or BASE_MODEL_ID}",
            "user_id": user_id,
            "user_email": user_email,
            "is_guest": is_guest,
            "message": user_content,
            "model": self.valves.MODEL,
            "assistant_id": assistant_id,
            "assistant_system_prompt": assistant_system_prompt,
            # Used only by langgraph's final /outputs upload. It is never
            # added to model messages or persisted conversation history.
            "openwebui_authorization": _request_authorization(__request__),
        }

        headers = (
            {"Authorization": f"Bearer {self.valves.INTERNAL_SERVICE_TOKEN}"}
            if self.valves.INTERNAL_SERVICE_TOKEN
            else {}
        )

        message_buffer: list[str] = []
        pending_files: list[dict] = []

        def flush_message_buffer() -> str:
            content = _sanitize_sandbox_links("".join(message_buffer))
            message_buffer.clear()
            return content

        try:
            timeout = httpx.Timeout(self.valves.REQUEST_TIMEOUT_SECONDS)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.valves.LANGGRAPH_SERVICE_URL}/chat",
                    json=payload,
                    headers=headers,
                ) as response:
                    response.raise_for_status()
                    async for raw_line in response.aiter_lines():
                        if not raw_line or not raw_line.startswith("data: "):
                            continue
                        data = raw_line[len("data: "):]
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue

                        if chunk.get("type") == "message":
                            message_buffer.append(chunk.get("content", ""))
                            continue

                        if chunk.get("type") == "file":
                            pending_files.append(chunk)
                            continue

                        if message_buffer:
                            yield flush_message_buffer()
                        for translated in self._translate_chunk(
                            chunk,
                            public_base_url,
                        ):
                            yield translated
        except httpx.HTTPError as exc:
            if message_buffer:
                yield flush_message_buffer()
            yield f"\n\n**Error reaching langgraph service:** {exc}\n\n"
            return

        referenced_file_ids: set[str] = set()
        if message_buffer:
            resolved_message, referenced_file_ids = _resolve_output_links(
                "".join(message_buffer),
                pending_files,
                public_base_url,
            )
            message_buffer.clear()
            yield resolved_message

        for file_chunk in pending_files:
            if file_chunk.get("openwebui_file_id") in referenced_file_ids:
                continue
            for translated in self._translate_chunk(
                file_chunk,
                public_base_url,
            ):
                yield translated

    @staticmethod
    def _translate_chunk(
        chunk: dict,
        public_base_url: str = "",
    ) -> Generator[str, None, None]:
        """
        Converts one langgraph_service SSE chunk into markdown text for Open
        WebUI's chat stream. Mirrors the rendering logic currently done in
        frontend/assistant.js (processChunk/appendMessage) but collapsed into
        plain markdown since Open WebUI's chat pane has no bespoke
        code/console/image chunk types of its own.
        """
        if "error" in chunk:
            yield f"\n\n**Error:** {chunk['error']}\n\n"
            return

        chunk_type = chunk.get("type")
        content = chunk.get("content", "")
        fmt = chunk.get("format")

        if chunk_type == "message":
            # Plain assistant text - stream through unchanged.
            yield content
        elif chunk_type == "code":
            lang = fmt or ""
            yield f"\n\n```{lang}\n{content}\n```\n\n"
        elif chunk_type == "console":
            yield f"\n\n```text\n{content}\n```\n\n"
        elif chunk_type == "image" and content:
            ext = (fmt or "base64.png").split(".")[-1]
            yield f"\n\n![generated image](data:image/{ext};base64,{content})\n\n"
        elif chunk_type == "heartbeat":
            # Keeps bytes flowing over the wire during a long silent
            # blocking tool call (see TerminalAgent._invoke_with_heartbeat)
            # so nothing along the chain mistakes the connection for dead.
            # Empty string is a no-op content delta for Open WebUI.
            yield ""
        elif chunk_type == "file":
            # terminal_agent.py already uploaded this file to Open WebUI's own
            # Files API (see TerminalAgent._sync_outputs_to_openwebui) and
            # handed back its file id - just link to Open WebUI's own
            # storage, no bytes to translate here.
            file_id = chunk.get("openwebui_file_id")
            filename = chunk.get("filename") or "file"
            if file_id:
                yield f"\n\n📎 {_file_link(file_id, filename, public_base_url)}\n\n"
