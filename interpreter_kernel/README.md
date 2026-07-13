# OI Kernel + Open Terminal (guest image for per-user microVMs)

This is the OCI image `sandbox_service/msb_sandbox.py` boots per-user
microsandbox microVMs *from* - not a `docker-compose.yml` service of its
own. It combines two things inside one VM:

1. **[`open-webui/open-terminal`](https://github.com/open-webui/open-terminal)**
   (unmodified, pulled as the base image) - gives `grep_search_tool`/
   `glob_search_tool` a maintained implementation instead of hand-rolled
   `grep`/`find` shelling.
2. **This repo's own persistent Python kernel** (`daemon.py`), reusing Open
   Interpreter's *execution engine only*
   (`../interpreter/core/computer/terminal/languages/python.py`, a real
   `jupyter_client`-backed IPython kernel) - gives `run_python_tool` real
   stateful-kernel semantics: variables, imports, and matplotlib figures
   created in one turn are still there on the next. Open Terminal's own
   `/execute` endpoint is a fresh background process per call, not a
   kernel, so it doesn't provide this on its own - see "Why not just use
   Open Terminal's `/execute`?" below.

**Neither of these makes LangGraph's `ConversationOrchestrator`/
`TerminalAgent` optional.** Open Terminal's own OpenWebUI-native
integration (its "Native tool-calling" mode, where OpenWebUI's backend
becomes the agent loop) and its own multi-user isolation modes (per-Linux-
account or its "Terminals" companion project, both weaker than a dedicated
microVM) are **not used at all** here. LangGraph stays the only thing
deciding what runs; microsandbox (one VM per user) stays the only isolation
boundary. Open Terminal is subordinate to both - just a better-maintained
execution surface *inside* that boundary. See the architecture doc this was
built from for the full reasoning.

## How it's used

Inside a running VM, three processes matter (see `entrypoint.sh`):

- **Open Terminal**, listening on `127.0.0.1:8000` - started via its own
  unmodified `entrypoint-slim.sh` (home dir setup, optional egress
  firewall, privilege drop to a non-root user).
- **The kernel daemon** (`daemon.py`), listening on `127.0.0.1:8721`.
- Both bind to loopback only - **neither is reachable from outside the
  VM**. `sandbox_service` never talks to either directly over a network
  path; it uses the microsandbox SDK's own `sandbox.shell()` (already
  confirmed to work for plain command exec) to run `curl` (for Open
  Terminal) or `client.py` (for the kernel daemon) *inside* the VM.

Open Terminal requires an API key on every request. `entrypoint.sh`
generates one per VM at boot and writes it to a fixed path
(`/opt/oi_kernel/.open_terminal_api_key`); `sandbox_service` reads it once
via the SDK's `fs.read()` (the same channel already used for file I/O, not
a network request) and caches it for that VM's lifetime - see
`msb_sandbox.py`'s `_get_open_terminal_key()`.

See `sandbox_service/msb_sandbox.py` (`run_python()`, `grep_search()`,
`glob_search()`) for the host-side half of this, and
`langgraph/tools/persistent_terminal.py` /
`langgraph/agents/terminal_agent.py`'s `run_python_tool`/
`grep_search_tool`/`glob_search_tool` for how the agent calls it.
`run_terminal_tool`/`write_file_tool` are unchanged - they still go through
the microsandbox SDK's own `shell()`/`fs.write()`/`fs.read()` directly,
not through Open Terminal, since that already works and Open Terminal's
`/execute` doesn't offer anything more for plain shell commands.

## Building

```bash
# From the repo root (needs interpreter/core/computer/ in the build context):
docker build -f interpreter_kernel/Dockerfile -t idea/oi-kernel:slim .
msb pull idea/oi-kernel:slim   # or push to a registry microsandbox can pull from
```

Set `SANDBOX_IMAGE=idea/oi-kernel:slim` on the `sandbox` service
(`docker-compose.yml`) to have new sandboxes boot from this image instead
of the bare `python` image. `run_terminal_tool`/`write_file_tool` work
against any image; `run_python_tool`/`grep_search_tool`/`glob_search_tool`
specifically require this one (or one built on top of it).

## Why not just use Open Terminal's `/execute` for everything?

Its `/execute` endpoint runs a shell command as a new background process
per call (polled via `/execute/{id}/status`) - closer to what
`run_terminal_tool` already does via `sandbox.shell()` than to a stateful
kernel. It has no equivalent to a Jupyter kernel's `execute_result`/
`display_data` messaging (which is how `daemon.py` gets automatic
matplotlib figure capture and true variable persistence). The closest
Open Terminal gets is its interactive PTY terminal sessions
(`/api/terminals`, WebSocket) - you could keep a `python3` REPL alive and
pipe code into its stdin, but that's fragile text-scraping, not structured
messaging, and doesn't fit LangGraph's clean tool-call/tool-result loop.
So: Open Terminal for shell/grep/glob (things it's genuinely better at),
this repo's own kernel daemon for stateful Python (something Open Terminal
doesn't provide at all).

## Why not just import Open Interpreter's `Computer` class for the kernel?

`Computer.__init__` (`interpreter/core/computer/computer.py`) eagerly
constructs subsystems irrelevant to a headless data-analysis kernel
(Mouse, Keyboard, Browser, Mail, SMS, Calendar, Contacts, Skills, ...).
Some have hard, non-lazy dependencies this image avoids pulling in - e.g.
`Browser` imports `selenium`/`webdriver_manager` at module load time, and
`Skills` reaches into `interpreter.terminal_interface`, a package not
vendored here. `daemon.py` instead imports only the two leaf language
runners it actually needs (`terminal/languages/python.py`,
`terminal/languages/shell.py`) directly, bypassing `Computer` and
`terminal/terminal.py`'s own dispatcher (which would pull in the
HTML/React runners and, through them, the same `html2image`/
`terminal_interface` chain). See `daemon.py`'s module docstring for the
full explanation.

## If `ensurepip` ever stops working

The Dockerfile installs the kernel daemon's Python dependencies
(`jupyter_client`, `ipykernel`, `matplotlib`) on top of Open Terminal's
published `:slim` runtime image, which has `pip` itself removed to save
space (see `open-webui/open-terminal`'s own `Dockerfile.slim`) - restoring
it via `python3 -m ensurepip` (a stdlib mechanism, independent of that
removal) is what makes this work. If a future Open Terminal release also
strips `ensurepip`'s bundled wheels, this approach breaks. The fallback is
to build from Open Terminal's own `Dockerfile.slim` source instead of
layering on its published image: add this image's packages to *its*
builder stage's `pip install` line (its own documented customization
point - "Want extra pip packages? Add them to the pip install line in the
builder"), and add this directory's `vendor/`, `daemon.py`, `client.py`,
and a merged entrypoint into its runtime stage. That requires vendoring
Open Terminal's own source into this repo (or referencing it as a build
context), which the current approach deliberately avoids.
