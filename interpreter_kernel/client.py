"""
OI Kernel Client - runs *inside* a per-user microVM, invoked via the
microsandbox SDK's `sandbox.shell()` (see sandbox_service/msb_sandbox.py:
MicrosandboxTerminal.run_python). Talks to daemon.py over loopback only.

sandbox_service never talks to the daemon directly over a network path -
it shells into the VM to run this script, which then does a plain HTTP
call to 127.0.0.1 *inside that same VM*. This keeps the daemon unreachable
from outside the VM boundary without needing microsandbox to expose any
port, and reuses the exact channel (`sandbox.shell()`) already confirmed
to work for the existing shell-command execution path.
"""

import http.client
import json
import os
import signal
import subprocess
import sys
import time

HOST = "127.0.0.1"
PORT = int(os.environ.get("OI_KERNEL_PORT", "8721"))
DAEMON_SCRIPT = os.environ.get("OI_KERNEL_DAEMON", "/opt/oi_kernel/daemon.py")


def _is_up() -> bool:
    conn = None
    try:
        conn = http.client.HTTPConnection(HOST, PORT, timeout=1)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        resp.read()
        return resp.status == 200
    except Exception:
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def ensure_daemon(timeout: float = 60.0) -> None:
    if _is_up():
        return
    # Idempotent: if another concurrent client already started it, the
    # health check above (or the poll loop below) just observes it coming up.
    with open(os.devnull, "wb") as devnull:
        subprocess.Popen(
            [sys.executable, DAEMON_SCRIPT],
            stdout=devnull,
            stderr=devnull,
            stdin=devnull,
            start_new_session=True,
        )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_up():
            return
        time.sleep(0.5)
    raise RuntimeError("OI kernel daemon did not become healthy in time")


def _post(path: str, body: dict) -> dict:
    ensure_daemon()
    encoded = json.dumps(body).encode("utf-8")
    conn = http.client.HTTPConnection(HOST, PORT, timeout=None)
    try:
        conn.request("POST", path, body=encoded, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        return json.loads(resp.read())
    finally:
        conn.close()


def _interrupt_daemon(kernel_id: str) -> bool:
    """Interrupt an already-started daemon without recursively starting it."""
    conn = http.client.HTTPConnection(HOST, PORT, timeout=5)
    try:
        encoded = json.dumps({"kernel_id": kernel_id}).encode("utf-8")
        conn.request(
            "POST", "/interrupt", body=encoded,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read())
        return bool(payload.get("interrupted"))
    except Exception:
        return False
    finally:
        conn.close()


def run(code_path: str, kernel_id: str = "default", run_id: str = "") -> dict:
    with open(code_path, "r", encoding="utf-8") as f:
        code = f.read()
    return _post("/run", {
        "language": "python", "code": code,
        "kernel_id": kernel_id, "run_id": run_id,
    })


def run_stream(code_path: str, kernel_id: str = "default", run_id: str = "") -> None:
    """Forward the daemon's NDJSON stream to stdout without buffering it."""
    with open(code_path, "r", encoding="utf-8") as f:
        code = f.read()
    cancel_requested = False
    request_started = False

    def _handle_interrupt(_signum, _frame) -> None:
        nonlocal cancel_requested
        cancel_requested = True
        # Once the HTTP run has been submitted, forward SIGINT to the
        # persistent kernel instead of terminating this bridge process.
        if request_started:
            _interrupt_daemon(kernel_id)

    previous_sigint = signal.signal(signal.SIGINT, _handle_interrupt)
    encoded = json.dumps({
        "language": "python", "code": code,
        "kernel_id": kernel_id, "run_id": run_id,
    }).encode("utf-8")
    conn = http.client.HTTPConnection(HOST, PORT, timeout=None)
    try:
        ensure_daemon()
        if cancel_requested:
            return
        request_started = True
        conn.request(
            "POST", "/run-stream", body=encoded,
            headers={"Content-Type": "application/json"},
        )
        # Covers SIGINT arriving after request_started was set but before the
        # daemon had registered the runner. The first forwarding attempt may
        # correctly have returned False in that narrow interval.
        if cancel_requested:
            _interrupt_daemon(kernel_id)
        resp = conn.getresponse()
        if resp.status != 200:
            raise RuntimeError(f"OI kernel daemon returned HTTP {resp.status}: {resp.read()!r}")
        while True:
            line = resp.readline()
            if not line:
                break
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
    finally:
        conn.close()
        signal.signal(signal.SIGINT, previous_sigint)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--ensure-daemon":
        ensure_daemon()
        print(json.dumps({"ok": True}))
    elif "--run-file" in sys.argv or "--run-stream-file" in sys.argv:
        option = "--run-stream-file" if "--run-stream-file" in sys.argv else "--run-file"
        code_path = sys.argv[sys.argv.index(option) + 1]
        kernel_id = (
            sys.argv[sys.argv.index("--kernel-id") + 1]
            if "--kernel-id" in sys.argv else "default"
        )
        run_id = (
            sys.argv[sys.argv.index("--run-id") + 1]
            if "--run-id" in sys.argv else ""
        )
        if option == "--run-stream-file":
            run_stream(code_path, kernel_id, run_id)
        else:
            print(json.dumps(run(code_path, kernel_id, run_id)))
    elif "--interrupt" in sys.argv:
        kernel_id = (
            sys.argv[sys.argv.index("--kernel-id") + 1]
            if "--kernel-id" in sys.argv else "default"
        )
        print(json.dumps(_post("/interrupt", {"kernel_id": kernel_id})))
    else:
        print(json.dumps({"error": "usage: client.py --ensure-daemon | --run-file <path> | --run-stream-file <path>"}))
        sys.exit(1)
