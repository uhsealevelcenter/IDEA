"""
title: IDEA Agent (LangGraph)
author: IDEA
description: >
    Open WebUI Pipe function that bridges to IDEA's existing langgraph
    microservice (see /langgraph/langgraph_service.py). Reuses
    ConversationOrchestrator / TerminalAgent / sandbox_service exactly as-is -
    this file only translates between Open WebUI's chat protocol and
    langgraph_service's SSE chunk format ({role, type, content, format,
    start, end}, see multi_agent.py: ConversationOrchestrator.chat()).
version: 0.2.0
"""

import asyncio
import json
import os
import posixpath
import re
import httpx
from pydantic import BaseModel, Field
from pathlib import PurePosixPath
from typing import AsyncGenerator, Awaitable, Callable, Generator
from urllib.parse import quote, unquote


BASE_MODEL_ID = "idea_terminal_agent.idea-terminal-agent"
BASE_MODEL_ALIASES = {BASE_MODEL_ID, "idea-terminal-agent"}
TERMINAL_RUN_STATUSES = {
    "completed", "stopped", "failed", "cancelled-before-start"
}
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
ARTIFACT_TARGET_PREFIXES = (
    "/outputs/",
    "sandbox:/outputs/",
    "file:/outputs/",
)
MAX_PENDING_MARKDOWN_LABEL = 512
RAW_ARTIFACT_REFERENCE_RE = re.compile(r"(?:sandbox|file):/outputs/")
MARKDOWN_ARTIFACT_REFERENCE_RE = re.compile(
    rf"\[[^\]\n]{{0,{MAX_PENDING_MARKDOWN_LABEL}}}\]\("
    r"(?:(?:sandbox|file):)?/outputs/"
)
ASSISTANT_INLINE_IMAGE_MARKDOWN_RE = re.compile(
    r"!\[[^\]\n]*\]\(data:image/[^;\s)]+;base64,[A-Za-z0-9+/=]+\)"
)
INLINE_IMAGE_DATA_URI_RE = re.compile(
    r"data:image/[^;\s)]+;base64,[A-Za-z0-9+/=]+"
)
CONVERSATION_SUMMARY_MARKER = "[CONVERSATION SUMMARY]"
LEGACY_TOOL_OUTPUT_START = "<!-- IDEA_TOOL_OUTPUT_START -->"
LEGACY_TOOL_OUTPUT_END = "<!-- IDEA_TOOL_OUTPUT_END -->"
# Open WebUI escapes HTML comments in assistant Markdown, so readable comment
# markers leak into chat. These Unicode format characters are non-rendering
# delimiters: the tool content remains visible, while the next request can
# still distinguish display-only blocks from model conversation context.
TOOL_OUTPUT_START = "\u2063\u2064\u2063\u2064"
TOOL_OUTPUT_END = "\u2064\u2063\u2064\u2063"
TOOL_OUTPUT_BLOCK_RE = re.compile(
    r"(?:"
    + re.escape(TOOL_OUTPUT_START)
    + r".*?"
    + re.escape(TOOL_OUTPUT_END)
    + r"|"
    + re.escape(LEGACY_TOOL_OUTPUT_START)
    + r".*?"
    + re.escape(LEGACY_TOOL_OUTPUT_END)
    + r")",
    re.DOTALL,
)
LEGACY_CONSOLE_BLOCK_RE = re.compile(
    r"```text\s*\n(?:(?:✓|✗) Command .*?|Output:\s*.*?|"
    r"Calling [A-Za-z_][A-Za-z0-9_]*\(.*?|"
    r"\{\s*\"source\"\s*:\s*\"(?:builtin|workspace)\".*?)\n```",
    re.DOTALL,
)
MAX_ASSISTANT_MODEL_CONTEXT_BYTES = int(
    os.getenv("IDEA_MAX_MODEL_HISTORY_MESSAGE_BYTES", "16000")
)


def _split_streamable_message(content: str) -> tuple[str, str, bool]:
    """
    Split assistant text into a safe-to-stream prefix and a retained tail.

    Artifact URLs cannot be resolved until the LangGraph service emits its
    final ``file`` chunks. Ordinary text should not wait for that. Retain only
    a possible/confirmed artifact reference (including references split across
    model-token boundaries) and stream everything before it immediately.
    """
    confirmed_starts = [
        match.start()
        for pattern in (
            RAW_ARTIFACT_REFERENCE_RE,
            MARKDOWN_ARTIFACT_REFERENCE_RE,
        )
        if (match := pattern.search(content))
    ]
    if confirmed_starts:
        start = min(confirmed_starts)
        return content[:start], content[start:], True

    possible_starts: list[int] = []

    # Preserve a suffix that may be the beginning of a raw artifact URL.
    for prefix in ("sandbox:/outputs/", "file:/outputs/"):
        for length in range(1, min(len(content), len(prefix) - 1) + 1):
            if content.endswith(prefix[:length]):
                possible_starts.append(len(content) - length)

    # Preserve an incomplete Markdown link only while it can still become an
    # output link. Once its target is visibly something else, it is safe to
    # stream. The label cap prevents an unmatched "[" from buffering an
    # otherwise unbounded response.
    markdown_start = content.rfind("[")
    if markdown_start >= 0:
        candidate = content[markdown_start:]
        close_label = candidate.find("]")
        if close_label < 0:
            if (
                "\n" not in candidate
                and len(candidate) <= MAX_PENDING_MARKDOWN_LABEL
            ):
                possible_starts.append(markdown_start)
        else:
            after_label = candidate[close_label + 1:]
            if not after_label:
                possible_starts.append(markdown_start)
            elif after_label.startswith("("):
                target_fragment = after_label[1:]
                if not target_fragment or any(
                    prefix.startswith(target_fragment)
                    for prefix in ARTIFACT_TARGET_PREFIXES
                ):
                    possible_starts.append(markdown_start)

    if not possible_starts:
        return content, "", False

    start = min(possible_starts)
    return content[:start], content[start:], False


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


def _structured_messages(messages: list[dict]) -> list[dict]:
    """Preserve branch history and demote compaction summaries to context.

    Assistant policy remains in ``_assistant_system_prompt``. Open WebUI
    appends its generated conversation summary to that system content; the
    summary is data about prior turns, not policy, so forward it separately
    for LangGraph to present as human-style conversation context.
    """
    result: list[dict] = []
    summaries = _conversation_summaries(messages)
    if summaries:
        result.append({
            "id": "",
            "role": "system",
            "content": "\n\n".join(summaries),
        })
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = message.get("content", "")
        if not isinstance(content, (str, list)):
            content = str(content)
        if role == "assistant":
            content = _sanitize_assistant_image_history(content)
            content = _sanitize_assistant_tool_history(content)
        result.append({
            "id": str(message.get("id") or ""),
            "role": role,
            "content": content,
        })
    return result


def _sanitize_assistant_image_history(content: str | list) -> str | list:
    """Remove persisted generated-image bytes before model context assembly."""
    marker = "[Generated image omitted from model context; use its file link.]"
    if isinstance(content, str):
        content = ASSISTANT_INLINE_IMAGE_MARKDOWN_RE.sub(marker, content)
        return INLINE_IMAGE_DATA_URI_RE.sub("[inline image omitted]", content)

    sanitized: list = []
    for item in content:
        if not isinstance(item, dict):
            sanitized.append(item)
            continue
        item = dict(item)
        item_type = item.get("type")
        image_url = item.get("image_url")
        url = image_url.get("url") if isinstance(image_url, dict) else image_url
        if item_type in {"image", "image_url"} and isinstance(url, str) and url.startswith("data:image/"):
            sanitized.append({"type": "text", "text": marker})
            continue
        for key in ("text", "content"):
            if isinstance(item.get(key), str):
                item[key] = _sanitize_assistant_image_history(item[key])
        sanitized.append(item)
    return sanitized


def _bounded_model_history_text(content: str) -> str:
    encoded = content.encode("utf-8")
    if len(encoded) <= MAX_ASSISTANT_MODEL_CONTEXT_BYTES:
        return content
    marker = (
        "\n\n[Earlier display-only assistant/tool output omitted from model "
        "context.]\n\n"
    )
    # The final answer is normally at the end of an Open WebUI assistant
    # message, so retain substantially more of the suffix than the prefix.
    head = encoded[:3000].decode("utf-8", errors="ignore")
    remaining = MAX_ASSISTANT_MODEL_CONTEXT_BYTES - len(
        (head + marker).encode("utf-8")
    )
    tail = encoded[-max(remaining, 0):].decode("utf-8", errors="ignore")
    return head + marker + tail


def _sanitize_assistant_tool_history(content: str | list) -> str | list:
    """Keep UI transcript details out of subsequent model requests."""
    replacement = "[IDEA tool display omitted; durable execution memory is authoritative.]"
    if isinstance(content, str):
        content = TOOL_OUTPUT_BLOCK_RE.sub(replacement, content)
        content = LEGACY_CONSOLE_BLOCK_RE.sub(replacement, content)
        return _bounded_model_history_text(content)
    sanitized: list = []
    for item in content:
        if not isinstance(item, dict):
            sanitized.append(item)
            continue
        item = dict(item)
        for key in ("text", "content"):
            if isinstance(item.get(key), str):
                item[key] = _sanitize_assistant_tool_history(item[key])
        sanitized.append(item)
    return sanitized


def _latest_idea_context(messages: list[dict]) -> dict:
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        meta = message.get("meta") or message.get("metadata") or {}
        context = meta.get("idea_context") if isinstance(meta, dict) else None
        if isinstance(context, dict) and context.get("schema_version") == 1:
            return context
    return {}


def _latest_user_content(messages: list) -> str:
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            return _message_content(message)
    return ""


def _assistant_system_prompt(messages: list) -> str | None:
    """Collect system messages Open WebUI injected for the selected Assistant."""
    prompts = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        content = _message_content(message)
        policy, _, _ = content.partition(CONVERSATION_SUMMARY_MARKER)
        if policy.strip():
            prompts.append(policy.strip())
    prompts = [prompt for prompt in prompts if prompt]
    return "\n\n".join(prompts) or None


def _conversation_summaries(messages: list) -> list[str]:
    summaries = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        content = _message_content(message)
        _, marker, summary = content.partition(CONVERSATION_SUMMARY_MARKER)
        if marker and summary.strip():
            summaries.append(summary.strip())
    return summaries


def _selected_assistant_id(metadata: dict | None) -> str | None:
    metadata = metadata or {}
    model = metadata.get("model")
    if isinstance(model, dict):
        model_id = model.get("id")
        if isinstance(model_id, str) and model_id and model_id not in BASE_MODEL_ALIASES:
            return model_id
    model_id = metadata.get("model_id")
    if (
        isinstance(model_id, str)
        and model_id
        and model_id not in BASE_MODEL_ALIASES
    ):
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


def _attached_resource_descriptors(
    files: list[dict] | None,
    metadata: dict | None,
    body_files: list[dict] | None = None,
) -> list[dict]:
    """Return safe file/collection IDs without trusting client paths.

    Collection descriptors must survive the Pipe boundary so LangGraph can
    resolve their current members through Open WebUI using the current user's
    credential. LangGraph, not the model, performs that authorization.

    Open WebUI supplies direct chat attachments through ``__files__`` and
    injects an Assistant's persistent Knowledge collections into
    request metadata and, transiently, ``body["files"]`` in legacy
    function-calling mode, so all sources are required. LangGraph still
    resolves every opaque ID through Open WebUI using the current user's
    credential.
    """
    candidates = list(files or [])
    candidates.extend(body_files or [])
    user_message = (metadata or {}).get("user_message")
    if isinstance(user_message, dict):
        candidates.extend(user_message.get("files") or [])
    model = (metadata or {}).get("model")
    if isinstance(model, dict):
        model_info = model.get("info")
        model_meta = (
            model_info.get("meta")
            if isinstance(model_info, dict)
            else model.get("meta")
        )
        if isinstance(model_meta, dict):
            candidates.extend(model_meta.get("knowledge") or [])

    descriptors: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type", "file")
        if item_type not in {"file", "image", "collection"}:
            continue
        file_id = item.get("id") or item.get("url")
        if (
            not isinstance(file_id, str)
            or not file_id
            or (item_type, file_id) in seen
            or file_id.startswith(("http://", "https://", "data:"))
            or "/" in file_id
            or "\\" in file_id
            or any(ord(char) < 32 for char in file_id)
        ):
            continue

        descriptor = {"id": file_id, "type": item_type}
        name = item.get("name") or item.get("filename")
        if isinstance(name, str) and name:
            descriptor["name"] = name
        content_type = item.get("content_type")
        if isinstance(content_type, str) and content_type:
            descriptor["content_type"] = content_type
        size = item.get("size")
        if isinstance(size, int) and size >= 0:
            descriptor["size"] = size

        descriptors.append(descriptor)
        seen.add((item_type, file_id))
    return descriptors


# Backward-compatible name for callers/tests that only care about files.
_attached_file_descriptors = _attached_resource_descriptors


def _configured_paperqa_assistants(value: str) -> set[str]:
    return {
        assistant_id.strip()
        for assistant_id in value.split(",")
        if assistant_id.strip()
    }


def _paperqa_enabled(
    assistant_id: str | None,
    is_guest: bool,
    configured_ids: str,
) -> bool:
    return bool(
        assistant_id
        and not is_guest
        and assistant_id in _configured_paperqa_assistants(configured_ids)
    )


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


def _resolve_displayed_images(
    image_paths: list[str],
    synced_files: list[dict],
    public_base_url: str = "",
) -> tuple[str, set[str]]:
    """Render displayed sandbox images through durable Open WebUI files."""
    files_by_path = {
        item.get("filename"): item.get("openwebui_file_id")
        for item in synced_files
        if item.get("filename") and item.get("openwebui_file_id")
    }
    files_by_basename: dict[str, list[tuple[str, str]]] = {}
    for file_path, file_id in files_by_path.items():
        files_by_basename.setdefault(
            PurePosixPath(file_path).name, []
        ).append((file_path, file_id))
    rendered: list[str] = []
    referenced_file_ids: set[str] = set()
    seen: set[str] = set()
    for raw_path in image_paths:
        path = _normalized_output_path(raw_path)
        if path in seen:
            continue
        seen.add(path)
        resolved_path = path
        file_id = files_by_path.get(resolved_path)
        if not file_id and path.startswith("/workspace/"):
            # publish_artifact_tool normally preserves the private source's
            # workspace-relative path under /outputs. A model may show the
            # source and publish it in the same tool batch, so the image and
            # uploaded-file events legitimately have different roots.
            published_path = "/outputs/" + path.removeprefix("/workspace/")
            if published_path in files_by_path:
                resolved_path = published_path
                file_id = files_by_path[published_path]
        if not file_id:
            # If publication intentionally changed directories, accept a
            # basename match only when it identifies exactly one upload.
            matches = files_by_basename.get(PurePosixPath(path).name, [])
            if len(matches) == 1:
                resolved_path, file_id = matches[0]
        if not file_id:
            display_name = PurePosixPath(path).name or path
            rendered.append(f"⚠️ {display_name} (image preview unavailable)")
            continue
        referenced_file_ids.add(file_id)
        url = _file_url(file_id, resolved_path, public_base_url)
        rendered.append(f"![generated image]({url})")
    if not rendered:
        return "", referenced_file_ids
    return "\n\n" + "\n\n".join(rendered) + "\n\n", referenced_file_ids


def _is_streamed_python_replay(chunk: dict, completed_stream_ids: set[str]) -> bool:
    """Track completed code streams and identify their legacy full replay."""
    chunk_type = chunk.get("type")
    stream_id = str(chunk.get("stream_id") or "")
    if chunk_type == "python_code_end" and chunk.get("complete") and stream_id:
        completed_stream_ids.add(stream_id)
        return False
    tool_call_id = str(chunk.get("tool_call_id") or "")
    if (
        chunk_type == "code"
        and chunk.get("format") == "python"
        and tool_call_id in completed_stream_ids
    ):
        completed_stream_ids.discard(tool_call_id)
        return True
    return False


class Pipe:
    class Valves(BaseModel):
        LANGGRAPH_SERVICE_URL: str = Field(
            default="http://langgraph:8010",
            description="Base URL of the langgraph_service.py microservice.",
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
        PAPERQA_ASSISTANT_IDS: str = Field(
            default="welcome-assistant,sea,mars-assistant",
            description=(
                "Comma-separated Assistant IDs for which attached Knowledge "
                "collections and direct PDFs are handled exclusively by PaperQA2."
            ),
        )

    def __init__(self):
        self.valves = self.Valves()

    def pipes(self) -> list[dict]:
        """Registers this as a single selectable model in Open WebUI's model dropdown."""
        return [{"id": "idea-terminal-agent", "name": "IDEA Agent"}]

    async def pipe(
        self,
        body: dict,
        __user__: dict | None = None,
        __files__: list[dict] | None = None,
        __metadata__: dict | None = None,
        __request__: object | None = None,
        __event_emitter__: (
            Callable[[dict], Awaitable[None]] | None
        ) = None,
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
        paperqa_enabled = _paperqa_enabled(
            assistant_id,
            is_guest,
            self.valves.PAPERQA_ASSISTANT_IDS,
        )

        idea_context = _latest_idea_context(messages)
        payload = {
            "session_id": session_id,
            "user_id": user_id,
            "user_email": user_email,
            "is_guest": is_guest,
            "messages": _structured_messages(messages),
            "response_message_id": str(
                (__metadata__ or {}).get("message_id") or ""
            ) or None,
            "input_checkpoint_id": idea_context.get("output_checkpoint_id"),
            "idea_context": idea_context,
            "assistant_id": assistant_id,
            "assistant_system_prompt": assistant_system_prompt,
            "paperqa_enabled": paperqa_enabled,
            "attached_files": _attached_resource_descriptors(
                __files__,
                __metadata__,
                body.get("files"),
            ),
            # Used only by langgraph's final /outputs upload. It is never
            # added to model messages or persisted conversation history.
            "openwebui_authorization": _request_authorization(__request__),
        }

        headers = (
            {"Authorization": f"Bearer {self.valves.INTERNAL_SERVICE_TOKEN}"}
            if self.valves.INTERNAL_SERVICE_TOKEN
            else {}
        )

        message_buffer = ""
        artifact_reference_confirmed = False
        pending_files: list[dict] = []
        pending_images: list[str] = []
        completed_python_stream_ids: set[str] = set()
        status_done = False
        stop_sent = False
        error_event_received = False

        async def emit_status(status: dict) -> None:
            """Forward a LangGraph phase to Open WebUI's native status UI."""
            nonlocal status_done
            if status.get("done"):
                status_done = True
            if not __event_emitter__:
                return
            data = {
                key: status[key]
                for key in (
                    "action",
                    "phase",
                    "description",
                    "done",
                    "tool_name",
                    "error",
                )
                if key in status
            }
            try:
                await __event_emitter__({
                    "type": "status",
                    "data": data,
                })
            except Exception:
                # Status display is best-effort and must never interrupt the
                # actual assistant response.
                return

        async def request_backend_stop() -> None:
            """Send at most one cooperative stop request for this run."""
            nonlocal stop_sent
            if not run_id or run_terminal or stop_sent:
                return
            stop_sent = True
            try:
                async with httpx.AsyncClient(timeout=10) as stop_client:
                    await stop_client.post(
                        f"{self.valves.LANGGRAPH_SERVICE_URL}/chat-runs/{run_id}/stop",
                        headers=headers,
                    )
            except Exception:
                # The local terminal status is more important than surfacing a
                # cleanup error after the browser has already disconnected.
                pass

        def flush_message_buffer() -> str:
            nonlocal message_buffer, artifact_reference_confirmed
            content = _sanitize_sandbox_links(message_buffer)
            message_buffer = ""
            artifact_reference_confirmed = False
            return content

        await emit_status({
            "action": "idea_agent",
            "phase": "starting",
            "description": "Working on your request…",
            "done": False,
        })

        run_id = ""
        run_terminal = False
        try:
            timeout = httpx.Timeout(self.valves.REQUEST_TIMEOUT_SECONDS)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self.valves.LANGGRAPH_SERVICE_URL}/chat-runs",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                run_id = str(response.json().get("run_id") or "")
                if not run_id:
                    raise httpx.HTTPError("LangGraph did not return a run_id")
                after = 0
                while True:
                    response = await client.get(
                        f"{self.valves.LANGGRAPH_SERVICE_URL}/chat-runs/{run_id}/events",
                        params={"after": after},
                        headers=headers,
                    )
                    response.raise_for_status()
                    response_data = response.json()
                    for event in response_data.get("events") or []:
                        after = max(after, int(event.get("seq") or 0))
                        chunk = event.get("chunk")
                        if isinstance(chunk, str):
                            if artifact_reference_confirmed:
                                message_buffer += chunk
                                continue
                            streamable, message_buffer, confirmed = (
                                _split_streamable_message(message_buffer + chunk)
                            )
                            artifact_reference_confirmed = confirmed
                            if streamable:
                                yield streamable
                            continue
                        if not isinstance(chunk, dict):
                            continue
                        if _is_streamed_python_replay(
                            chunk, completed_python_stream_ids
                        ):
                            continue
                        if chunk.get("type") == "idea_context":
                            if __event_emitter__:
                                try:
                                    await __event_emitter__({
                                        "type": "message_meta",
                                        "data": {"idea_context": chunk},
                                    })
                                except Exception:
                                    pass
                            continue
                        if chunk.get("type") == "status":
                            await emit_status(chunk)
                            continue
                        if "error" in chunk:
                            error_event_received = True
                            # Preserve a preceding classified terminal status
                            # (for example model_timeout) instead of replacing
                            # its actionable description with a generic one.
                            if not status_done:
                                await emit_status({
                                    "action": "idea_agent",
                                    "phase": "failed",
                                    "description": "IDEA encountered an error",
                                    "done": True,
                                    "error": True,
                                })
                        if chunk.get("type") == "message":
                            content = chunk.get("content", "")
                            if artifact_reference_confirmed:
                                message_buffer += content
                                continue
                            streamable, message_buffer, confirmed = (
                                _split_streamable_message(message_buffer + content)
                            )
                            artifact_reference_confirmed = confirmed
                            if streamable:
                                yield streamable
                            continue
                        if chunk.get("type") == "file":
                            pending_files.append(chunk)
                            continue
                        if chunk.get("type") == "image" and chunk.get("filename"):
                            pending_images.append(str(chunk["filename"]))
                            continue
                        if message_buffer:
                            yield flush_message_buffer()
                        for translated in self._translate_chunk(chunk, public_base_url):
                            yield translated

                    status = str(response_data.get("status") or "")
                    if status in TERMINAL_RUN_STATUSES:
                        run_terminal = True
                        # LangGraph normally publishes the failure as an event
                        # and repeats it in terminal run metadata. Render the
                        # metadata only as a fallback for older/interrupted
                        # backends that did not publish an error event.
                        if (
                            status == "failed"
                            and response_data.get("error")
                            and not error_event_received
                        ):
                            yield f"\n\n**Error:** {response_data['error']}\n\n"
                        break
                    await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            # Open WebUI cancels this generator when the user presses Stop.
            # Clear its last non-terminal status immediately; polling has
            # already ended, so it cannot observe the backend's stop event.
            await emit_status({
                "action": "idea_agent",
                "phase": "stopped",
                "description": "Stopped",
                "done": True,
            })
            await asyncio.shield(request_backend_stop())
            raise
        except httpx.HTTPError as exc:
            await emit_status({
                "action": "idea_agent",
                "phase": "failed",
                "description": "Unable to reach the IDEA agent",
                "done": True,
                "error": True,
            })
            if message_buffer:
                yield flush_message_buffer()
            yield f"\n\n**Error reaching langgraph service:** {exc}\n\n"
            return
        finally:
            # The stock Open WebUI Stop cancels this generator. Convert that
            # disconnect into a backend stop instead of leaving work running.
            # Generator close paths do not always surface as CancelledError,
            # so also terminalize any still-active status here.
            if run_id and not run_terminal and not status_done:
                await emit_status({
                    "action": "idea_agent",
                    "phase": "stopped",
                    "description": "Stopped",
                    "done": True,
                })
            await request_backend_stop()

        referenced_file_ids: set[str] = set()
        if message_buffer:
            resolved_message, referenced_file_ids = _resolve_output_links(
                message_buffer,
                pending_files,
                public_base_url,
            )
            message_buffer = ""
            yield resolved_message

        rendered_images, image_file_ids = _resolve_displayed_images(
            pending_images,
            pending_files,
            public_base_url,
        )
        referenced_file_ids.update(image_file_ids)
        if rendered_images:
            yield rendered_images

        for file_chunk in pending_files:
            if file_chunk.get("openwebui_file_id") in referenced_file_ids:
                continue
            for translated in self._translate_chunk(
                file_chunk,
                public_base_url,
            ):
                yield translated

        if not status_done:
            await emit_status({
                "action": "idea_agent",
                "phase": "completed",
                "description": "Finished",
                "done": True,
            })

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
        elif chunk_type == "python_code_start":
            # Four backticks keep ordinary Markdown fences embedded in Python
            # string literals from prematurely closing the streamed block.
            yield f"\n\n{TOOL_OUTPUT_START}\n````python\n"
        elif chunk_type == "python_code_delta":
            yield content
        elif chunk_type == "python_code_end":
            yield f"\n````\n{TOOL_OUTPUT_END}\n\n"
        elif chunk_type == "code":
            lang = fmt or ""
            yield (
                f"\n\n{TOOL_OUTPUT_START}\n```{lang}\n{content}\n```\n"
                f"{TOOL_OUTPUT_END}\n\n"
            )
        elif chunk_type == "console":
            if fmt == "error":
                yield (
                    f"\n\n{TOOL_OUTPUT_START}\n"
                    "⚠️ **Python execution error**\n\n"
                    f"````text\n{content}\n````\n"
                    f"{TOOL_OUTPUT_END}\n\n"
                )
            else:
                yield (
                    f"\n\n{TOOL_OUTPUT_START}\n```text\n{content}\n```\n"
                    f"{TOOL_OUTPUT_END}\n\n"
                )
        elif chunk_type == "image" and content:
            # Backward compatibility for older LangGraph services. Current
            # services send a filename and the main Pipe loop resolves it to
            # an Open WebUI file URL instead of persisting base64 here.
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
