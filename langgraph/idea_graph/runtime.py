"""Adapter between graph nodes and IDEA's existing tools/UI event format."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from .memory import execution_memory_block


@dataclass
class ToolOutcome:
    content: str
    status: str = "completed"
    events: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    error: str | None = None


class GraphRuntime(Protocol):
    def prepare(self, state: dict[str, Any]) -> dict[str, Any]: ...
    def call_model(self, messages: list[BaseMessage]) -> AIMessage: ...
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

    def call_model(self, messages: list[BaseMessage]) -> AIMessage:
        response = self.agent.llm.invoke(messages)
        if not isinstance(response, AIMessage):
            response = AIMessage(content=str(getattr(response, "content", response)))
        if response.content:
            self.emit(str(response.content))
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
                    self.emit({**chunk, "role": "assistant", "start": True, "end": True})
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
                        self.emit({
                            "role": "assistant",
                            "type": "image",
                            "format": f"base64.{image_format}",
                            "content": encoded,
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
            referenced_paths=referenced_output_paths(response),
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
