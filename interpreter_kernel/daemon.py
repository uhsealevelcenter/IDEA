"""
OI Kernel Daemon - runs *inside* a per-user microsandbox microVM (see
sandbox_service/msb_sandbox.py), not as a docker-compose service of its own.

Gives that VM a persistent, stateful Python kernel by reusing Open
Interpreter's execution engine (interpreter/core/computer/terminal/
languages/python.py -> jupyter_language.py, a real jupyter_client-backed
IPython kernel), vendored unmodified under ./vendor. Only the execution
engine is imported here - never interpreter/core/core.py or respond.py
(Open Interpreter's own chat/agent loop), which stay out of this image on
purpose: LangGraph's ConversationOrchestrator/TerminalAgent is the only
thing deciding what code to run. This daemon only runs it and reports back
what happened, in the same Open Interpreter chunk format
({"type": "console"/"image", "format": ..., "content": ...}) the rest of
the stack already speaks end-to-end.

Deliberately dependency-light: only the two leaf language runners actually
needed (Python's Jupyter-backed one, and Shell) are imported - not
terminal.py's own dispatcher, which unconditionally imports the HTML/React
language runners and, through them, a chain that reaches into
interpreter.terminal_interface (not vendored here) and the optional
`html2image` package. Importing only the specific submodules below avoids
ever triggering those imports, even though the wider `interpreter/core/
computer` tree is copied into the image as-is (see Dockerfile) - Python
only executes the modules an `import` statement actually names.

Binds to 127.0.0.1 only: this process is never reachable from outside the
VM. sandbox_service talks to it indirectly, by using the microsandbox SDK's
own `sandbox.shell()` to run client.py *inside* the VM - see
MicrosandboxTerminal.run_python in sandbox_service/msb_sandbox.py. There is
intentionally no network path into this daemon from the host or from other
users' VMs.
"""

import http.server
import json
import os
import socketserver
import sys
import threading
from pathlib import Path

# Disables jupyter_language.py's "ask an LLM whether this looks like it
# wants stdin" nudge (see JupyterLanguage._execute_code's iopub_message_
# listener). The execution engine must never make its own LLM calls - only
# LangGraph's TerminalAgent decides what runs - so this is set high enough
# that branch never fires rather than trying to stub out litellm.
os.environ.setdefault("INTERPRETER_TERMINAL_INPUT_PATIENCE", "999999999")

sys.path.insert(0, str(Path(__file__).parent / "vendor"))

from interpreter.core.computer.terminal.languages.python import Python as PythonLanguage  # noqa: E402
from interpreter.core.computer.terminal.languages.shell import Shell as ShellLanguage  # noqa: E402

HOST = "127.0.0.1"
PORT = int(os.environ.get("OI_KERNEL_PORT", "8721"))


class _StubLLM:
    model = "none"
    api_key = None


class _StubInterpreter:
    """
    Minimal duck-typed stand-in for a real Open Interpreter `Interpreter`
    instance - enough to satisfy what JupyterLanguage actually reads
    (`.stop_event` via hasattr, `.messages`, `.llm.model`/`.llm.api_key` on
    its now-disabled input-patience path). Never imports or constructs a
    real Interpreter/OpenInterpreter - that class is Open Interpreter's own
    agent loop and is intentionally not part of this image at all.
    """

    def __init__(self):
        self.messages = []
        self.llm = _StubLLM()
        self.stop_event = threading.Event()


class _StubComputer:
    """
    Minimal duck-typed stand-in for Open Interpreter's `Computer` -
    JupyterLanguage only reads `.interpreter` off of it (via
    `self.computer.interpreter...`). Deliberately not the real `Computer`
    class (interpreter/core/computer/computer.py): that class eagerly
    constructs Mouse/Browser/Skills/etc. subsystems at __init__ time, none
    of which apply to a headless data-analysis kernel, and some of which
    (Browser) hard-import `selenium`/`webdriver_manager`, or (Skills) reach
    into `interpreter.terminal_interface`, which isn't vendored here.
    """

    def __init__(self):
        self.interpreter = _StubInterpreter()


# One language runner instance per language, created lazily and reused for
# the lifetime of this daemon process - this is what gives the VM real
# persistent-kernel semantics (variables/imports survive across /run calls)
# instead of a fresh interpreter per call.
_languages: dict = {}
_languages_lock = threading.Lock()

# sandbox_service already serializes calls into a given VM via its own
# per-sandbox_id lock (terminal_registry.py), so this is defense-in-depth,
# not the primary guarantee: a JupyterLanguage instance's run()/output
# capture is not safe to call concurrently from two requests at once.
_exec_locks: dict[str, threading.Lock] = {}

_LANGUAGE_CLASSES = {
    "python": PythonLanguage,
    "shell": ShellLanguage,
}


def _get_language(name: str, kernel_id: str = "default"):
    key = (name, kernel_id)
    with _languages_lock:
        if key not in _languages:
            lang_class = _LANGUAGE_CLASSES[name]
            # Mirrors terminal.py's own dynamic check: only Jupyter-backed
            # Python needs a `computer` reference; Shell's __init__ takes
            # none.
            if lang_class.__init__.__code__.co_argcount > 1:
                _languages[key] = lang_class(_StubComputer())
            else:
                _languages[key] = lang_class()
            _exec_locks.setdefault(kernel_id, threading.Lock())
        return _languages[key]


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep stdout clean - sandbox_service only reads /run's JSON body

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path not in {"/run", "/interrupt"}:
            self._json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid JSON body"})
            return

        kernel_id = str(body.get("kernel_id") or "default")
        if not kernel_id.replace("_", "").isalnum() or len(kernel_id) > 96:
            self._json(400, {"error": "invalid kernel_id"})
            return
        if self.path == "/interrupt":
            with _languages_lock:
                runner = _languages.get(("python", kernel_id))
            if runner is None:
                self._json(200, {"interrupted": False})
                return
            runner.interrupt_and_drain()
            self._json(200, {"interrupted": True})
            return

        language = body.get("language", "python")
        code = body.get("code", "")

        if language not in _LANGUAGE_CLASSES:
            self._json(400, {"error": f"unsupported language: {language}"})
            return

        chunks = []
        runner = _get_language(language, kernel_id)
        with _exec_locks[kernel_id]:
            try:
                for chunk in runner.run(code):
                    if chunk.get("format") != "active_line":
                        chunks.append(chunk)
            except Exception as e:
                chunks.append({"type": "console", "format": "output", "content": f"Kernel error: {e}"})

        self._json(200, {"chunks": chunks})

    def _json(self, status: int, payload: dict):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class _ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main():
    # Warm the Python kernel at boot so the first real /run call isn't
    # slowed down by ipykernel startup.
    _get_language("python", "default")
    with _ThreadingServer((HOST, PORT), Handler) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
