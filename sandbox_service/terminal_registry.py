"""
Terminal registry - the stateful core of the sandbox service.

Owns every live terminal (either a raw local shell, PersistentTerminal/
pexpect-backed, or an isolated microVM sandbox, MicrosandboxTerminal) and the
per-sandbox_id lock that serializes command execution against it. This is
the ONE place in the whole system where this kind of process/OS-level state
is allowed to live - the langgraph service that calls into this over HTTP
(see main.py) is otherwise stateless with respect to terminals.
"""

import os
import threading
import time
import uuid
import pexpect

from msb_sandbox import MicrosandboxTerminal, microsandbox_available

# "auto" picks microsandbox when it's importable and /dev/kvm is available,
# otherwise falls back to a plain local shell. Override with "local" or
# "microsandbox" to force a specific backend (e.g. local dev on a Mac with no KVM).
SANDBOX_BACKEND = os.getenv("SANDBOX_BACKEND", "auto")


class PersistentTerminal:
    """
    Persistent terminal session for an LLM agent.

    Commands are sent directly into a live interactive shell (not a spawned
    subshell), so cd / export / source / shell functions persist across calls.
    Each command is bracketed by a unique random sentinel that also carries the
    exit code, which makes output capture and success detection reliable.
    """

    def __init__(self, shell: str = '/bin/bash', timeout: int = 1800):
        self.timeout = timeout
        import uuid
        # A fresh, hard-to-collide marker base for this session.
        self._marker = f"__AGENT_{uuid.uuid4().hex}__"
        # spawn with a generous master timeout; per-command timeouts override.
        self.shell = pexpect.spawn(
            shell, encoding='utf-8', timeout=timeout, echo=False
        )
        self.shell.setwinsize(50, 200)  # avoid line-wrapping in captured output

        # Turn off echo at the tty level too, and set a stable, unique prompt.
        self._bootstrap()

    def _bootstrap(self):
        """Initialize shell with disabled echo and empty prompt."""
        # Wait for initial shell startup
        time.sleep(0.1)
        # Clear any prompt command
        self.shell.sendline('unset PROMPT_COMMAND')
        time.sleep(0.1)
        # Set empty prompt to avoid prompt in output
        self.shell.sendline('export PS1=""')
        time.sleep(0.1)
        # Disable terminal echo
        self.shell.sendline('stty -echo')
        time.sleep(0.2)
        # Drain any pending output
        try:
            self.shell.expect('.+', timeout=1)
        except pexpect.TIMEOUT:
            pass

    def run(self, command: str, progress_callback=None) -> tuple[bool, str, float]:
        """
        Execute a command in the persistent shell with progress monitoring.

        Args:
            command: Shell command to execute
            progress_callback: Optional function to call with elapsed time updates

        Returns:
            (success, output, elapsed_time) tuple
        """
        import re

        start_time = time.time()
        last_update = start_time

        print(f"Command started at {time.strftime('%H:%M:%S')}")

        start = f"{self._marker}_START"
        # End sentinel embeds the real exit code of the user's command.
        end = f"{self._marker}_END"

        # echo start; run command; echo "END <exit-code>" regardless of success.
        # Note: no `set -e`. The command runs with normal shell semantics.
        wrapped = f'echo {start}; {command}\n__rc=$?; echo "{end} $__rc"'

        self.shell.sendline(wrapped)

        # Find the start marker first so we don't capture the echoed wrapper.
        try:
            self.shell.expect_exact(start, timeout=self.timeout)
        except (pexpect.TIMEOUT, pexpect.EOF):
            elapsed_time = time.time() - start_time
            return False, "Shell desynchronized before command start.", elapsed_time

        # Capture everything up to the END marker + exit code with progress monitoring.
        pattern = re.compile(re.escape(end) + r" (\d+)")
        try:
            # Monitor for progress while waiting
            while True:
                try:
                    self.shell.expect(pattern, timeout=5)
                    # Got the end marker
                    exit_code = int(self.shell.match.group(1))
                    output = self.shell.before

                    # Give shell a moment to settle
                    time.sleep(0.05)

                    elapsed_time = time.time() - start_time
                    success = (exit_code == 0)

                    mins, secs = divmod(int(elapsed_time), 60)
                    if mins > 0:
                        print(f"Completed in {mins}m {secs}s")
                    else:
                        print(f"Completed in {secs}s")

                    return success, self._clean(output), elapsed_time

                except pexpect.TIMEOUT:
                    elapsed = time.time() - start_time

                    # Show progress every 5 seconds
                    if time.time() - last_update >= 5:
                        mins, secs = divmod(int(elapsed), 60)
                        if mins > 0:
                            print(f"Still running... {mins}m {secs}s elapsed")
                        else:
                            print(f"Still running... {secs}s elapsed")
                        last_update = time.time()

                        if progress_callback:
                            progress_callback(elapsed)

                    # Check for hard timeout
                    if elapsed > self.timeout:
                        partial = self.shell.before or ""
                        self._interrupt()
                        elapsed_time = time.time() - start_time
                        mins, secs = divmod(int(elapsed_time), 60)
                        return False, f"Command timed out after {mins}m {secs}s\nPartial output:\n{self._clean(partial)}", elapsed_time

                    # Continue waiting
                    continue

        except pexpect.EOF:
            elapsed_time = time.time() - start_time
            return False, "Shell process exited unexpectedly.", elapsed_time

    def _interrupt(self):
        """Send Ctrl-C to interrupt the current command."""
        try:
            self.shell.sendcontrol('c')
            time.sleep(0.1)
            # Drain any pending output
            try:
                self.shell.expect('.+', timeout=0.5)
            except pexpect.TIMEOUT:
                pass
        except (pexpect.TIMEOUT, pexpect.EOF):
            pass  # best effort

    @staticmethod
    def _clean(text: str) -> str:
        """Strip leading/trailing whitespace from output."""
        return text.strip('\r\n')

    def close(self):
        """Close the shell session."""
        try:
            self.shell.sendline('exit')
            self.shell.close(force=True)
        except:
            pass

    def destroy(self):
        """Alias for close() - a local shell has no separate resumable state to preserve."""
        self.close()


# One terminal instance per sandbox_id (in practice, one per user - see
# langgraph/agents/terminal_agent.py) - and one lock per sandbox_id, since a
# sandbox/shell may be shared across a user's multiple open tabs/conversations
# and is not safe for concurrent command execution.
_terminals: dict = {}
_terminal_locks: dict = {}
_registry_lock = threading.Lock()
_active_python_runs: dict[str, tuple[str, str, object]] = {}
_active_codex_runs: dict[str, tuple[str, object]] = {}


def _use_microsandbox() -> bool:
    if SANDBOX_BACKEND == "local":
        return False
    if SANDBOX_BACKEND == "microsandbox":
        return True
    return microsandbox_available()


def _get_lock(sandbox_id: str) -> threading.Lock:
    """Get or create the execution lock for a given sandbox_id's terminal."""
    with _registry_lock:
        lock = _terminal_locks.get(sandbox_id)
        if lock is None:
            lock = threading.Lock()
            _terminal_locks[sandbox_id] = lock
        return lock


def _get_terminal(sandbox_id: str):
    """Get or create the terminal (sandboxed or local) for a given sandbox_id."""
    with _registry_lock:
        if sandbox_id not in _terminals:
            if _use_microsandbox():
                _terminals[sandbox_id] = MicrosandboxTerminal(sandbox_id)
            else:
                _terminals[sandbox_id] = PersistentTerminal()
        return _terminals[sandbox_id]


def stop_all_terminals() -> int:
    """
    Gracefully stop every cached terminal - called from main.py's shutdown
    hook so a container stop/restart cleanly unmounts each microVM's disk
    overlay first, instead of the runtime SIGKILLing it mid-write. An
    unclean kill can leave upper.ext4 (see msb_sandbox.py) in a state the
    runtime won't resume from on next start, silently losing that
    sandbox's files even with persistent storage mounted.

    Returns the number of terminals stopped.
    """
    with _registry_lock:
        sandbox_ids = list(_terminals.keys())
    count = 0
    for sandbox_id in sandbox_ids:
        if stop_terminal(sandbox_id):
            count += 1
    return count


def stop_terminal(sandbox_id: str) -> bool:
    """
    Gracefully stop (state-preserving) and remove a sandbox_id's terminal
    from the in-process cache. The underlying sandbox/shell can still be
    resumed later via _get_terminal() with the same sandbox_id.

    Returns True if a terminal existed and was stopped, False otherwise.
    """
    with _get_lock(sandbox_id):
        terminal = _terminals.pop(sandbox_id, None)
        if terminal is not None:
            terminal.close()
            return True
        return False


def destroy_terminal(sandbox_id: str) -> bool:
    """
    Permanently delete a sandbox_id's terminal (kill + remove the sandbox,
    or close the local shell) - NOT resumable. Only call this for an
    explicit user-initiated "wipe my environment" action.

    Returns True if a terminal existed and was destroyed, False otherwise.
    """
    with _get_lock(sandbox_id):
        terminal = _terminals.pop(sandbox_id, None)
        if terminal is not None:
            terminal.destroy()
            return True
        return False


def run_command(command: str, sandbox_id: str) -> tuple[bool, str, float]:
    """Execute a shell command in sandbox_id's persistent terminal session."""
    terminal = _get_terminal(sandbox_id)
    with _get_lock(sandbox_id):
        return terminal.run(command)


def write_file(filepath: str, content: str, sandbox_id: str, append: bool = False) -> None:
    """
    Write content to a file, routed into sandbox_id's sandbox filesystem
    when the microsandbox backend is active, otherwise the host filesystem.
    Raises on failure (PermissionError, IsADirectoryError, etc.) - callers
    (main.py) translate these into HTTP error responses.
    """
    terminal = _get_terminal(sandbox_id)
    with _get_lock(sandbox_id):
        if isinstance(terminal, MicrosandboxTerminal):
            terminal.write_file(filepath, content, append=append)
        else:
            mode = 'a' if append else 'w'
            dirpath = os.path.dirname(filepath)
            if dirpath:
                os.makedirs(dirpath, exist_ok=True)
            with open(filepath, mode, encoding='utf-8') as f:
                f.write(content)


def write_file_bytes(filepath: str, source, sandbox_id: str) -> None:
    """Atomically stream raw bytes into sandbox_id's private filesystem."""
    terminal = _get_terminal(sandbox_id)
    with _get_lock(sandbox_id):
        if isinstance(terminal, MicrosandboxTerminal):
            terminal.write_file_bytes(filepath, source)
            return

        dirpath = os.path.dirname(filepath)
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)
        temporary_path = f"{filepath}.idea-upload-{uuid.uuid4().hex}"
        try:
            with open(temporary_path, "wb") as output:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
            os.replace(temporary_path, filepath)
        finally:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def run_python(
    code: str,
    sandbox_id: str,
    kernel_id: str = "default",
    run_id: str = "",
) -> dict:
    """
    Execute Python code in sandbox_id's persistent kernel (microsandbox
    backend only - see MicrosandboxTerminal.run_python). The local pexpect
    fallback has no per-VM kernel image to run this against, so it returns
    a clear error chunk instead of raising - callers (langgraph) degrade to
    run_terminal_tool in that case rather than failing the whole turn.
    """
    terminal = _get_terminal(sandbox_id)
    with _get_lock(sandbox_id):
        if isinstance(terminal, MicrosandboxTerminal):
            if run_id:
                with _registry_lock:
                    _active_python_runs[run_id] = (sandbox_id, kernel_id, terminal)
            try:
                return terminal.run_python(code, kernel_id=kernel_id, run_id=run_id)
            finally:
                if run_id:
                    with _registry_lock:
                        _active_python_runs.pop(run_id, None)
        return {
            "chunks": [{
                "type": "console",
                "format": "error",
                "content": (
                    "✗ Persistent Python kernel requires the microsandbox "
                    "backend (SANDBOX_BACKEND=microsandbox|auto with a "
                    "sandbox image that includes the OI kernel - see "
                    "interpreter_kernel/). Use run_terminal_tool instead."
                ),
            }]
        }


def run_codex(request: dict, sandbox_id: str) -> dict:
    """Execute Codex inside the user's microVM; never fall back to the host."""
    terminal = _get_terminal(sandbox_id)
    if not isinstance(terminal, MicrosandboxTerminal):
        return {
            "ok": False,
            "status": "failed",
            "error": (
                "Codex delegation requires the microsandbox backend and an "
                "IDEA guest image containing the Codex runner."
            ),
            "events": [],
        }
    run_id = str(request.get("run_id", ""))
    with _get_lock(sandbox_id):
        if run_id:
            with _registry_lock:
                _active_codex_runs[run_id] = (sandbox_id, terminal)
        try:
            return terminal.run_codex(request)
        finally:
            if run_id:
                with _registry_lock:
                    _active_codex_runs.pop(run_id, None)


def interrupt_run(sandbox_id: str, run_id: str) -> bool:
    """Interrupt without taking the sandbox execution lock held by the run."""
    with _registry_lock:
        active_codex = _active_codex_runs.get(run_id)
        active = _active_python_runs.get(run_id)
    if active_codex and active_codex[0] == sandbox_id:
        return active_codex[1].interrupt_codex(run_id)
    if not active or active[0] != sandbox_id:
        return False
    _, kernel_id, terminal = active
    if not isinstance(terminal, MicrosandboxTerminal):
        return False
    return terminal.interrupt_python(kernel_id)


def grep_search(sandbox_id: str, **kwargs) -> dict:
    """
    Search file contents in sandbox_id's VM via Open Terminal (microsandbox
    backend only - see MicrosandboxTerminal.grep_search). Raises on the
    local backend / on failure - callers (langgraph) already wrap tool
    calls in their own try/except, unlike run_python's synthetic-chunk
    convention which exists specifically for mid-stream error display.
    """
    terminal = _get_terminal(sandbox_id)
    with _get_lock(sandbox_id):
        if not isinstance(terminal, MicrosandboxTerminal):
            raise RuntimeError(
                "grep_search requires the microsandbox backend with an "
                "Open Terminal-based sandbox image - see interpreter_kernel/."
            )
        return terminal.grep_search(**kwargs)


def glob_search(sandbox_id: str, **kwargs) -> dict:
    """Search files by name in sandbox_id's VM via Open Terminal - see grep_search's docstring."""
    terminal = _get_terminal(sandbox_id)
    with _get_lock(sandbox_id):
        if not isinstance(terminal, MicrosandboxTerminal):
            raise RuntimeError(
                "glob_search requires the microsandbox backend with an "
                "Open Terminal-based sandbox image - see interpreter_kernel/."
            )
        return terminal.glob_search(**kwargs)


def read_file_bytes(filepath: str, sandbox_id: str) -> bytes:
    """Read raw file bytes from inside sandbox_id's sandbox, or the host disk."""
    terminal = _get_terminal(sandbox_id)
    with _get_lock(sandbox_id):
        if isinstance(terminal, MicrosandboxTerminal):
            return terminal.read_file(filepath)
        with open(filepath, 'rb') as f:
            return f.read()


def file_exists(filepath: str, sandbox_id: str) -> bool:
    """Check whether filepath exists in sandbox_id's sandbox, or the host disk."""
    terminal = _get_terminal(sandbox_id)
    with _get_lock(sandbox_id):
        if isinstance(terminal, MicrosandboxTerminal):
            return terminal.file_exists(filepath)
        return os.path.isfile(filepath)
