"""User-visible progress events for long-running IDEA agent turns."""

from typing import Any


TOOL_STATUS_DESCRIPTIONS = {
    "run_terminal_tool": (
        "Preparing a terminal command…",
        "Running a terminal command…",
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
