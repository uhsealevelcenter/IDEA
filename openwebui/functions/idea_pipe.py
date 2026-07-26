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
import requests
from pydantic import BaseModel, Field
from typing import Generator, Iterator, Union


BASE_MODEL_ID = "idea_terminal_agent.idea-terminal-agent"
BASE_MODEL_ALIASES = {BASE_MODEL_ID, "idea-terminal-agent"}


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

    def pipe(
        self,
        body: dict,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
    ) -> Union[str, Generator, Iterator]:
        messages = body.get("messages", [])
        if not messages:
            return ""

        user_content = _latest_user_content(messages)
        if not user_content:
            return ""
        assistant_system_prompt = _assistant_system_prompt(messages)
        assistant_id = _selected_assistant_id(__metadata__)

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
        }

        headers = (
            {"Authorization": f"Bearer {self.valves.INTERNAL_SERVICE_TOKEN}"}
            if self.valves.INTERNAL_SERVICE_TOKEN
            else {}
        )

        try:
            response = requests.post(
                f"{self.valves.LANGGRAPH_SERVICE_URL}/chat",
                json=payload,
                headers=headers,
                stream=True,
                timeout=self.valves.REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            yield f"\n\n**Error reaching langgraph service:** {exc}\n\n"
            return

        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line or not raw_line.startswith("data: "):
                continue
            data = raw_line[len("data: "):]
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            yield from self._translate_chunk(chunk)

    @staticmethod
    def _translate_chunk(chunk: dict) -> Generator[str, None, None]:
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
            display_name = filename.rsplit("/", 1)[-1]
            if file_id:
                yield f"\n\n📎 [{display_name}](/api/v1/files/{file_id}/content)\n\n"
