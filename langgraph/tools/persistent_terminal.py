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

import os
import httpx
from langchain_core.tools import tool

# Base URL of the sandbox_service (see docker-compose.yml). A generous
# read timeout is used since commands can legitimately run for a long time
# (sandbox_service's own per-command ceiling is 1800s).
SANDBOX_SERVICE_URL = os.getenv("SANDBOX_SERVICE_URL", "http://sandbox:8020").rstrip("/")
_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=1800.0, write=60.0, pool=10.0)

_client = httpx.Client(base_url=SANDBOX_SERVICE_URL, timeout=_HTTP_TIMEOUT)


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
        The output from running the command (stdout/stderr)
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

    if success:
        return f"✓ Command executed successfully in {time_str}.\nOutput:\n{output if output else '(no output)'}"
    else:
        return f"✗ Command failed after {time_str}.\nOutput:\n{output}"


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


def read_file_bytes(filepath: str, session_id: str) -> bytes:
    """Read raw file bytes from inside the session's sandbox (via sandbox_service)."""
    response = _client.get(f"/sandboxes/{session_id}/files/content", params={"filepath": filepath})
    response.raise_for_status()
    return response.content


def file_exists(filepath: str, session_id: str) -> bool:
    """Check whether filepath exists in the session's sandbox (via sandbox_service)."""
    try:
        response = _client.get(f"/sandboxes/{session_id}/files/exists", params={"filepath": filepath})
        response.raise_for_status()
        return response.json().get("exists", False)
    except httpx.HTTPError:
        return False


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


def make_agent_tools(session_id: str):
    """
    Build a run_terminal_tool / write_file_tool / show_image_tool set bound to
    one specific session's terminal (local shell or microsandbox microVM).

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

    return run_terminal_tool, write_file_tool, show_image_tool
