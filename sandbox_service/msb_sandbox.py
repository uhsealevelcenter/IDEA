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
import contextlib
import json
import os
import queue
import signal
import shlex
import threading
import time
import urllib.parse
import uuid
from typing import Callable, Optional

# ioctl request number for KVM_GET_API_VERSION (arch-independent, see
# linux/kvm.h) - used by _kvm_functional() below. Imported lazily inside
# that function instead since fcntl doesn't exist on non-POSIX platforms.
_KVM_GET_API_VERSION = 0xAE00

DEFAULT_IMAGE = os.getenv("SANDBOX_IMAGE", "python")
DEFAULT_CPUS = int(os.getenv("SANDBOX_CPUS", "1"))
DEFAULT_MEMORY_MB = int(os.getenv("SANDBOX_MEMORY_MB", "1024"))
# Optional absolute directory inside the sandbox-service container which is
# exposed read-only at /app/data in every microVM. docker-compose.yml mounts
# the administrator-managed idea_shared_data volume here. Leaving it unset
# preserves standalone/non-Compose behavior.
DEFAULT_SHARED_DATA_HOST_PATH = os.getenv("SHARED_DATA_HOST_PATH", "").strip()
SHARED_DATA_GUEST_PATH = "/app/data"
SHARED_DATA_REQUIRED_PATHS = (
    "metadata/fd_metadata.geojson",
    "benchmarks/all_benchmarks.json",
    "altimetry/cmems_altimetry_regrid.nc",
    "InSight",
)

# Path to client.py *inside* the VM image - see ../interpreter_kernel/.
# Only meaningful when SANDBOX_IMAGE is that image (or one built on top of
# it); run_python() below fails clearly if the VM doesn't have it.
OI_KERNEL_CLIENT_PATH = os.getenv("OI_KERNEL_CLIENT_PATH", "/opt/oi_kernel/client.py")
CODEX_RUNNER_PATH = os.getenv("CODEX_RUNNER_PATH", "/opt/oi_kernel/codex_runner.py")

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


def shared_data_volumes(host_path: str) -> dict:
    """Build the read-only Microsandbox mount mapping for shared data."""
    if not host_path:
        return {}
    shared_data_path = os.path.abspath(host_path)
    if not os.path.isdir(shared_data_path):
        raise RuntimeError(
            f"SHARED_DATA_HOST_PATH is not a directory: {shared_data_path}"
        )
    missing = [
        relative_path
        for relative_path in SHARED_DATA_REQUIRED_PATHS
        if not os.path.exists(os.path.join(shared_data_path, relative_path))
    ]
    if missing:
        raise RuntimeError(
            "Shared data is not initialized; missing "
            f"{', '.join(missing)}. See shared_data/README.md."
        )
    from microsandbox import Volume

    return {
        SHARED_DATA_GUEST_PATH: Volume.bind(
            shared_data_path,
            readonly=True,
            noexec=True,
            nosuid=True,
            nodev=True,
        )
    }


def _kvm_functional(path: str = "/dev/kvm") -> bool:
    """
    True only if `path` is a real, usable KVM device node - not just a path
    that happens to exist. This matters because docker-compose.yml's
    `sandbox.devices` always binds *something* to /dev/kvm (defaulting to
    /dev/null via KVM_DEVICE_PATH when unset, e.g. local dev on a Mac) so
    Compose doesn't hard-fail on hosts without real KVM. A plain
    os.path.exists() check would then incorrectly return True for that
    dummy device too.

    Mirrors the standard kvm-ok check: open the device and issue
    KVM_GET_API_VERSION, which only a real KVM device answers with 12.
    """
    try:
        import fcntl
    except ImportError:
        return False
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        return fcntl.ioctl(fd, _KVM_GET_API_VERSION, 0) == 12
    except OSError:
        return False
    finally:
        os.close(fd)


def microsandbox_available() -> bool:
    """Best-effort check for whether the microsandbox runtime can be used here."""
    try:
        import microsandbox  # noqa: F401
    except ImportError:
        return False

    # microsandbox needs KVM (or WHP on Windows) on the host to boot microVMs.
    if os.name == "posix" and _kvm_functional("/dev/kvm"):
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
        shared_data_host_path: str = DEFAULT_SHARED_DATA_HOST_PATH,
    ):
        self.session_id = session_id
        self.image = image
        self.cpus = cpus
        self.memory = memory
        self.idle_timeout = idle_timeout
        self.max_duration = max_duration
        self.shared_data_host_path = shared_data_host_path
        self._sandbox = None
        self._open_terminal_key: Optional[str] = None
        self._cwd: Optional[str] = None

        # Dedicated event loop + thread so the SDK's connection(s) persist
        # across calls instead of being torn down/recreated each time
        # (which is what would happen if we called asyncio.run() per call).
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

        self._connect_or_create()

    def _run(self, coro_factory, timeout: Optional[float] = None):
        """
        Run `coro_factory()` (a zero-arg callable returning an awaitable) on
        this instance's dedicated background loop/thread, and block the
        calling thread for the result.

        `coro_factory` must be a *callable*, not a pre-evaluated
        coroutine/awaitable - the microsandbox SDK's async methods (e.g.
        `.shell()`, `.fs.write()`) bind to whichever event loop is running
        at the moment they're *called*, returning an already-scheduled
        Future tied to that loop rather than a portable coroutine object.
        Since this method is normally invoked from FastAPI's request
        handler (running on uvicorn's own event loop, not self._loop),
        evaluating e.g. `self._sandbox.shell(command)` eagerly at the call
        site - then handing the result to `run_coroutine_threadsafe()`
        targeting self._loop - raises "TypeError: A coroutine object is
        required" (self._loop != uvicorn's loop). Wrapping the SDK call in
        a lambda/callable defers it until it's actually executing inside
        self._loop's thread, where `asyncio.get_running_loop()` correctly
        resolves to self._loop.
        """
        async def _runner():
            return await coro_factory()

        future = asyncio.run_coroutine_threadsafe(_runner(), self._loop)
        return future.result(timeout=timeout)

    def _exec(self, coro_factory, timeout: Optional[float] = None):
        """
        Like `_run()`, but resilient to the sandbox having been stopped out
        from under us since we last connected/created it.

        `self._sandbox` is a handle obtained once (in `_connect_or_create()`,
        called only from `__init__`) and then cached for this
        MicrosandboxTerminal's lifetime - but `terminal_registry.py` also
        caches *this whole object* per sandbox_id for the life of the
        service process. The microsandbox runtime itself can independently
        stop the underlying microVM at any time via `idle_timeout`/
        `max_duration` (see module docstring constants above) with no
        callback to us, so a long-idle-then-resumed session hits a cached
        handle pointing at a VM that's no longer running - surfacing as
        "sandbox ... has no agent endpoint (is it running?)" from the SDK.
        On that failure, reconnect (`Sandbox.start()` resumes a
        stopped-but-not-removed sandbox with its filesystem intact - see
        `_connect_or_create()`) and retry once before giving up.
        """
        try:
            return self._run(coro_factory, timeout=timeout)
        except Exception:
            self._connect_or_create()
            return self._run(coro_factory, timeout=timeout)

    def _connect_or_create(self):
        from microsandbox import Sandbox
        from microsandbox.errors import SandboxNotFoundError

        async def _get_sandbox():
            # Explicitly branch on the sandbox's actual current state
            # instead of a blind try/except-fallback-to-create(): calling
            # Sandbox.create() on a name that already exists (e.g. because
            # Sandbox.start() raised for some *other* reason - already
            # running, a transient RPC error, etc.) silently reprovisions
            # it, wiping its filesystem. Sandbox.get() + status is a
            # read-only check with no such side effect, so use it to
            # decide which of create()/start()/connect() is actually safe.
            try:
                handle = await Sandbox.get(self.session_id)
            except SandboxNotFoundError:
                handle = None

            if handle is None:
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
                volumes = shared_data_volumes(self.shared_data_host_path)
                if volumes:
                    create_kwargs["volumes"] = volumes
                return await Sandbox.create(self.session_id, **create_kwargs)

            await handle.refresh()
            if handle.status == "running":
                return await handle.connect()
            # Resumes an existing stopped-but-not-removed sandbox of this
            # name with its filesystem state intact.
            return await Sandbox.start(self.session_id, detached=True)

        self._sandbox = self._run(_get_sandbox)

    def run(self, command: str) -> tuple[bool, str, float]:
        """
        Execute a shell command inside the sandbox.

        Returns:
            (success, output, elapsed_time) tuple, matching PersistentTerminal.run().
        """
        start_time = time.time()
        try:
            output = self._exec(lambda: self._sandbox.shell(command), timeout=self._exec_timeout(command))
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

    def run_python(
        self,
        code: str,
        kernel_id: str = "default",
        run_id: str = "",
    ) -> dict:
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
            self._exec(lambda: self._sandbox.fs.write(tmp_path, code.encode("utf-8")))
            output = self._exec(
                lambda: self._sandbox.shell(
                    f"python3 {OI_KERNEL_CLIENT_PATH} --run-file {tmp_path} "
                    f"--kernel-id {shlex.quote(kernel_id)} --run-id {shlex.quote(run_id)}"
                ),
                timeout=self._exec_timeout(code),
            )
        except Exception as e:
            return {"chunks": [{"type": "console", "format": "error", "content": f"Kernel exec failed: {e}"}]}
        finally:
            try:
                self._exec(lambda: self._sandbox.shell(f"rm -f {tmp_path}"))
            except Exception:
                pass

        text = (output.stdout_text or "").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            stderr = getattr(output, "stderr_text", None) or ""
            content = text or stderr or "(no output from kernel client)"
            return {"chunks": [{"type": "console", "format": "error", "content": content}]}

    def run_python_stream(
        self,
        code: str,
        kernel_id: str = "default",
        run_id: str = "",
        cancelled: Optional[Callable[[], bool]] = None,
    ):
        """Yield persistent-kernel chunks as the guest produces them."""
        tmp_path = f"/tmp/.oi_kernel_code_{uuid.uuid4().hex}.py"
        events: queue.Queue = queue.Queue()
        sentinel = object()
        stderr_parts: list[bytes] = []
        exit_code = 0

        try:
            self._exec(lambda: self._sandbox.fs.write(tmp_path, code.encode("utf-8")))
        except Exception as exc:
            yield {
                "type": "console", "format": "error",
                "content": f"Kernel exec failed: {exc}",
            }
            return

        command = (
            f"python3 {OI_KERNEL_CLIENT_PATH} --run-stream-file {tmp_path} "
            f"--kernel-id {shlex.quote(kernel_id)} --run-id {shlex.quote(run_id)}"
        )

        async def _produce() -> None:
            cancellation_monitor = None
            try:
                handle = await self._sandbox.shell_stream(
                    command,
                    timeout=self._exec_timeout(code),
                )

                async def _monitor_cancellation() -> None:
                    while True:
                        if cancelled is not None and cancelled():
                            # Signal the already-running guest bridge. Opening
                            # another sandbox.shell() here can block behind a
                            # cold VM/client startup and make Stop ineffective.
                            await handle.signal(signal.SIGINT)
                            return
                        await asyncio.sleep(0.05)

                if cancelled is not None:
                    cancellation_monitor = asyncio.create_task(
                        _monitor_cancellation()
                    )
                async for event in handle:
                    event_type = str(getattr(event, "event_type", "")).lower()
                    if "stdout" in event_type:
                        events.put(("stdout", bytes(getattr(event, "data", b"") or b"")))
                    elif "stderr" in event_type:
                        events.put(("stderr", bytes(getattr(event, "data", b"") or b"")))
                    elif "exit" in event_type:
                        events.put(("exit", int(getattr(event, "code", 0) or 0)))
            except Exception as exc:
                events.put(("error", exc))
            finally:
                if cancellation_monitor is not None:
                    cancellation_monitor.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await cancellation_monitor
                events.put(("done", sentinel))

        future = asyncio.run_coroutine_threadsafe(_produce(), self._loop)
        stdout_buffer = b""
        saw_error_chunk = False
        completed = False
        try:
            while True:
                try:
                    kind, value = events.get(timeout=0.1)
                except queue.Empty:
                    continue
                if kind == "stdout":
                    stdout_buffer += value
                    while b"\n" in stdout_buffer:
                        raw_line, stdout_buffer = stdout_buffer.split(b"\n", 1)
                        chunk = self._decode_kernel_stream_line(raw_line)
                        if chunk is not None:
                            saw_error_chunk = saw_error_chunk or chunk.get("format") == "error"
                            yield chunk
                elif kind == "stderr":
                    stderr_parts.append(value)
                elif kind == "exit":
                    exit_code = value
                elif kind == "error":
                    saw_error_chunk = True
                    yield {
                        "type": "console", "format": "error",
                        "content": f"Kernel exec failed: {value}",
                    }
                elif kind == "done":
                    completed = True
                    break

            if stdout_buffer.strip():
                chunk = self._decode_kernel_stream_line(stdout_buffer)
                if chunk is not None:
                    saw_error_chunk = saw_error_chunk or chunk.get("format") == "error"
                    yield chunk
            if exit_code and stderr_parts and not saw_error_chunk:
                yield {
                    "type": "console", "format": "error",
                    "content": b"".join(stderr_parts).decode("utf-8", errors="replace").strip(),
                }
        finally:
            if not completed and not future.done():
                self.interrupt_python(kernel_id)
                future.cancel()
            try:
                self._exec(lambda: self._sandbox.shell(f"rm -f {tmp_path}"))
            except Exception:
                pass

    @staticmethod
    def _decode_kernel_stream_line(raw_line: bytes):
        if not raw_line.strip():
            return None
        try:
            envelope = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {
                "type": "console", "format": "error",
                "content": raw_line.decode("utf-8", errors="replace"),
            }
        if envelope.get("event") == "chunk" and isinstance(envelope.get("chunk"), dict):
            return envelope["chunk"]
        if envelope.get("event") == "error":
            return {
                "type": "console", "format": "error",
                "content": str(envelope.get("error") or "Unknown kernel stream error"),
            }
        return None

    def interrupt_python(self, kernel_id: str) -> bool:
        """Send a Jupyter interrupt through the in-VM daemon."""
        try:
            output = self._exec(
                lambda: self._sandbox.shell(
                    f"python3 {OI_KERNEL_CLIENT_PATH} --interrupt "
                    f"--kernel-id {shlex.quote(kernel_id)}"
                ),
                timeout=30.0,
            )
            payload = json.loads((output.stdout_text or "").strip())
            return bool(payload.get("interrupted"))
        except Exception:
            return False

    def run_codex(self, request: dict) -> dict:
        """Run the guest Codex SDK bridge using a credential-safe request file."""
        request_path = f"/tmp/.idea_codex_request_{uuid.uuid4().hex}.json"
        run_id = str(request.get("run_id", ""))
        cancel_path = (
            f"/tmp/.idea_codex_cancel_{self._safe_run_id(run_id)}" if run_id else ""
        )
        try:
            data = json.dumps(request).encode("utf-8")
            self._exec(lambda: self._sandbox.fs.write(request_path, data))
            self._exec(
                lambda: self._sandbox.shell(
                    f"chmod 600 -- {shlex.quote(request_path)}"
                )
            )
            output = self._exec(
                lambda: self._sandbox.shell(
                    f"python3 {shlex.quote(CODEX_RUNNER_PATH)} "
                    f"--request-file {shlex.quote(request_path)}"
                ),
                timeout=self._exec_timeout(str(request.get("task", ""))),
            )
            text = (output.stdout_text or "").strip()
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                stderr = getattr(output, "stderr_text", None) or ""
                return {
                    "ok": False,
                    "status": "failed",
                    "error": text or stderr or "Codex runner returned no JSON",
                    "events": [],
                }
        except Exception as exc:
            return {"ok": False, "status": "failed", "error": str(exc), "events": []}
        finally:
            targets = " ".join(shlex.quote(path) for path in (request_path, cancel_path) if path)
            try:
                self._exec(lambda: self._sandbox.shell(f"rm -f -- {targets}"))
            except Exception:
                pass

    @staticmethod
    def _safe_run_id(run_id: str) -> str:
        return "".join(char if char.isalnum() or char in "_.-" else "_" for char in run_id)[:160]

    def interrupt_codex(self, run_id: str) -> bool:
        """Signal the guest runner's cancellation watcher without taking its lock."""
        if not run_id:
            return False
        path = f"/tmp/.idea_codex_cancel_{self._safe_run_id(run_id)}"
        try:
            self._exec(lambda: self._sandbox.fs.write(path, b"cancel\n"), timeout=10.0)
            return True
        except Exception:
            return False

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
        # Microsandbox imports OCI entrypoint/CMD metadata but does not run
        # the image's primary process when a detached sandbox is created or
        # resumed. Start Open Terminal lazily (and restart it after an idle
        # stop/resume) before trying to read the key generated for it.
        self._ensure_open_terminal()
        if self._open_terminal_key:
            return self._open_terminal_key

        last_error: Optional[Exception] = None
        for _ in range(retries):
            try:
                data = self._exec(lambda: self._sandbox.fs.read(OPEN_TERMINAL_KEY_PATH))
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

    def _ensure_open_terminal(self) -> None:
        """Start the in-VM Open Terminal server if it is not already healthy."""
        key_path = shlex.quote(OPEN_TERMINAL_KEY_PATH)
        health_url = shlex.quote(
            f"http://127.0.0.1:{OPEN_TERMINAL_PORT}/health"
        )
        command = (
            f"if ! curl -fsS --max-time 2 {health_url} >/dev/null 2>&1; then "
            f"KEY_PATH={key_path}; "
            'if [ ! -s "$KEY_PATH" ]; then '
            'head -c 32 /dev/urandom | od -An -tx1 | tr -d \' \\n\' > "$KEY_PATH"; '
            "fi; "
            'chmod 600 "$KEY_PATH"; '
            'nohup env OPEN_TERMINAL_API_KEY="$(cat "$KEY_PATH")" '
            "/app/entrypoint-slim.sh run "
            ">/tmp/idea-open-terminal.log 2>&1 </dev/null & "
            "fi; "
            f"for attempt in $(seq 1 20); do "
            f"curl -fsS --max-time 2 {health_url} >/dev/null 2>&1 && exit 0; "
            "sleep 0.5; done; exit 1"
        )
        output = self._exec(
            lambda: self._sandbox.shell(command),
            timeout=20.0,
        )
        if output.exit_code != 0:
            stderr = getattr(output, "stderr_text", None) or ""
            raise RuntimeError(
                f"Open Terminal failed to start in sandbox {self.session_id}: "
                f"{stderr or 'health check timed out'}"
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
        output = self._exec(lambda: self._sandbox.shell(cmd), timeout=60.0)
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

    def _get_cwd(self) -> str:
        """
        Absolute working directory `shell()`/`run_python()` commands run
        from inside this VM - baked into the guest image's `WORKDIR` (e.g.
        `/opt/oi_kernel` for the oi-kernel image; `/` for the bare `python`
        image). Cached after the first lookup since it's stable for the
        VM's lifetime.
        """
        if self._cwd is None:
            output = self._exec(lambda: self._sandbox.shell("pwd"))
            self._cwd = (output.stdout_text or "/").strip() or "/"
        return self._cwd

    def _resolve_path(self, filepath: str) -> str:
        """
        Joins a relative filepath against this VM's shell working directory
        (see `_get_cwd()`). `fs.write()`/`fs.read()`/`fs.exists()` below are
        a separate microsandbox API that resolves relative paths against the
        VM's filesystem root by default, regardless of the image's
        `WORKDIR` - without this, a relative path written via write_file()
        and the same relative path referenced from a `shell()`/
        `run_python()` command (e.g. `python3 script.py`) would silently
        resolve to two different files whenever the image's `WORKDIR` isn't
        `/` (e.g. writing to `/script.py` but shell looking in
        `/opt/oi_kernel/script.py`).
        """
        if filepath.startswith("/"):
            return filepath
        return f"{self._get_cwd().rstrip('/')}/{filepath}"

    def write_file(self, filepath: str, content: str, append: bool = False) -> None:
        """Write content to a file inside the sandbox's filesystem."""
        filepath = self._resolve_path(filepath)
        parent = os.path.dirname(filepath)
        if parent:
            quoted_parent = shlex.quote(parent)
            self._exec(
                lambda: self._sandbox.shell(f"mkdir -p -- {quoted_parent}")
            )
        data = content.encode("utf-8")
        if append:
            # No native append API - emulate via shell so it stays atomic
            # inside the sandbox rather than round-tripping bytes twice.
            escaped = content.replace("'", "'\\''")
            quoted_filepath = shlex.quote(filepath)
            self._exec(
                lambda: self._sandbox.shell(
                    f"printf '%s' '{escaped}' >> {quoted_filepath}"
                )
            )
        else:
            self._exec(lambda: self._sandbox.fs.write(filepath, data))

    def write_file_bytes(self, filepath: str, source) -> None:
        """Atomically stream a binary file into the sandbox filesystem."""
        filepath = self._resolve_path(filepath)
        parent = os.path.dirname(filepath)
        if parent:
            quoted_parent = shlex.quote(parent)
            self._exec(
                lambda: self._sandbox.shell(f"mkdir -p -- {quoted_parent}")
            )

        temporary_path = f"{filepath}.idea-upload-{uuid.uuid4().hex}"
        sink = self._exec(
            lambda: self._sandbox.fs.write_stream(temporary_path)
        )
        try:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                self._exec(lambda chunk=chunk: sink.write(chunk))
            self._exec(lambda: sink.close())
            self._exec(
                lambda: self._sandbox.fs.rename(temporary_path, filepath)
            )
        except Exception:
            try:
                self._exec(lambda: sink.close())
            except Exception:
                pass
            try:
                self._exec(
                    lambda: self._sandbox.fs.remove(temporary_path)
                )
            except Exception:
                pass
            raise

    def read_file(self, filepath: str) -> bytes:
        """Read raw bytes of a file from inside the sandbox (e.g. for image display)."""
        return self._exec(lambda: self._sandbox.fs.read(self._resolve_path(filepath)))

    def file_exists(self, filepath: str) -> bool:
        """Check whether filepath exists inside the sandbox."""
        try:
            return self._exec(lambda: self._sandbox.fs.exists(self._resolve_path(filepath)))
        except Exception:
            return False

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
                self._run(self._sandbox.stop)
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
                self._run(self._sandbox.kill)
            self._run(lambda: Sandbox.remove(self.session_id))
        except Exception:
            pass
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
