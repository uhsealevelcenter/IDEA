"""Bounded, reproducible execution-memory helpers."""

from __future__ import annotations

import ast
import hashlib
import json
from datetime import datetime, timezone
from typing import Any


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


def execution_memory_block(state: dict[str, Any], recent: int = 8) -> str:
    records = list(state.get("python_executions") or [])[-recent:]
    actions = list(state.get("completed_actions") or [])[-recent:]
    if not records and not actions:
        return ""
    lines = ["Prior IDEA execution memory (authoritative machine state):"]
    for record in records:
        lines.extend([
            f"- Python execution {record.get('execution_id')} [{record.get('status')}]",
            f"  kernel: {record.get('kernel_id')}",
            f"  source path: {record.get('source_path')}",
            "  exact submitted code:",
            "```python",
            str(record.get("submitted_code") or ""),
            "```",
        ])
    for action in actions:
        if action.get("tool_name") == "run_python_tool":
            continue
        lines.append(
            f"- {action.get('tool_name')} [{action.get('status')}]: "
            f"{action.get('result_excerpt', '')}"
        )
    return "\n".join(lines)
