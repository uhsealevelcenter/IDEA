"""
Microsandbox-backed persistent terminal.

Wraps the async `microsandbox` SDK behind the same synchronous
(success, output, elapsed_time) interface that `PersistentTerminal`
(terminal_registry.py) exposes, so the rest of this service does not need
to know whether commands are running in a raw local shell or an isolated
microVM sandbox.

Each instance owns one named microVM sandbox (keyed by sandbox_id), and
runs its own background asyncio event loop on a dedicated thread so the
underlying aiohttp-based SDK session stays alive across calls.
"""

import asyncio
import json
import os
import shlex
import threading
import time
import urllib.parse
import uuid
from typing import Optional

DEFAULT_IMAGE = os.getenv("SANDBOX_IMAGE", "python")
DEFAULT_CPUS = int(os.getenv("SANDBOX_CPUS", "1"))
DEFAULT_MEMORY_MB = int(os.getenv("SANDBOX_MEMORY_MB", "1024"))

# Path to client.py *inside* the VM image - see ../interpreter_kernel/.
# Only meaningful when SANDBOX_IMAGE is that image (or one built on top of
# it); run_python() below fails clearly if the VM doesn't have it.
OI_KERNEL_CLIENT_PATH = os.getenv("OI_KERNEL_CLIENT_PATH", "/opt/oi_kernel/client.py")

# Where entrypoint.sh drops Open Terminal's per-VM API key - see
# ../interpreter_kernel/entrypoint.sh. Read once per MicrosandboxTerminal
# instance (cached) via the SDK's own fs.read(), never sent off-VM.
OPEN_TERMINAL_KEY_PATH = os.getenv(
    "OPEN_TERMINAL_KEY_PATH", "/opt/oi_kernel/.open_terminal_api_key"
)
OPEN_TERMINAL_PORT = int(os.getenv("OPEN_TERMINAL_PORT", "8000"))

# Lifecycle policy: idle_timeout auto-drains (stops, does NOT delete) the
# sandbox after this many idle seconds; max_duration is a hard lifetime cap
# regardless of activity. Both are enforced by the microsandbox runtime
# itself - no polling/reaper needed on our side. None disables the policy.
_IDLE_TIMEOUT_ENV = os.getenv("SANDBOX_IDLE_TIMEOUT_SECONDS", "1800")
_MAX_DURATION_ENV = os.getenv("SANDBOX_MAX_DURATION_SECONDS", "")
DEFAULT_IDLE_TIMEOUT = int(_IDLE_TIMEOUT_ENV) if _IDLE_TIMEOUT_ENV else None
DEFAULT_MAX_DURATION = int(_MAX_DURATION_ENV) if _MAX_DURATION_ENV else None


def microsandbox_available() -> bool:
    """Best-effort check for whether the microsandbox runtime can be used here."""
    try:
        import microsandbox  # noqa: F401
    except ImportError:
        return False

    # microsandbox needs KVM (or WHP on Windows) on the host to boot microVMs.
    if os.name == "posix" and os.path.exists("/dev/kvm"):
        return True

    # Allow an explicit override for environments where the check above is
    # unreliable (e.g. custom virtualization setups).
    return os.getenv("SANDBOX_FORCE_MICROSANDBOX", "0") == "1"


class MicrosandboxTerminal:
    """
    Persistent terminal session backed by a named microsandbox microVM.

    Mirrors PersistentTerminal's public surface (run/close) plus
    write_file/read_file for routing file I/O into the sandbox's own
    filesystem instead of the host's.
    """

    def __init__(
        self,
        session_id: str,
        image: str = DEFAULT_IMAGE,
        cpus: int = DEFAULT_CPUS,
        memory: int = DEFAULT_MEMORY_MB,
        idle_timeout: Optional[int] = DEFAULT_IDLE_TIMEOUT,
        max_duration: Optional[int] = DEFAULT_MAX_DURATION,
    ):
        self.session_id = session_id
        self.image = image
        self.cpus = cpus
        self.memory = memory
        self.idle_timeout = idle_timeout
        self.max_duration = max_duration
        self._sandbox = None
        self._open_terminal_key: Optional[str] = None

        # Dedicated event loop + thread so the SDK's connection(s) persist
        # across calls instead of being torn down/recreated each time
        # (which is what would happen if we called asyncio.run() per call).
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

        self._connect_or_create()

    def _run(self, coro, timeout: Optional[float] = None):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def _connect_or_create(self):
        from microsandbox import Sandbox

        async def _get_sandbox():
            try:
                # Resumes an existing stopped-but-not-removed sandbox of this
                # name with its filesystem state intact, if one exists.
                return await Sandbox.start(self.session_id, detached=True)
            except Exception:
                create_kwargs = dict(
                    image=self.image,
                    cpus=self.cpus,
                    memory=self.memory,
                    detached=True,
                )
                if self.idle_timeout is not None:
                    create_kwargs["idle_timeout"] = self.idle_timeout
                if self.max_duration is not None:
                    create_kwargs["max_duration"] = self.max_duration
                return await Sandbox.create(self.session_id, **create_kwargs)

        self._sandbox = self._run(_get_sandbox())

    def run(self, command: str) -> tuple[bool, str, float]:
        """
        Execute a shell command inside the sandbox.

        Returns:
            (success, output, elapsed_time) tuple, matching PersistentTerminal.run().
        """
        start_time = time.time()
        try:
            output = self._run(self._sandbox.shell(command), timeout=self._exec_timeout(command))
        except Exception as e:
            elapsed_time = time.time() - start_time
            return False, f"Sandbox execution failed: {e}", elapsed_time

        elapsed_time = time.time() - start_time
        success = output.exit_code == 0
        text = output.stdout_text or ""
        if getattr(output, "stderr_text", None):
            text = f"{text}\n{output.stderr_text}" if text else output.stderr_text
        return success, text.strip("\r\n"), elapsed_time

    @staticmethod
    def _exec_timeout(command: str) -> float:
        # Generous ceiling; mirrors PersistentTerminal's default 1800s window.
        return 1800.0

    def run_python(self, code: str) -> dict:
        """
        Execute Python code in this sandbox's persistent Jupyter-backed
        kernel (see ../interpreter_kernel/daemon.py, baked into the VM
        image at SANDBOX_IMAGE). Unlike run(), which spawns a fresh
        subprocess per shell command, this reuses the same in-VM kernel
        process across calls within the VM's lifetime - variables,
        imports, and matplotlib figures persist across turns.

        Code is written to a temp file inside the sandbox (via the SDK's
        own fs.write, already used by write_file()) rather than passed as
        a shell argument, to sidestep quoting/escaping entirely - the
        in-VM client.py then reads and runs it. Returns the daemon's raw
        {"chunks": [...]} payload (Open Interpreter chunk format - see
        interpreter/core/computer/terminal/terminal.py), or a synthetic
        one-chunk error payload on failure, so callers never have to
        special-case exceptions from this method.
        """
        tmp_path = f"/tmp/.oi_kernel_code_{uuid.uuid4().hex}.py"
        try:
            self._run(self._sandbox.fs.write(tmp_path, code.encode("utf-8")))
            output = self._run(
                self._sandbox.shell(f"python3 {OI_KERNEL_CLIENT_PATH} --run-file {tmp_path}"),
                timeout=self._exec_timeout(code),
            )
        except Exception as e:
            return {"chunks": [{"type": "console", "format": "output", "content": f"Kernel exec failed: {e}"}]}
        finally:
            try:
                self._run(self._sandbox.shell(f"rm -f {tmp_path}"))
            except Exception:
                pass

        text = (output.stdout_text or "").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            stderr = getattr(output, "stderr_text", None) or ""
            content = text or stderr or "(no output from kernel client)"
            return {"chunks": [{"type": "console", "format": "output", "content": content}]}

    def _get_open_terminal_key(self, retries: int = 10, delay: float = 0.5) -> str:
        """
        Read Open Terminal's per-VM API key, generated by entrypoint.sh at
        boot (see ../interpreter_kernel/entrypoint.sh) and never sent off
        this VM as a network request - only ever read via the SDK's own
        fs.read(), the same channel already used for file I/O. Cached after
        the first successful read (the key is stable for the VM's lifetime).

        A short retry loop covers the case where this is called immediately
        after Sandbox.create() and entrypoint.sh hasn't written the key file
        yet.
        """
        if self._open_terminal_key:
            return self._open_terminal_key

        last_error: Optional[Exception] = None
        for _ in range(retries):
            try:
                data = self._run(self._sandbox.fs.read(OPEN_TERMINAL_KEY_PATH))
                key = data.decode("utf-8").strip()
                if key:
                    self._open_terminal_key = key
                    return key
            except Exception as e:
                last_error = e
            time.sleep(delay)

        raise RuntimeError(
            f"Open Terminal API key not available at {OPEN_TERMINAL_KEY_PATH} "
            f"in sandbox {self.session_id} after {retries} retries: {last_error}"
        )

    def _open_terminal_get(self, path: str, params: dict) -> dict:
        """
        GET a query-param endpoint on Open Terminal's own API, running
        *inside* this VM on 127.0.0.1:OPEN_TERMINAL_PORT - reached the same
        way run_python() reaches the kernel daemon: build the full command
        as a string and hand it to sandbox.shell(), rather than requiring
        microsandbox to expose any port to the host. See
        ../interpreter_kernel/entrypoint.sh / Dockerfile for how Open
        Terminal ends up running in this VM at all.
        """
        key = self._get_open_terminal_key()
        query = urllib.parse.urlencode(params, doseq=True)
        url = f"http://127.0.0.1:{OPEN_TERMINAL_PORT}{path}?{query}"
        cmd = (
            "curl -s -w '\\n%{http_code}' "
            f"-H {shlex.quote('Authorization: Bearer ' + key)} {shlex.quote(url)}"
        )
        output = self._run(self._sandbox.shell(cmd), timeout=60.0)
        text = output.stdout_text or ""
        body, _, status = text.rpartition("\n")
        try:
            status_code = int(status)
        except ValueError:
            # curl produced no parseable trailing status line at all (e.g.
            # connection refused - Open Terminal not up yet) - treat as a
            # hard failure rather than silently returning a bogus 200-shaped
            # response built from whatever curl did print (usually nothing).
            raise RuntimeError(f"Open Terminal {path}: no response (curl output: {text!r})")

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"raw": body}

        if status_code >= 400:
            detail = parsed.get("detail", body) if isinstance(parsed, dict) else body
            raise RuntimeError(f"Open Terminal {path} returned {status_code}: {detail}")
        return parsed

    def grep_search(
        self,
        query: str,
        path: str = ".",
        regex: bool = True,
        case_insensitive: bool = False,
        include: Optional[list[str]] = None,
        max_results: int = 50,
    ) -> dict:
        """Search file contents via Open Terminal's /files/grep - see main.py's grep_search endpoint upstream."""
        params: dict = {
            "query": query,
            "path": path,
            "regex": str(regex).lower(),
            "case_insensitive": str(case_insensitive).lower(),
            "max_results": max_results,
        }
        if include:
            params["include"] = include
        return self._open_terminal_get("/files/grep", params)

    def glob_search(
        self,
        pattern: str,
        path: str = ".",
        exclude: Optional[list[str]] = None,
        type: str = "any",
        max_results: int = 50,
    ) -> dict:
        """Search files by name via Open Terminal's /files/glob - see main.py's glob_search endpoint upstream."""
        params: dict = {"pattern": pattern, "path": path, "type": type, "max_results": max_results}
        if exclude:
            params["exclude"] = exclude
        return self._open_terminal_get("/files/glob", params)

    def write_file(self, filepath: str, content: str, append: bool = False) -> None:
        """Write content to a file inside the sandbox's filesystem."""
        data = content.encode("utf-8")
        if append:
            # No native append API - emulate via shell so it stays atomic
            # inside the sandbox rather than round-tripping bytes twice.
            escaped = content.replace("'", "'\\''")
            self._run(self._sandbox.shell(f"printf '%s' '{escaped}' >> {filepath}"))
        else:
            self._run(self._sandbox.fs.write(filepath, data))

    def read_file(self, filepath: str) -> bytes:
        """Read raw bytes of a file from inside the sandbox (e.g. for image display)."""
        return self._run(self._sandbox.fs.read(filepath))

    def close(self):
        """
        Gracefully stop the sandbox (state-preserving) and shut down the
        background event loop.

        Uses stop() rather than kill()+remove() so a later
        Sandbox.start(session_id) - e.g. the user reconnecting, or this
        object being recreated on the next command - resumes with the same
        filesystem/installed-package state intact. Idle sandboxes are also
        auto-drained (stopped, not removed) by the microsandbox runtime
        itself via the idle_timeout/max_duration policy set at creation.
        """
        try:
            if self._sandbox is not None:
                self._run(self._sandbox.stop())
        except Exception:
            pass
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def destroy(self):
        """
        Permanently delete the named sandbox (kill + remove) - unlike
        close(), this is NOT resumable. Only call this for an explicit
        user-initiated "wipe my environment" action, not routine cleanup.
        """
        try:
            from microsandbox import Sandbox

            if self._sandbox is not None:
                self._run(self._sandbox.kill())
            self._run(Sandbox.remove(self.session_id))
        except Exception:
            pass
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
