"""
Persistent Terminal Tool - thin HTTP client to the sandbox_service.

All actual terminal/sandbox state (pexpect shells, microsandbox microVMs,
locks) now lives in the standalone sandbox_service (../../sandbox_service/),
so this module - and by extension the langgraph service as a whole - holds
no terminal-related process state itself. Every session (keyed by
sandbox_id, in practice a stable user_id - see agents/terminal_agent.py)
still gets its own isolated terminal, just resolved server-side by
sandbox_service instead of in this process.
"""

import json
import os
import posixpath
import shlex
import time
import uuid
from typing import Iterable, Optional
import httpx
from langchain_core.tools import tool

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from utils.output_sync import parse_file_metadata_output
from config import (
    SANDBOX_SERVICE_URL,
    SANDBOX_HTTP_CONNECT_TIMEOUT_SECONDS,
    SANDBOX_HTTP_READ_TIMEOUT_SECONDS,
    SANDBOX_HTTP_WRITE_TIMEOUT_SECONDS,
    SANDBOX_HTTP_POOL_TIMEOUT_SECONDS,
    INTERNAL_SERVICE_TOKEN as _INTERNAL_SERVICE_TOKEN,
    OUTPUT_HEAD_TAIL_LINES,
    MAX_OUTPUT_TOKENS,
    TEMP_OUTPUT_DIR as _TEMP_OUTPUT_DIR,
)

_HTTP_TIMEOUT = httpx.Timeout(
    connect=SANDBOX_HTTP_CONNECT_TIMEOUT_SECONDS,
    read=SANDBOX_HTTP_READ_TIMEOUT_SECONDS,
    write=SANDBOX_HTTP_WRITE_TIMEOUT_SECONDS,
    pool=SANDBOX_HTTP_POOL_TIMEOUT_SECONDS,
)

try:
    import tiktoken
    _ENCODER = tiktoken.get_encoding("cl100k_base")
except Exception:
    _ENCODER = None


def _count_tokens(text: str) -> int:
    """Best-effort token count - uses tiktoken if available, else a ~4 chars/token heuristic."""
    if not text:
        return 0
    if _ENCODER is not None:
        try:
            return len(_ENCODER.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)


def _guess_output_extension(output: str) -> str:
    """Best-effort guess at an appropriate file extension for raw command output."""
    stripped = output.strip()
    if not stripped:
        return "log"
    if stripped[0] in "{[":
        try:
            json.loads(stripped)
            return "json"
        except Exception:
            pass
    lines = stripped.splitlines()
    if len(lines) > 1:
        comma_counts = {line.count(",") for line in lines[:20] if line.strip()}
        if len(comma_counts) == 1 and comma_counts.pop() > 0:
            return "csv"
    if stripped.startswith("<"):
        return "html" if "<html" in stripped.lower() else "xml"
    return "log"


def _truncate_output(
    output: str,
    n_lines: int = OUTPUT_HEAD_TAIL_LINES,
    max_tokens: int = MAX_OUTPUT_TOKENS,
) -> str:
    """
    Truncate `output` to at most its first/last `n_lines` lines, then
    further enforce a hard `max_tokens` cap (trimming evenly from the
    head/tail blocks if needed, e.g. for a handful of extremely long
    lines) so a single command's output can never blow out the LLM's
    context window. The caller is responsible for saving the full,
    untruncated output elsewhere (see run_terminal's temp-file save).
    """
    lines = output.splitlines()
    if len(lines) > 2 * n_lines:
        head = "\n".join(lines[:n_lines])
        tail = "\n".join(lines[-n_lines:])
        omitted = len(lines) - 2 * n_lines
        truncated = f"{head}\n\n... [{omitted} line(s) omitted] ...\n\n{tail}"
    else:
        truncated = output

    if _count_tokens(truncated) <= max_tokens:
        return truncated

    # Still over budget (e.g. a few very long lines) - trim characters
    # evenly from the head/tail instead.
    chars_per_token = max(1, len(truncated) // max(1, _count_tokens(truncated)))
    max_chars = max_tokens * chars_per_token
    if len(truncated) <= max_chars:
        return truncated
    half = max_chars // 2
    return (
        f"{truncated[:half]}\n\n... [truncated to fit {max_tokens}-token limit] ...\n\n{truncated[-half:]}"
    )

# _INTERNAL_SERVICE_TOKEN imported from config.py above - must match
# sandbox_service/main.py's own INTERNAL_SERVICE_TOKEN (same .env value,
# injected into both containers - see docker-compose.yml). Sent on every
# request; sandbox_service no-ops the check if its own copy is unset.
_default_headers = (
    {"Authorization": f"Bearer {_INTERNAL_SERVICE_TOKEN}"} if _INTERNAL_SERVICE_TOKEN else {}
)

_client = httpx.Client(base_url=SANDBOX_SERVICE_URL, timeout=_HTTP_TIMEOUT, headers=_default_headers)


def close_terminal(session_id: str) -> None:
    """
    Ask sandbox_service to gracefully stop (state-preserving) this
    session's terminal. It can still be resumed later with the same
    session_id.
    """
    try:
        _client.post(f"/sandboxes/{session_id}/stop")
    except httpx.HTTPError as e:
        print(f"Failed to stop sandbox {session_id}: {e}")


def destroy_terminal(session_id: str) -> None:
    """
    Ask sandbox_service to permanently delete this session's terminal
    (kill + remove the sandbox, or close the local shell) - NOT resumable.
    Only call this for an explicit user-initiated "wipe my environment" action.
    """
    try:
        _client.post(f"/sandboxes/{session_id}/destroy")
    except httpx.HTTPError as e:
        print(f"Failed to destroy sandbox {session_id}: {e}")


def run_terminal(command: str, session_id: str = 'default') -> str:
    """
    Execute a shell command in a persistent terminal session (via sandbox_service).
    The shell state (environment variables, working directory, etc.) persists across calls.
    Use this for system commands, file operations, installing packages, etc.
    
    Args:
        command: The shell command to execute (e.g., "ls -la", "pip install numpy")
        session_id: Identifier for the persistent shell session (default: 'default')
        
    Returns:
        The output from running the command (stdout/stderr), truncated to
        its first/last OUTPUT_HEAD_TAIL_LINES lines and MAX_OUTPUT_TOKENS
        tokens. When truncation occurs, the full output is saved to a temp
        file in the sandbox (see read_output_range for paging through it).
    """
    try:
        response = _client.post(f"/sandboxes/{session_id}/exec", json={"command": command})
        response.raise_for_status()
        result = response.json()
    except httpx.HTTPError as e:
        return f"✗ Failed to reach sandbox service: {e}"

    success = result.get("success", False)
    output = result.get("output", "")
    elapsed_time = result.get("elapsed_time", 0.0)

    mins, secs = divmod(int(elapsed_time), 60)
    time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
    status_line = (
        f"✓ Command executed successfully in {time_str}."
        if success else f"✗ Command failed after {time_str}."
    )

    if not output:
        return f"{status_line}\nOutput:\n(no output)"

    truncated_output = _truncate_output(output)
    was_truncated = truncated_output != output

    parts = [status_line, "Output:", truncated_output]
    if was_truncated:
        parts.append(
            f"\n(Output truncated to first/last {OUTPUT_HEAD_TAIL_LINES} lines, "
            f"max {MAX_OUTPUT_TOKENS} tokens.)"
        )
        ext = _guess_output_extension(output)
        output_filepath = (
            f"{_TEMP_OUTPUT_DIR}/output_{int(time.time())}_"
            f"{uuid.uuid4().hex[:8]}.{ext}"
        )
        write_result = write_file(
            output_filepath, output, session_id=session_id
        )
        if write_result.startswith("✓"):
            parts.append(f"\nFull output saved to: {output_filepath}")
            parts.append(
                "Use read_output_range_tool(filepath, offset, n_limit) to read "
                "specific character ranges of this file if you need more detail."
            )
        else:
            parts.append(
                f"\n(Failed to save full output to a temp file: {write_result})"
            )

    return "\n".join(parts)


def write_file(filepath: str, content: str, session_id: str, append: bool = False) -> str:
    """
    Write content to a file, routed into the session's sandbox filesystem
    (via sandbox_service) when the microsandbox backend is active there,
    otherwise sandbox_service's own host filesystem.
    """
    try:
        response = _client.post(
            f"/sandboxes/{session_id}/files",
            json={"filepath": filepath, "content": content, "append": append},
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail", str(e)) if e.response is not None else str(e)
        return f"✗ {detail}"
    except httpx.HTTPError as e:
        return f"✗ Failed to reach sandbox service: {e}"

    lines = content.count('\n') + (1 if content and not content.endswith('\n') else 0)
    chars = len(content)
    action = "Appended" if append else "Wrote"
    return f"✓ {action} {chars} characters ({lines} lines) to {filepath}"


def write_file_stream(
    filepath: str,
    chunks: Iterable[bytes],
    session_id: str,
    expected_size: int | None = None,
    timeout: httpx.Timeout | float | None = None,
) -> int:
    """Stream arbitrary bytes into a sandbox without JSON/base64 encoding."""
    response = _client.put(
        f"/sandboxes/{session_id}/files/content",
        params={
            "filepath": filepath,
            **(
                {"expected_size": expected_size}
                if expected_size is not None
                else {}
            ),
        },
        headers={"Content-Type": "application/octet-stream"},
        content=chunks,
        timeout=timeout or _HTTP_TIMEOUT,
    )
    response.raise_for_status()
    return int(response.json().get("size", 0))


def read_file_bytes(
    filepath: str,
    session_id: str,
    timeout: httpx.Timeout | float | None = None,
) -> bytes:
    """Read raw file bytes from inside the session's sandbox (via sandbox_service)."""
    response = _client.get(
        f"/sandboxes/{session_id}/files/content",
        params={"filepath": filepath},
        timeout=timeout or _HTTP_TIMEOUT,
    )
    response.raise_for_status()
    return response.content


def read_output_range(filepath: str, session_id: str, offset: int = 0, n_limit: int = 2000) -> str:
    """
    Read a slice of a text file's content, starting at character `offset`
    and returning up to `n_limit` characters - lets a caller page through a
    large file (e.g. the full output saved by run_terminal) without pulling
    the whole thing into context at once.
    """
    try:
        data = read_file_bytes(filepath, session_id=session_id)
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail", str(e)) if e.response is not None else str(e)
        return f"✗ {detail}"
    except httpx.HTTPError as e:
        return f"✗ Failed to reach sandbox service: {e}"

    text = data.decode("utf-8", errors="replace")
    total_len = len(text)

    if offset < 0:
        offset = max(0, total_len + offset)
    if offset >= total_len:
        return f"(offset {offset} is at/past end of file - total length is {total_len} characters)"

    chunk = text[offset:offset + n_limit]
    end = offset + len(chunk)
    return f"Characters {offset}-{end} of {total_len} total in {filepath}:\n\n{chunk}"


def file_exists(
    filepath: str,
    session_id: str,
    timeout: httpx.Timeout | float | None = None,
) -> bool:
    """Check whether filepath exists in the session's sandbox (via sandbox_service)."""
    try:
        response = _client.get(
            f"/sandboxes/{session_id}/files/exists",
            params={"filepath": filepath},
            timeout=timeout or _HTTP_TIMEOUT,
        )
        response.raise_for_status()
        return response.json().get("exists", False)
    except httpx.HTTPError:
        return False


def normalize_publish_paths(
    source_path: str,
    output_path: str | None = None,
) -> tuple[str, str]:
    """Validate and normalize an explicit /workspace -> /outputs publish."""
    source = posixpath.normpath(source_path.strip())
    if source == "/workspace" or not source.startswith("/workspace/"):
        raise ValueError("source_path must name a file under /workspace")

    if output_path and output_path.strip():
        destination = posixpath.normpath(output_path.strip())
    else:
        relative = posixpath.relpath(source, "/workspace")
        destination = posixpath.normpath(posixpath.join("/outputs", relative))

    if destination == "/outputs" or not destination.startswith("/outputs/"):
        raise ValueError("output_path must name a file under /outputs")
    return source, destination


def publish_artifact(
    source_path: str,
    session_id: str,
    output_path: str | None = None,
) -> str:
    """
    Copy one explicitly selected workspace file into the publish-only
    /outputs tree without exposing or scanning the rest of /workspace.
    """
    try:
        source, destination = normalize_publish_paths(source_path, output_path)
    except ValueError as exc:
        return f"✗ {exc}"

    source_q = shlex.quote(source)
    destination_q = shlex.quote(destination)
    destination_parent_q = shlex.quote(posixpath.dirname(destination))
    command = (
        "set -eu; "
        "mkdir -p -- /workspace /outputs; "
        f"source_real=$(realpath -e -- {source_q}); "
        "workspace_real=$(realpath -e -- /workspace); "
        'case "$source_real" in "$workspace_real"/*) ;; '
        "*) echo 'Source escapes /workspace' >&2; exit 64 ;; esac; "
        '[ -f "$source_real" ] || { echo "Source is not a regular file" >&2; exit 66; }; '
        f"mkdir -p -- {destination_parent_q}; "
        "outputs_real=$(realpath -e -- /outputs); "
        f"destination_parent_real=$(realpath -e -- {destination_parent_q}); "
        'case "$destination_parent_real" in "$outputs_real"|"$outputs_real"/*) ;; '
        "*) echo 'Destination escapes /outputs' >&2; exit 64 ;; esac; "
        f"[ ! -L {destination_q} ] || "
        "{ echo 'Destination may not be a symbolic link' >&2; exit 64; }; "
        f"cp -- \"$source_real\" {destination_q}; "
        f"printf 'Published %s\\n' {destination_q}"
    )
    return run_terminal(command, session_id=session_id)


def list_files(directory: str, session_id: str) -> list[str]:
    """
    Return a list of file paths under `directory` in the session's sandbox,
    via a raw `find` call to sandbox_service's /exec (bypassing
    run_terminal's human-readable wrapper text, since this is consumed
    programmatically - see TerminalAgent._sync_outputs_to_openwebui).
    Returns an empty list if the directory doesn't exist, the command fails,
    or the sandbox is unreachable.
    """
    try:
        response = _client.post(
            f"/sandboxes/{session_id}/exec",
            json={"command": f"find {directory} -type f 2>/dev/null"},
        )
        response.raise_for_status()
        result = response.json()
    except httpx.HTTPError:
        return []

    if not result.get("success", False):
        return []

    output = result.get("output", "") or ""
    return [line.strip() for line in output.splitlines() if line.strip()]


def list_file_metadata(
    directory: str,
    session_id: str,
) -> dict[str, str] | None:
    """
    Snapshot regular files under `directory` as path -> size/mtime signature.

    Returns None when the sandbox cannot be inspected, allowing callers to
    distinguish a failed snapshot from a valid empty directory.
    """
    quoted_directory = shlex.quote(directory)
    command = (
        f"if [ -d {quoted_directory} ]; then "
        f"find {quoted_directory} -type f "
        r"-printf '%p\t%s\t%T@\n'; "
        "fi"
    )
    try:
        response = _client.post(
            f"/sandboxes/{session_id}/exec",
            json={"command": command},
        )
        response.raise_for_status()
        result = response.json()
    except httpx.HTTPError:
        return None

    if not result.get("success", False):
        return None

    return parse_file_metadata_output(result.get("output", "") or "")


def run_python(code: str, session_id: str) -> list[dict]:
    """
    Execute Python code in the session's persistent kernel (via
    sandbox_service's /run-python, backed by the in-VM OI kernel daemon -
    see sandbox_service/msb_sandbox.py:MicrosandboxTerminal.run_python and
    ../../interpreter_kernel/). Unlike run_terminal(), this keeps state
    (variables, imports, matplotlib figures) across calls.

    Returns the raw Open Interpreter-format chunk list (not a formatted
    string) since callers need to distinguish console output from images -
    see TerminalAgent's run_python_tool dispatch, which is the only caller
    that matters for streaming; make_agent_tools' run_python_tool below
    just summarizes this into text for the LLM-facing tool result.
    """
    try:
        response = _client.post(f"/sandboxes/{session_id}/run-python", json={"code": code})
        response.raise_for_status()
        result = response.json()
    except httpx.HTTPError as e:
        return [{"type": "console", "format": "output", "content": f"✗ Failed to reach sandbox service: {e}"}]
    return result.get("chunks", [])


def grep_search(
    query: str,
    session_id: str,
    path: str = ".",
    regex: bool = True,
    case_insensitive: bool = False,
    include: Optional[list[str]] = None,
    max_results: int = 50,
) -> dict:
    """
    Search file contents in the session's sandbox via Open Terminal (via
    sandbox_service's /grep - see sandbox_service/msb_sandbox.py:
    MicrosandboxTerminal.grep_search and ../../interpreter_kernel/).
    """
    body = {
        "query": query,
        "path": path,
        "regex": regex,
        "case_insensitive": case_insensitive,
        "max_results": max_results,
    }
    if include:
        body["include"] = include
    try:
        response = _client.post(f"/sandboxes/{session_id}/grep", json=body)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail", str(e)) if e.response is not None else str(e)
        return {"error": detail}
    except httpx.HTTPError as e:
        return {"error": f"Failed to reach sandbox service: {e}"}


def glob_search(
    pattern: str,
    session_id: str,
    path: str = ".",
    exclude: Optional[list[str]] = None,
    type: str = "any",
    max_results: int = 50,
) -> dict:
    """Search files by name in the session's sandbox via Open Terminal (via sandbox_service's /glob)."""
    body = {"pattern": pattern, "path": path, "type": type, "max_results": max_results}
    if exclude:
        body["exclude"] = exclude
    try:
        response = _client.post(f"/sandboxes/{session_id}/glob", json=body)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail", str(e)) if e.response is not None else str(e)
        return {"error": detail}
    except httpx.HTTPError as e:
        return {"error": f"Failed to reach sandbox service: {e}"}


def make_agent_tools(session_id: str):
    """
    Build a run_terminal_tool / write_file_tool / show_image_tool /
    read_output_range_tool set bound to one specific session's terminal
    (local shell or microsandbox microVM).

    Every TerminalAgent must build its own set via this factory instead of
    sharing module-level tool instances, so concurrent users never end up
    executing commands in - or writing files into - the same underlying
    shell/sandbox.
    """

    @tool
    def run_terminal_tool(command: str) -> str:
        """
        Execute a shell command in a persistent terminal session.
        The shell state (environment variables, working directory, etc.) persists across calls.
        Use this for system commands, installing packages, running scripts, etc.

        Args:
            command: The shell command to execute (e.g., "ls -la", "pip install numpy", "python script.py")

        Returns:
            The output from running the command (stdout/stderr)
        """
        return run_terminal(command, session_id=session_id)

    @tool
    def write_file_tool(filepath: str, content: str, append: bool = False) -> str:
        """
        Write content to a file. Use this for creating or modifying files.
        Handles newlines, encoding, and file permissions properly.

        Args:
            filepath: Path to the file (relative or absolute)
            content: Content to write to the file (newlines are preserved)
            append: If True, append to file; if False (default), overwrite

        Returns:
            Success message with file stats, or error message

        Examples:
            - write_file_tool("script.py", "def hello():\\n    print('Hello')")
            - write_file_tool("data.txt", "new line\\n", append=True)
        """
        return write_file(filepath, content, session_id=session_id, append=append)

    @tool
    def publish_artifact_tool(
        source_path: str,
        output_path: str = "",
    ) -> str:
        """
        Publish one existing workspace file as a user deliverable by safely
        copying it from /workspace into /outputs. Use this instead of making
        /workspace directly downloadable. The source must be a regular file
        under /workspace. The optional destination must be under /outputs;
        when omitted, the workspace-relative path is preserved.

        Args:
            source_path: Existing file under /workspace.
            output_path: Optional destination under /outputs.

        Returns:
            The published /outputs path, or a validation/copy error.
        """
        return publish_artifact(
            source_path,
            session_id=session_id,
            output_path=output_path or None,
        )

    @tool
    def read_output_range_tool(filepath: str, offset: int = 0, n_limit: int = 2000) -> str:
        """
        Read a slice of a (typically large) file's text content - e.g. the
        full command output saved by run_terminal_tool when its result was
        truncated ("Full output saved to: ..."). Lets you page through the
        file incrementally instead of loading it all at once.

        Args:
            filepath: Path to the file (e.g. the "Full output saved to:"
                      path returned by run_terminal_tool).
            offset: Character index to start reading from (0-based, default 0).
            n_limit: Maximum number of characters to return (default 2000).

        Returns:
            The requested character range, prefixed with its position and
            the file's total length, or an error message.
        """
        return read_output_range(filepath, session_id=session_id, offset=offset, n_limit=n_limit)

    @tool
    def run_python_tool(code: str) -> str:
        """
        Execute Python code in a persistent, stateful kernel - like a
        Jupyter notebook cell. Variables, imports, and any state from
        previous run_python_tool calls in this conversation carry over
        automatically. Prefer this over run_terminal_tool for Python data
        analysis/plotting, so you don't have to re-load data or re-import
        libraries every call. Plots created with matplotlib are
        automatically captured and shown to the user - no need to save to
        a file or call show_image_tool for them.

        Args:
            code: Python code to execute

        Returns:
            The console output produced by the code (print statements,
            errors, etc). Images are shown to the user separately by the
            agent loop, not included in this text.
        """
        # NOTE: TerminalAgent's tool-call dispatch calls tools.persistent_
        # terminal.run_python() directly (not this function) so it can
        # stream individual console/image chunks as they're produced. This
        # function's own implementation exists so run_python_tool still
        # behaves correctly if ever invoked outside that dispatch loop
        # (e.g. directly via .invoke()), and so its docstring/schema is
        # what the LLM sees via bind_tools().
        chunks = run_python(code, session_id=session_id)
        texts = [
            c.get("content", "")
            for c in chunks
            if c.get("type") == "console" and c.get("format") != "active_line" and c.get("content")
        ]
        image_count = sum(1 for c in chunks if c.get("type") == "image")
        summary = "\n".join(texts).strip()
        if image_count:
            summary = (summary + f"\n[{image_count} image(s) generated and shown to the user]").strip()
        return summary or "(no output)"

    @tool
    def show_image_tool(filepath: str) -> str:
        """
        Display an image file to the user in the chat interface.
        Call this explicitly whenever you want to show a plot, chart, or any
        other image you have created or found, instead of relying on the file
        just existing on disk. The image is read from disk and streamed to the
        frontend as base64-encoded content.

        Args:
            filepath: Path to the image file (relative or absolute).
                      Supported extensions: .png, .jpg, .jpeg, .gif, .bmp, .webp, .svg

        Returns:
            Success message confirming the image was displayed, or an error message.
        """
        valid_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg'}
        ext = os.path.splitext(filepath)[1].lower()

        if ext not in valid_extensions:
            return f"✗ Unsupported image extension '{ext}' for {filepath}"
        if not file_exists(filepath, session_id):
            return f"✗ Image not found: {filepath}"

        return f"✓ Image ready to display: {filepath}"

    @tool
    def grep_search_tool(
        query: str,
        path: str = ".",
        regex: bool = True,
        case_insensitive: bool = False,
        include: Optional[list[str]] = None,
        max_results: int = 50,
    ) -> str:
        """
        Search file contents for a text or regex pattern. Prefer this over
        `run_terminal_tool("grep -r ...")` - it returns structured matches
        (file, line number, content) and skips binary files automatically.

        Args:
            query: Text or regex pattern to search for.
            path: Directory or file to search in (default: current directory).
            regex: Treat query as a regex (default True); set False for a literal search.
            case_insensitive: Case-insensitive matching (default False).
            include: Glob patterns to filter which files are searched (e.g. ["*.py"]).
            max_results: Maximum number of matches to return (default 50).

        Returns:
            A summary of matches (file:line: content), or an error message.
        """
        result = grep_search(
            query,
            session_id=session_id,
            path=path,
            regex=regex,
            case_insensitive=case_insensitive,
            include=include,
            max_results=max_results,
        )
        if "error" in result:
            return f"✗ {result['error']}"
        matches = result.get("matches", [])
        if not matches:
            return f"No matches for {query!r} in {result.get('path', path)}"
        lines = [
            f"{m.get('file')}:{m['line']}: {m['content']}" if "line" in m else str(m.get("file"))
            for m in matches
        ]
        suffix = " (truncated)" if result.get("truncated") else ""
        return f"{len(matches)} match(es){suffix}:\n" + "\n".join(lines)

    @tool
    def glob_search_tool(
        pattern: str,
        path: str = ".",
        exclude: Optional[list[str]] = None,
        type: str = "any",
        max_results: int = 50,
    ) -> str:
        """
        Search for files/directories by name using a glob pattern (e.g.
        "*.csv", "**/*.py"). Prefer this over `run_terminal_tool("find ...")`
        for locating files by name.

        Args:
            pattern: Glob pattern to search for (e.g. "*.py").
            path: Directory to search within (default: current directory).
            exclude: Glob patterns to exclude from results.
            type: "file", "directory", or "any" (default).
            max_results: Maximum number of matches to return (default 50).

        Returns:
            A list of matching paths, or an error message.
        """
        result = glob_search(
            pattern, session_id=session_id, path=path, exclude=exclude, type=type, max_results=max_results
        )
        if "error" in result:
            return f"✗ {result['error']}"
        matches = result.get("matches", [])
        if not matches:
            return f"No files matching {pattern!r} in {path}"
        lines = [f"{m.get('path')} ({m.get('type')}, {m.get('size')} bytes)" for m in matches]
        suffix = " (truncated)" if result.get("truncated") else ""
        return f"{len(matches)} result(s){suffix}:\n" + "\n".join(lines)

    return (
        run_terminal_tool,
        write_file_tool,
        publish_artifact_tool,
        show_image_tool,
        read_output_range_tool,
        run_python_tool,
        grep_search_tool,
        glob_search_tool,
    )
