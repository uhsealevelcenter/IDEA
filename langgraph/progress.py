"""User-visible progress events for long-running IDEA agent turns."""

import json
import re
from typing import Any


TOOL_STATUS_DESCRIPTIONS = {
    "run_terminal_tool": (
        "Preparing a terminal command…",
        "Running a terminal command…",
    ),
    "restart_terminal_tool": (
        "Preparing to restart the terminal…",
        "Restarting the terminal…",
    ),
    "write_file_tool": (
        "Preparing a file…",
        "Writing a file…",
    ),
    "run_python_tool": (
        "Preparing Python code…",
        "Running Python code…",
    ),
    "show_image_tool": (
        "Preparing an image…",
        "Displaying an image…",
    ),
    "inspect_image_tool": (
        "Preparing an image…",
        "Inspecting an image…",
    ),
    "view_skill": (
        "Preparing task instructions…",
        "Reading task instructions…",
    ),
}

_CODE_ARGUMENT_START = re.compile(r'(?:^|[{,])\s*"code"\s*:\s*"')
_JSON_SIMPLE_ESCAPES = {'"', "\\", "/", "b", "f", "n", "r", "t"}
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def partial_python_code_argument(raw_arguments: str) -> tuple[str | None, bool]:
    """Decode the complete prefix of a streamed JSON ``code`` string.

    A trailing escape or partial ``\\uXXXX`` sequence is withheld until it is
    complete, allowing callers to append only newly decoded Python text.
    ``None`` means the code value has not begun or its prefix is invalid.
    """
    match = _CODE_ARGUMENT_START.search(raw_arguments)
    if not match:
        return None, False

    value_start = match.end()
    cursor = value_start
    safe_end = value_start
    complete = False
    while cursor < len(raw_arguments):
        char = raw_arguments[cursor]
        if char == '"':
            complete = True
            break
        if char == "\\":
            if cursor + 1 >= len(raw_arguments):
                break
            escape = raw_arguments[cursor + 1]
            if escape == "u":
                escape_end = cursor + 6
                if escape_end > len(raw_arguments):
                    break
                if any(
                    digit not in _HEX_DIGITS
                    for digit in raw_arguments[cursor + 2 : escape_end]
                ):
                    return None, False
                cursor = escape_end
                safe_end = cursor
                continue
            if escape not in _JSON_SIMPLE_ESCAPES:
                return None, False
            cursor += 2
            safe_end = cursor
            continue
        if ord(char) < 0x20:
            return None, False
        cursor += 1
        safe_end = cursor

    try:
        decoded = json.loads(f'"{raw_arguments[value_start:safe_end]}"')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, False

    # json.loads combines a complete surrogate pair. Withhold an unmatched
    # high surrogate so later output always extends the previously sent text.
    if decoded and 0xD800 <= ord(decoded[-1]) <= 0xDBFF:
        decoded = decoded[:-1]
    return decoded, complete


def progress_chunk(
    phase: str,
    description: str,
    *,
    done: bool = False,
    tool_name: str | None = None,
) -> dict[str, Any]:
    """Build a safe SSE progress chunk without including tool arguments."""
    chunk: dict[str, Any] = {
        "role": "assistant",
        "type": "status",
        "action": "idea_agent",
        "phase": phase,
        "description": description,
        "done": done,
    }
    if tool_name:
        chunk["tool_name"] = tool_name
    return chunk


def tool_call_chunk_names(chunk: object) -> list[str]:
    """Return tool names revealed by a streaming model chunk."""
    raw_chunks = getattr(chunk, "tool_call_chunks", None) or []
    names: list[str] = []
    for raw_chunk in raw_chunks:
        if isinstance(raw_chunk, dict):
            name = raw_chunk.get("name")
        else:
            name = getattr(raw_chunk, "name", None)
        if isinstance(name, str) and name and name not in names:
            names.append(name)
    return names


def tool_status_description(tool_name: str, *, preparing: bool) -> str:
    """Return a user-facing description for a tool phase."""
    descriptions = TOOL_STATUS_DESCRIPTIONS.get(
        tool_name,
        ("Preparing a tool…", "Using a tool…"),
    )
    return descriptions[0 if preparing else 1]
