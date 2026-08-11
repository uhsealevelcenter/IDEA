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


def run(code_path: str, kernel_id: str = "default", run_id: str = "") -> dict:
    with open(code_path, "r", encoding="utf-8") as f:
        code = f.read()
    return _post("/run", {
        "language": "python", "code": code,
        "kernel_id": kernel_id, "run_id": run_id,
    })


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--ensure-daemon":
        ensure_daemon()
        print(json.dumps({"ok": True}))
    elif "--run-file" in sys.argv:
        code_path = sys.argv[sys.argv.index("--run-file") + 1]
        kernel_id = (
            sys.argv[sys.argv.index("--kernel-id") + 1]
            if "--kernel-id" in sys.argv else "default"
        )
        run_id = (
            sys.argv[sys.argv.index("--run-id") + 1]
            if "--run-id" in sys.argv else ""
        )
        print(json.dumps(run(code_path, kernel_id, run_id)))
    elif "--interrupt" in sys.argv:
        kernel_id = (
            sys.argv[sys.argv.index("--kernel-id") + 1]
            if "--kernel-id" in sys.argv else "default"
        )
        print(json.dumps(_post("/interrupt", {"kernel_id": kernel_id})))
    else:
        print(json.dumps({"error": "usage: client.py --ensure-daemon | --run-file <path>"}))
        sys.exit(1)
