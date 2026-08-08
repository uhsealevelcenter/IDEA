"""Bounded, reproducible execution-memory helpers."""

from __future__ import annotations

import ast
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import ToolMessage

from idea_config import IDEA_MAX_EXECUTION_MEMORY_BYTES


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Recursively remove credentials and bound large fields before checkpointing."""
    denied = {"authorization", "token", "api_key", "password", "secret"}

    def sanitize(key: str, value: Any) -> Any:
        if any(part in key.lower() for part in denied):
            return "[redacted]"
        if tool_name == "run_python_tool" and key == "code":
            return f"sha256:{sha256_text(str(value))}"
        if isinstance(value, dict):
            return {
                str(child_key): sanitize(str(child_key), child_value)
                for child_key, child_value in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [sanitize(key, item) for item in value]
        if isinstance(value, str) and len(value) > 2000:
            return value[:2000] + "…"
        return value

    return {
        str(key): sanitize(str(key), value)
        for key, value in arguments.items()
    }


def defined_names(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
    return sorted(names)


def bounded_excerpt(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore") + "\n[…truncated…]"


def bounded_text_bytes(value: str, limit: int) -> str:
    """Return UTF-8 text whose encoded representation never exceeds limit."""
    if limit <= 0:
        return ""
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    marker = "\n[…truncated…]".encode("utf-8")
    if len(marker) >= limit:
        return marker[:limit].decode("utf-8", errors="ignore")
    prefix = encoded[:limit - len(marker)].decode("utf-8", errors="ignore")
    return prefix + marker.decode("utf-8")


def bounded_records(
    records: list[dict[str, Any]],
    *,
    max_count: int,
    max_bytes: int,
) -> list[dict[str, Any]]:
    """Keep the newest ledger records within count and serialized-size bounds."""
    bounded = [dict(record) for record in records if isinstance(record, dict)]
    bounded = bounded[-max(max_count, 0):] if max_count > 0 else []
    while len(bounded) > 1 and len(
        json.dumps(bounded, default=str).encode("utf-8")
    ) > max_bytes:
        bounded.pop(0)
    return bounded


def execution_memory_block(
    state: dict[str, Any],
    recent: int = 8,
    max_bytes: int = IDEA_MAX_EXECUTION_MEMORY_BYTES,
) -> str:
    """Build a newest-first, byte-bounded model view of execution memory."""
    current_run_id = str(state.get("run_id") or "")
    records = [
        record for record in (state.get("python_executions") or [])
        if not current_run_id or str(record.get("run_id") or "") != current_run_id
    ][-recent:]
    actions = [
        action for action in (state.get("completed_actions") or [])
        if not current_run_id or str(action.get("run_id") or "") != current_run_id
    ][-recent:]
    if not records and not actions:
        return ""
    header = (
        "Prior IDEA execution memory (authoritative checkpoint state). "
        "The listed kernel variables are reusable when the kernel ID still "
        "matches; prefer reusing them for follow-up edits instead of "
        "reloading unchanged data:"
    )
    entries: list[str] = []
    for record in reversed(records):
        namespace = [
            item for item in (record.get("namespace") or [])
            if isinstance(item, dict) and item.get("name")
        ]
        variable_summary = ", ".join(
            f"{item.get('name')}: {item.get('type', 'object')}"
            + (
                f" shape={tuple(item.get('shape'))}"
                if isinstance(item.get("shape"), list) else ""
            )
            + (
                f" len={item.get('length')}"
                if item.get("length") is not None else ""
            )
            for item in namespace
        )
        entries.append("\n".join([
            f"- Python execution {record.get('execution_id')} [{record.get('status')}]",
            f"  kernel: {record.get('kernel_id')}",
            f"  source path: {record.get('source_path')}",
            f"  code sha256: {record.get('code_sha256')}",
            f"  reusable variables: {variable_summary or ', '.join(record.get('defined_names') or []) or 'unknown'}",
            f"  outputs: {', '.join(record.get('output_artifacts') or []) or 'none recorded'}",
            f"  result: {bounded_text_bytes(str(record.get('console_excerpt') or ''), 1200)}",
        ]))
    for action in reversed(actions):
        if action.get("tool_name") == "run_python_tool":
            continue
        entries.append(
            f"- {action.get('tool_name')} [{action.get('status')}]: "
            f"{action.get('result_excerpt', '')}"
        )

    selected: list[str] = []
    used = len(header.encode("utf-8"))
    for entry in entries:
        remaining = max_bytes - used - 1
        if remaining <= 0:
            break
        encoded = entry.encode("utf-8")
        if len(encoded) > remaining:
            entry = bounded_text_bytes(entry, remaining)
            selected.append(entry)
            break
        selected.append(entry)
        used += len(encoded) + 1
    return "\n".join([header, *selected])


def compact_turn_messages(
    messages: list[Any],
    *,
    observation_bytes: int,
    keep_recent_tools: int = 2,
) -> list[Any]:
    """Bound older tool observations while keeping the newest evidence intact."""
    tool_positions = [
        index for index, message in enumerate(messages)
        if isinstance(message, ToolMessage)
    ]
    recent_count = max(keep_recent_tools, 0)
    keep = set(tool_positions[-recent_count:]) if recent_count else set()
    compacted: list[Any] = []
    for index, message in enumerate(messages):
        if not isinstance(message, ToolMessage) or index in keep:
            compacted.append(message)
            continue
        content = str(message.content or "")
        bounded = bounded_text_bytes(content, observation_bytes)
        if bounded != content:
            bounded += (
                "\n[Older tool observation compacted. Use the execution ledger "
                "or archived output path for details.]"
            )
        compacted.append(ToolMessage(
            content=bounded,
            tool_call_id=message.tool_call_id,
            name=getattr(message, "name", None),
        ))
    return compacted
