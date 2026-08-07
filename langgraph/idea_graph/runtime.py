"""Adapter between graph nodes and IDEA's existing tools/UI event format."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    message_chunk_to_message,
)

from progress import tool_call_chunk_names, tool_status_description

from .memory import execution_memory_block


class ModelCallCancelled(Exception):
    """Raised after cancelling an in-flight provider request."""


MODEL_STREAM_FLUSH_INTERVAL_SECONDS = 0.05
MODEL_STREAM_FLUSH_CHARS = 80
MODEL_WAITING_STATUS_SECONDS = 5.0
MODEL_BUSY_STATUS_SECONDS = 15.0
KERNEL_IMAGE_EXTENSIONS = {"gif", "jpeg", "jpg", "png", "webp"}


@dataclass
class ToolOutcome:
    content: str
    status: str = "completed"
    events: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    error: str | None = None


class GraphRuntime(Protocol):
    def prepare(self, state: dict[str, Any]) -> dict[str, Any]: ...
    def call_model(self, messages: list[BaseMessage], *, cancellation: Any = None) -> AIMessage: ...
    def model_messages(self, state: dict[str, Any]) -> list[BaseMessage]: ...
    def execute_tool(self, tool_call: dict[str, Any], state: dict[str, Any]) -> ToolOutcome: ...
    def persist_python_source(self, execution_id: str, code: str, state: dict[str, Any]) -> str: ...
    def finalize(self, state: dict[str, Any]) -> list[dict[str, Any]]: ...
    def emit(self, chunk: dict[str, Any] | str) -> None: ...


class TerminalGraphRuntime:
    """Reuse TerminalAgent's mature tools while LangGraph owns orchestration."""

    def __init__(
        self,
        *,
        user_id: str,
        user_email: str | None,
        session_id: str,
        model: str,
        assistant_id: str | None,
        assistant_system_prompt: str | None,
        attached_files: list[dict[str, Any]],
        openwebui_authorization: str | None,
        is_guest: bool,
        paperqa_enabled: bool,
        event_callback: Callable[[dict[str, Any] | str], None] | None = None,
    ) -> None:
        # Lazy import keeps state/graph unit tests independent of optional
        # PaperQA and scientific runtime dependencies.
        from agents.terminal_agent import (
            OUTPUTS_DIR,
            SYSTEM_PROMPT_PATH,
            TerminalAgent,
            compose_system_prompt,
        )

        self.outputs_dir = OUTPUTS_DIR
        self.agent = TerminalAgent(
            session_id=session_id,
            user_id=user_id,
            user_email=user_email,
            model=model,
            assistant_id=assistant_id,
            assistant_system_prompt=assistant_system_prompt,
            attached_files=attached_files,
            openwebui_authorization=openwebui_authorization,
            is_guest=is_guest,
            paperqa_enabled=paperqa_enabled,
        )
        self.event_callback = event_callback
        self.outputs_before: dict[str, str] | None = None
        self.displayed_image_paths: set[str] = set()
        self.system_prompt = compose_system_prompt(
            SYSTEM_PROMPT_PATH.read_text(),
            assistant_system_prompt,
            self.agent.builtin_skill_loader.render_manifest(),
        )

    def emit(self, chunk: dict[str, Any] | str) -> None:
        if self.event_callback:
            self.event_callback(chunk)

    def prepare(self, state: dict[str, Any]) -> dict[str, Any]:
        from tools.persistent_terminal import list_file_metadata

        synced = self.agent._sync_inputs_from_openwebui() if self.agent.attached_files else []
        self.outputs_before = list_file_metadata(
            self.outputs_dir, session_id=self.agent.sandbox_id
        )
        return {"synced_inputs": synced}

    def model_messages(self, state: dict[str, Any]) -> list[BaseMessage]:
        result: list[BaseMessage] = [SystemMessage(content=self.system_prompt)]
        memory = execution_memory_block(state)
        if memory:
            result.append(SystemMessage(content=memory))
        for message in state.get("conversation_messages") or []:
            role = message.get("role")
            content = message.get("content", "")
            if role == "assistant":
                result.append(AIMessage(content=content))
            elif role == "system":
                result.append(HumanMessage(content=f"Conversation summary/context:\n{content}"))
            else:
                result.append(HumanMessage(content=content))
        result.extend(state.get("turn_messages") or [])
        return result

    def call_model(self, messages: list[BaseMessage], *, cancellation: Any = None) -> AIMessage:
        async def consume_stream() -> AIMessage:
            aggregated = None
            pending_text = ""
            last_flush = time.monotonic()
            announced_tools: set[str] = set()

            def flush_text() -> None:
                nonlocal pending_text, last_flush
                if pending_text:
                    self.emit(pending_text)
                    pending_text = ""
                    last_flush = time.monotonic()

            async for chunk in self.agent.llm.astream(messages):
                aggregated = chunk if aggregated is None else aggregated + chunk
                text = str(getattr(chunk, "text", "") or "")
                if text:
                    pending_text += text

                tool_names = [
                    name
                    for name in tool_call_chunk_names(chunk)
                    if name not in announced_tools
                ]
                if tool_names:
                    flush_text()
                    for name in tool_names:
                        announced_tools.add(name)
                        self.emit({
                            "type": "status",
                            "phase": "preparing_tool",
                            "description": tool_status_description(
                                name, preparing=True
                            ),
                            "tool_name": name,
                            "done": False,
                        })
                elif pending_text and (
                    len(pending_text) >= MODEL_STREAM_FLUSH_CHARS
                    or time.monotonic() - last_flush
                    >= MODEL_STREAM_FLUSH_INTERVAL_SECONDS
                ):
                    flush_text()

            flush_text()
            if aggregated is None:
                return AIMessage(content="")
            response = message_chunk_to_message(aggregated)
            if isinstance(response, AIMessage):
                return response
            return AIMessage(
                content=str(getattr(response, "content", response))
            )

        async def invoke() -> AIMessage:
            request = asyncio.create_task(consume_stream())
            started_at = time.monotonic()
            waiting_announced = False
            busy_announced = False
            while not request.done():
                if cancellation is not None and cancellation.requested:
                    request.cancel()
                    try:
                        await request
                    except asyncio.CancelledError:
                        pass
                    raise ModelCallCancelled(cancellation.reason or "user_requested")
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(request), timeout=0.1
                    )
                except asyncio.TimeoutError:
                    elapsed = time.monotonic() - started_at
                    if (
                        elapsed >= MODEL_BUSY_STATUS_SECONDS
                        and not busy_announced
                    ):
                        busy_announced = True
                        self.emit({
                            "type": "status",
                            "phase": "waiting_for_model",
                            "description": "Model is busy; still waiting…",
                            "done": False,
                        })
                    elif (
                        elapsed >= MODEL_WAITING_STATUS_SECONDS
                        and not waiting_announced
                    ):
                        waiting_announced = True
                        self.emit({
                            "type": "status",
                            "phase": "waiting_for_model",
                            "description": "Waiting for the model to respond…",
                            "done": False,
                        })
                    continue
            return await request

        try:
            response = asyncio.run(invoke())
        except ModelCallCancelled:
            raise
        except Exception as exc:
            detail = str(exc).lower()
            rate_limited = "rate limit" in detail or "429" in detail
            self.emit({
                "type": "status",
                "phase": "model_unavailable",
                "description": (
                    "Model capacity is temporarily limited; please retry"
                    if rate_limited
                    else "The model request failed"
                ),
                "done": True,
                "error": True,
            })
            raise
        return response

    def persist_python_source(self, execution_id: str, code: str, state: dict[str, Any]) -> str:
        from tools.persistent_terminal import write_file_stream

        thread = str(state.get("thread_id") or "unknown").replace("/", "_")
        path = f"/workspace/.idea/threads/{thread}/executions/{execution_id}.py"
        data = code.encode("utf-8")
        write_file_stream(
            path,
            [data],
            session_id=self.agent.sandbox_id,
            expected_size=len(data),
        )
        return path

    def persist_kernel_image(
        self,
        chunk: dict[str, Any],
        *,
        run_id: str,
        image_index: int,
    ) -> tuple[str, str]:
        """Persist a Jupyter display image without exposing base64 to chat."""
        from tools.persistent_terminal import write_file_stream

        encoded = str(chunk.get("content") or "")
        raw_format = str(chunk.get("format") or "base64.png").lower()
        image_format = raw_format.split(".")[-1]
        if image_format not in KERNEL_IMAGE_EXTENSIONS:
            raise ValueError(f"unsupported kernel image format: {image_format}")
        try:
            image_data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid base64 kernel image") from exc
        if not image_data:
            raise ValueError("empty kernel image")

        safe_run_id = "".join(
            char for char in run_id if char.isalnum() or char in {"-", "_"}
        ) or uuid.uuid4().hex
        path = (
            f"{self.outputs_dir}/.idea/kernel-images/"
            f"{safe_run_id}-{image_index}.{image_format}"
        )
        write_file_stream(
            path,
            [image_data],
            session_id=self.agent.sandbox_id,
            expected_size=len(image_data),
        )
        return path, image_format

    def execute_tool(self, tool_call: dict[str, Any], state: dict[str, Any]) -> ToolOutcome:
        name = str(tool_call.get("name") or "")
        args = dict(tool_call.get("args") or {})
        if name == "run_python_tool":
            from tools.persistent_terminal import run_python

            code = str(args.get("code") or "")
            self.emit({
                "role": "computer", "type": "code", "format": "python",
                "content": code, "start": True, "end": True,
            })
            chunks = run_python(
                code,
                session_id=self.agent.sandbox_id,
                kernel_id=str(state.get("kernel_id") or "default"),
                run_id=str(state.get("run_id") or ""),
            )
            console: list[str] = []
            image_count = 0
            run_id = str(state.get("run_id") or "")
            for chunk in chunks:
                if chunk.get("type") == "console" and chunk.get("format") != "active_line":
                    content = str(chunk.get("content") or "")
                    if content:
                        console.append(content)
                        self.emit({
                            "role": "computer", "type": "console", "format": "output",
                            "content": content, "start": True, "end": True,
                        })
                elif chunk.get("type") == "image":
                    image_count += 1
                    try:
                        image_path, image_format = self.persist_kernel_image(
                            chunk,
                            run_id=run_id,
                            image_index=image_count,
                        )
                    except Exception as exc:
                        warning = f"✗ Could not save Python image {image_count}: {exc}"
                        console.append(warning)
                        self.emit({
                            "role": "computer",
                            "type": "console",
                            "format": "output",
                            "content": warning,
                            "start": True,
                            "end": True,
                        })
                        continue
                    self.displayed_image_paths.add(image_path)
                    self.emit({
                        "role": "assistant",
                        "type": "image",
                        "format": image_format,
                        "filename": image_path,
                        "start": True,
                        "end": True,
                    })
            content = "\n".join(console).strip()
            if image_count:
                content = (content + f"\n[{image_count} image(s) generated and shown to the user]").strip()
            failed = content.startswith(("✗", "Kernel error", "Kernel exec failed"))
            return ToolOutcome(
                content=content or "(no output)",
                status="failed" if failed else "completed",
                error=content if failed else None,
            )

        tool = self.agent.tools_by_name.get(name)
        if tool is None:
            return ToolOutcome(content=f"Unknown tool: {name}", status="failed", error="unknown tool")

        if name == "show_image_tool":
            try:
                result = str(tool.invoke(args))
                if result.startswith("✓"):
                    image_path = str(args.get("filepath") or "")
                    encoded, image_format = self.agent._encode_image_to_base64(
                        image_path
                    )
                    content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
                    if content_hash in self.agent._shown_image_hashes:
                        result = (
                            "✓ Image already displayed to the user "
                            f"(identical content): {image_path}"
                        )
                    else:
                        self.agent._shown_image_hashes.add(content_hash)
                        self.displayed_image_paths.add(image_path)
                        if image_path.startswith("/workspace/"):
                            # publish_artifact_tool's default destination
                            # preserves this relative path. Include it in
                            # final sync references so an unchanged, already-
                            # published image still produces a file event.
                            self.displayed_image_paths.add(
                                "/outputs/"
                                + image_path.removeprefix("/workspace/")
                            )
                        self.emit({
                            "role": "assistant",
                            "type": "image",
                            # Open WebUI resolves this sandbox path to the
                            # uploaded file emitted by finalize(). Do not put
                            # base64 bytes in chat history: they were counted
                            # as conversation text and duplicated in the
                            # message's structured output.
                            "format": image_format,
                            "filename": image_path,
                            "start": True,
                            "end": True,
                        })
            except Exception as exc:
                result = f"✗ {name} failed: {exc}"

            failed = result.startswith("✗")
            if failed:
                self.emit({
                    "role": "computer", "type": "console", "format": "output",
                    "content": result, "start": True, "end": True,
                })
            return ToolOutcome(
                content=result,
                status="failed" if failed else "completed",
                error=result if failed else None,
            )

        try:
            result = str(tool.invoke(args))
        except Exception as exc:
            result = f"✗ {name} failed: {exc}"
        self.emit({
            "role": "computer", "type": "console", "format": "output",
            "content": result, "start": True, "end": True,
        })
        failed = result.startswith("✗")
        return ToolOutcome(
            content=result, status="failed" if failed else "completed",
            error=result if failed else None,
        )

    def finalize(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        from utils.output_sync import referenced_output_paths

        response = str(state.get("final_response") or "")
        synced = self.agent._sync_outputs_to_openwebui(
            self.outputs_before,
            referenced_paths=(
                referenced_output_paths(response)
                | self.displayed_image_paths
            ),
        )
        events: list[dict[str, Any]] = []
        for item in synced:
            event = {
                "role": "assistant", "type": "file",
                "filename": item["filename"],
                "openwebui_file_id": item["openwebui_file_id"],
                "start": True, "end": True,
            }
            events.append(event)
            self.emit(event)
        return events
