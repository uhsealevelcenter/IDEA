"""Adapter between graph nodes and IDEA's existing tools/UI event format."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import posixpath
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

from progress import (
    partial_python_code_argument,
    tool_call_chunk_names,
    tool_status_description,
)
from idea_config import (
    IDEA_MAX_MODEL_TOOL_OBSERVATION_BYTES,
    IDEA_MODEL_MAX_RETRIES,
    IDEA_MODEL_REQUEST_TIMEOUT_SECONDS,
)

from .memory import bounded_text_bytes, compact_turn_messages, execution_memory_block
from .memory import defined_names


class ModelCallCancelled(Exception):
    """Raised after cancelling an in-flight provider request."""


class ModelRequestTimeout(RuntimeError):
    """Raised with a stable, user-facing message after model timeouts."""


def _is_model_timeout(exc: BaseException) -> bool:
    """Recognize timeout wrappers used by httpx/OpenAI/LangChain."""
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, TimeoutError):
            return True
        name = type(current).__name__.lower()
        detail = str(current).lower()
        if "timeout" in name or "timed out" in detail or "time out" in detail:
            return True
        current = current.__cause__ or current.__context__
    return False


def _seconds_label(value: float) -> str:
    return f"{value:g}"


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
    vision_images: list[str] = field(default_factory=list)
    kernel_namespace: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


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
            _cacheable_system_message,
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
        self.cacheable_system_message = _cacheable_system_message

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
        cacheable_system_message = getattr(
            self,
            "cacheable_system_message",
            lambda content: SystemMessage(content=content),
        )
        result: list[BaseMessage] = [cacheable_system_message(self.system_prompt)]
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
        result.extend(compact_turn_messages(
            list(state.get("turn_messages") or []),
            observation_bytes=IDEA_MAX_MODEL_TOOL_OBSERVATION_BYTES,
        ))
        vision_paths = list(state.get("vision_images") or [])
        consumed = int(state.get("vision_consumed_count") or 0)
        if consumed < len(vision_paths):
            vision_content: list[dict[str, Any]] = []
            for image_path in vision_paths[consumed:]:
                vision_content.append({
                    "type": "text",
                    "text": (
                        "IDEA supplied an image for visual inspection at "
                        f"`{image_path}`. Inspect its actual pixels before "
                        "making appearance claims or continuing a visual "
                        "revision."
                    ),
                })
                vision_content.append(self.agent._model_image_part(image_path))
            result.append(HumanMessage(content=vision_content))
        return result

    def call_model(self, messages: list[BaseMessage], *, cancellation: Any = None) -> AIMessage:
        async def consume_stream(activity: dict[str, bool]) -> AIMessage:
            aggregated = None
            pending_text = ""
            last_flush = time.monotonic()
            announced_tools: set[str] = set()
            python_streams: dict[int, dict[str, Any]] = {}

            def flush_text() -> None:
                nonlocal pending_text, last_flush
                if pending_text:
                    self.emit(pending_text)
                    pending_text = ""
                    last_flush = time.monotonic()

            def emit_python_argument_chunks(chunk: object) -> None:
                raw_chunks = getattr(chunk, "tool_call_chunks", None) or []
                for position, raw_chunk in enumerate(raw_chunks):
                    if isinstance(raw_chunk, dict):
                        name = raw_chunk.get("name")
                        arguments = raw_chunk.get("args")
                        raw_index = raw_chunk.get("index")
                        tool_call_id = raw_chunk.get("id")
                    else:
                        name = getattr(raw_chunk, "name", None)
                        arguments = getattr(raw_chunk, "args", None)
                        raw_index = getattr(raw_chunk, "index", None)
                        tool_call_id = getattr(raw_chunk, "id", None)

                    index = raw_index if isinstance(raw_index, int) else position
                    tracker = python_streams.setdefault(index, {
                        "name": "",
                        "raw_arguments": "",
                        "emitted": "",
                        "started": False,
                        "ended": False,
                        "tool_call_id": "",
                    })
                    if isinstance(name, str) and name:
                        tracker["name"] = name
                    if isinstance(tool_call_id, str) and tool_call_id:
                        tracker["tool_call_id"] = tool_call_id
                    if isinstance(arguments, str):
                        tracker["raw_arguments"] += arguments
                    elif isinstance(arguments, dict):
                        code = arguments.get("code")
                        if isinstance(code, str):
                            tracker["raw_arguments"] = json.dumps({"code": code})

                    if tracker["name"] != "run_python_tool":
                        continue
                    decoded, _ = partial_python_code_argument(
                        tracker["raw_arguments"]
                    )
                    if decoded is None or not decoded.startswith(tracker["emitted"]):
                        continue
                    stream_id = tracker["tool_call_id"] or f"index:{index}"
                    if not tracker["started"]:
                        tracker["started"] = True
                        self.emit({
                            "type": "python_code_start",
                            "format": "python",
                            "stream_id": stream_id,
                        })
                    delta = decoded[len(tracker["emitted"]):]
                    if delta:
                        self.emit({
                            "type": "python_code_delta",
                            "format": "python",
                            "stream_id": stream_id,
                            "content": delta,
                        })
                        tracker["emitted"] = decoded

            def end_python_stream(tracker: dict[str, Any], *, complete: bool) -> None:
                if tracker["started"] and not tracker["ended"]:
                    tracker["ended"] = True
                    self.emit({
                        "type": "python_code_end",
                        "format": "python",
                        "stream_id": tracker["tool_call_id"],
                        "complete": complete,
                    })

            try:
                async for chunk in self.agent.llm.astream(messages):
                    activity["received_chunk"] = True
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
                    emit_python_argument_chunks(chunk)
            except (asyncio.CancelledError, Exception):
                flush_text()
                for tracker in python_streams.values():
                    end_python_stream(tracker, complete=False)
                raise

            flush_text()
            if aggregated is None:
                return AIMessage(content="")
            response = message_chunk_to_message(aggregated)
            if isinstance(response, AIMessage):
                final_response = response
            else:
                final_response = AIMessage(
                    content=str(getattr(response, "content", response))
                )

            for index, call in enumerate(final_response.tool_calls or []):
                call_id = str(call.get("id") or "")
                tracker = next(
                    (
                        item
                        for item in python_streams.values()
                        if call_id and item["tool_call_id"] == call_id
                    ),
                    python_streams.get(index),
                )
                if not tracker or call.get("name") != "run_python_tool":
                    continue
                code = str((call.get("args") or {}).get("code") or "")
                emitted = str(tracker["emitted"])
                if tracker["started"] and code.startswith(emitted):
                    call_id = call_id or str(tracker["tool_call_id"] or "")
                    tracker["tool_call_id"] = call_id
                    delta = code[len(emitted):]
                    if delta:
                        self.emit({
                            "type": "python_code_delta",
                            "format": "python",
                            "stream_id": call_id,
                            "content": delta,
                        })
                        tracker["emitted"] = code
                    end_python_stream(tracker, complete=True)

            for tracker in python_streams.values():
                end_python_stream(tracker, complete=False)
            return final_response

        async def invoke(activity: dict[str, bool]) -> AIMessage:
            request = asyncio.create_task(consume_stream(activity))
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

        timeout_seconds = float(getattr(
            self.agent,
            "model_request_timeout_seconds",
            IDEA_MODEL_REQUEST_TIMEOUT_SECONDS,
        ))
        max_retries = max(0, int(getattr(
            self.agent,
            "model_max_retries",
            IDEA_MODEL_MAX_RETRIES,
        )))

        for attempt in range(max_retries + 1):
            activity = {"received_chunk": False}
            try:
                return asyncio.run(invoke(activity))
            except ModelCallCancelled:
                raise
            except Exception as exc:
                timed_out = _is_model_timeout(exc)
                retry_available = attempt < max_retries
                # Retrying after any provider chunk could duplicate visible
                # text, tool announcements, or streamed Python arguments.
                safe_to_retry = timed_out and not activity["received_chunk"]
                if retry_available and safe_to_retry:
                    self.emit({
                        "type": "status",
                        "phase": "model_retrying",
                        "description": (
                            "The model response timed out after "
                            f"{_seconds_label(timeout_seconds)} seconds; "
                            f"retrying ({attempt + 1}/{max_retries})…"
                        ),
                        "done": False,
                        "attempt": attempt + 1,
                        "max_retries": max_retries,
                    })
                    continue

                detail = str(exc).lower()
                rate_limited = "rate limit" in detail or "429" in detail
                if timed_out:
                    qualifier = (
                        " after partial output"
                        if activity["received_chunk"]
                        else ""
                    )
                    message = (
                        "The model did not respond within "
                        f"{_seconds_label(timeout_seconds)} seconds"
                        f"{qualifier}. Any completed tool operations were "
                        "retained; please retry."
                    )
                    self.emit({
                        "type": "status",
                        "phase": "model_timeout",
                        "description": message,
                        "done": True,
                        "error": True,
                        "retryable": not activity["received_chunk"],
                    })
                    raise ModelRequestTimeout(message) from exc

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

        raise AssertionError("model retry loop exited unexpectedly")

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
        if name == "delegate_to_codex":
            from tools.persistent_terminal import run_codex

            cwd = str(args.get("cwd") or "/workspace")
            if not cwd.startswith("/"):
                cwd = f"/workspace/{cwd}"
            cwd = posixpath.normpath(cwd)
            access = str(args.get("access") or "read-only")
            if access not in {"read-only", "workspace-write"}:
                return ToolOutcome(
                    content="Codex access must be read-only or workspace-write.",
                    status="failed",
                    error="unsupported access mode",
                )
            thread_id = str((state.get("codex_threads") or {}).get(cwd) or "")
            self.emit({
                "type": "status",
                "phase": "codex",
                "description": "Codex is working in the private workspace…",
                "done": False,
            })
            result = run_codex(
                str(args.get("task") or ""),
                session_id=self.agent.sandbox_id,
                cwd=cwd,
                access=access,
                thread_id=thread_id,
                run_id=str(state.get("run_id") or ""),
            )
            status = str(result.get("status") or "failed")
            error = str(result.get("error") or "")
            final_response = str(result.get("final_response") or "").strip()
            changed_paths = [str(path) for path in result.get("changed_paths") or []]
            lines = [final_response] if final_response else []
            if changed_paths:
                lines.append("Changed paths:\n" + "\n".join(f"- {path}" for path in changed_paths))
            if error:
                lines.append(f"Codex error: {error}")
            content = bounded_text_bytes(
                "\n\n".join(lines) or "Codex returned no response.",
                IDEA_MAX_MODEL_TOOL_OBSERVATION_BYTES,
            )
            self.emit({
                "type": "status",
                "phase": "codex",
                "description": "Codex delegation finished" if result.get("ok") else "Codex delegation failed",
                "done": True,
            })
            mapped_status = (
                "completed" if result.get("ok") else
                "interrupted" if status == "interrupted" else "failed"
            )
            return ToolOutcome(
                content=content,
                status=mapped_status,
                error=(error or None) if mapped_status != "completed" else None,
                metadata={
                    "codex_cwd": cwd,
                    "codex_thread_id": str(result.get("thread_id") or ""),
                    "codex_usage": result.get("usage") or {},
                    "codex_events": result.get("events") or [],
                },
            )
        if name == "run_python_tool":
            from tools.persistent_terminal import (
                inspect_python_namespace,
                run_python_stream,
            )

            code = str(args.get("code") or "")
            tool_call_id = str(tool_call.get("id") or "")
            self.emit({
                "role": "computer", "type": "code", "format": "python",
                "content": code, "tool_call_id": tool_call_id,
                "start": True, "end": True,
            })
            chunks = run_python_stream(
                code,
                session_id=self.agent.sandbox_id,
                kernel_id=str(state.get("kernel_id") or "default"),
                run_id=str(state.get("run_id") or ""),
            )
            console: list[str] = []
            errors: list[str] = []
            kernel_failure_types: list[str] = []
            kernel_lost = False
            image_count = 0
            generated_images: list[str] = []
            run_id = str(state.get("run_id") or "")
            cancellation = state.get("_run_cancellation")
            console_stream_open = False

            def emit_console(content: str, console_format: str) -> None:
                nonlocal console_stream_open
                if not content:
                    return
                self.emit({
                    "role": "computer", "type": "console", "format": console_format,
                    "content": content, "tool_call_id": tool_call_id,
                    "start": not console_stream_open, "end": False,
                })
                console_stream_open = True

            try:
                for chunk in chunks:
                    if chunk.get("kernel_lost"):
                        kernel_lost = True
                    if chunk.get("error_type"):
                        kernel_failure_types.append(str(chunk["error_type"]))
                    if chunk.get("type") == "console" and chunk.get("format") != "active_line":
                        content = str(chunk.get("content") or "")
                        if content:
                            console.append(content)
                            console_format = str(chunk.get("format") or "output")
                            if console_format == "error":
                                errors.append(content)
                            emit_console(content, console_format)
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
                            errors.append(warning)
                            emit_console(warning, "error")
                            continue
                        self.displayed_image_paths.add(image_path)
                        generated_images.append(image_path)
                        self.emit({
                            "role": "assistant",
                            "type": "image",
                            "format": image_format,
                            "filename": image_path,
                            "start": True,
                            "end": True,
                        })
            finally:
                if console_stream_open:
                    self.emit({
                        "role": "computer", "type": "console", "format": "output",
                        "content": "", "tool_call_id": tool_call_id,
                        "start": False, "end": True,
                    })
            content = "\n".join(console).strip()
            if image_count:
                content = (content + f"\n[{image_count} image(s) generated and shown to the user]").strip()
            failed = bool(errors) or content.startswith(("✗", "Kernel error", "Kernel exec failed"))
            cancelled = bool(
                cancellation is not None
                and getattr(cancellation, "requested", False)
            )
            namespace = []
            if not failed and not cancelled:
                namespace = inspect_python_namespace(
                    session_id=self.agent.sandbox_id,
                    kernel_id=str(state.get("kernel_id") or "default"),
                    names=defined_names(code),
                    run_id=run_id,
                )
            return ToolOutcome(
                content=content or "(no output)",
                status=(
                    "failed" if failed else
                    "interrupted" if cancelled else
                    "completed"
                ),
                artifacts=generated_images,
                vision_images=generated_images,
                kernel_namespace=namespace,
                error=(
                    ("\n".join(errors).strip() or content)
                    if failed else
                    str(getattr(cancellation, "reason", None) or "user_requested")
                    if cancelled else
                    None
                ),
                metadata={
                    "kernel_lost": kernel_lost,
                    "kernel_failure_types": list(dict.fromkeys(kernel_failure_types)),
                },
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
        failed = result.startswith("✗")
        # Successful tool observations are for the agent, not the chat
        # transcript.  The native status events already tell the user what is
        # running, while ``result`` below retains the complete observation
        # (including any saved-output paging guidance) for the next model
        # turn.  Keep failures visible because they are actionable UI output.
        if failed:
            self.emit({
                "role": "computer", "type": "console", "format": "error",
                "content": result, "start": True, "end": True,
            })
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
