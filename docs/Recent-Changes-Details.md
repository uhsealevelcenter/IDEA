# Recent Changes: Development Details

## Scope and reading guidance

This document expands the `feature/langgraph-memory` section of
[`Recent-Changes.md`](Recent-Changes.md). It compares the current branch with
its `next-dev` merge base, commit `e6dc507`, and describes the resulting code
as a whole rather than treating each intermediate commit as an independent
feature. That distinction matters because several later commits revised or
replaced behavior introduced earlier in the range.

At the time this document was prepared, the range was `e6dc507..d4ccac3` (23
commits, 74 changed paths, approximately 8,200 additions and 3,200 deletions).
The most consequential change is that the normal Open WebUI `/chat-runs` path
is now a checkpointed LangGraph workflow. The former linear
`ConversationOrchestrator` remains available as a rollback path and behind the
legacy `/chat` endpoint, but it is no longer the primary runtime when Compose
uses its default configuration.

## End-to-end architecture

The request path now has four distinct owners:

1. Open WebUI owns the visible conversation, branch selection, compaction,
   Assistant selection, uploads, and published result files.
2. `openwebui/functions/idea_pipe.py` converts the selected Open WebUI branch
   into a `/chat-runs` request and converts persisted IDEA events back into
   Open WebUI text, status, image, file, and message-metadata events.
3. `langgraph/langgraph_service.py` owns run coordination and invokes the
   checkpointed graph in `langgraph/idea_graph/`.
4. `sandbox_service/` owns terminal and microVM objects. Shell, file, Python,
   Open Terminal, and Codex operations cross its authenticated HTTP boundary;
   user code does not execute in the LangGraph container.

Storage responsibilities are intentionally separate:

- Open WebUI's PostgreSQL data is the authoritative conversation record.
- The `idea_langgraph` PostgreSQL schema stores bounded LangGraph checkpoints;
- Redis stores transient run status/events and mappings from Open WebUI
  assistant-message IDs to LangGraph checkpoints; and
- each microsandbox VM stores the user's workspace, kernel state, generated
  files, and Codex state.

This replaces the earlier design in which an in-process linear agent loop and
a Redis transcript could compete with Open WebUI for conversational state.

## Checkpointed graph and durable execution state

### Graph topology

`langgraph/idea_graph/graph.py` adds `build_idea_graph()`, which compiles an
explicit `StateGraph` with these nodes:

- `prepare_turn()` resets transient per-turn fields, recognizes a narrowly
  phrased continuation request, prepares the current objective/plan, restores
  bounded ledgers, and optionally selects the active image for a visual
  follow-up.
- `call_model()` assembles model context, invokes the streaming model runtime,
  records usage, normalizes tool-call IDs, and enforces the model-iteration
  safety limit.
- `execute_one_tool()` executes one pending call at a time. This creates a
  checkpoint/cancellation boundary between tools and records safe arguments,
  hashes, timestamps, outcomes, Python metadata, artifacts, vision inputs, and
  Codex thread/usage information.
- `cancellation_gate()` observes cooperative Stop state after each tool.
- `stopped_summary()` produces a user-facing summary of completed, interrupted,
  or uncertain work and preserves a continuation record when the iteration
  limit was reached.
- `finalize()` synchronizes outputs for a successful response, while
  `finalize_stopped()` performs the same output-finalization pass after Stop.

Routing functions `route_after_prepare()`, `route_after_model()`, and
`route_after_tool()` decide whether to call the model, execute another tool,
finalize, or stop. `execute_one_tool` always passes through the cancellation
gate. `build_idea_graph()` is compiled with the configured checkpointer and a
structural recursion limit high enough that IDEA's own 20-model-iteration
limit is the effective safety boundary.

### State schema and memory bounds

`langgraph/idea_graph/state.py` defines the serialized `IDEAState` and the
`ConversationMessage`, `PythonExecutionRecord`, `ActionRecord`,
`ArtifactRecord`, and `DatasetRecord` shapes. Persisted fields now include:

- the current objective, minimal plan, continuation, iteration, and final
  status;
- bounded action and Python-execution ledgers;
- dataset and artifact references, including the active visual artifact;
- warnings, pending calls, and run/thread/workspace/kernel identities;
- model-usage records and pending model-vision paths; and
- Codex thread IDs keyed by working directory plus bounded Codex usage.

`langgraph/idea_graph/memory.py` contains the data-reduction rules:

- `safe_arguments()` recursively redacts keys containing credential terms and
  replaces Python source in generic action arguments with a SHA-256 digest.
- `defined_names()` parses submitted Python and records assigned variables,
  functions, and classes.
- `bounded_excerpt()`, `bounded_text_bytes()`, and `bounded_records()` enforce
  UTF-8 byte and record-count limits.
- `execution_memory_block()` gives the model a newest-first summary of prior
  actions, archived source paths, object types/shapes/lengths, output paths,
  and console excerpts without persisting arbitrary object values.
- `compact_turn_messages()` retains the most recent tool observations and
  truncates older observations before another model call.

The graph divides `IDEA_MAX_STATE_BYTES` between action and Python ledgers,
keeps only `IDEA_MAX_RECENT_ACTIONS` and `IDEA_MAX_RECENT_EXECUTIONS`, and
limits execution-memory/model-observation/history excerpts through the other
`IDEA_MAX_*` settings in `langgraph/idea_config.py` and `example.env`.
Completed actions retain an arguments hash; `execute_one_tool()` blocks a
fourth identical call with the default `IDEA_MAX_IDENTICAL_TOOL_CALLS=3`.
This prevents common tool loops while still leaving the earlier result in the
ledger.

Exact Python source is separately archived at
`/workspace/.idea/threads/<thread>/executions/<execution-id>.py` by
`TerminalGraphRuntime.persist_python_source()`. The checkpoint normally holds
only bounded source plus its digest and path.

### Checkpoint storage

`langgraph/idea_graph/checkpoints.py` implements process-wide checkpointer
lifecycle:

- `get_checkpointer()` uses `PostgresSaver` when
  `LANGGRAPH_DATABASE_URL` is set and `InMemorySaver` for local/test use.
- `_serializer()` enables `EncryptedSerializer` when `LANGGRAPH_AES_KEY` is a
  valid raw 16-, 24-, or 32-byte AES key; an invalid length fails closed.
- `setup_checkpointer()` and `close_checkpointer()` manage schema setup and
  connection-context shutdown.

`langgraph/db/init_langgraph_db.sql` defines a dedicated `idea_langgraph` role
and schema. `langgraph/db/setup_langgraph_db.sh` validates required variables,
creates/updates the least-privilege role, applies the schema, and runs the
LangGraph saver setup. `docker-compose.yml` supplies a schema-scoped
`LANGGRAPH_DATABASE_URL` and makes LangGraph wait for the database health
check. `docs/Quick-Deploy.md` and the root `README.md` now require this setup
alongside the existing LiteLLM database initialization.

## Conversation authority, branches, and identities

### Open WebUI is authoritative

`openwebui/functions/idea_pipe.py` now forwards the complete selected Open
WebUI branch through `_structured_messages()` instead of sending only a new
prompt and asking LangGraph to restore a separate transcript. It preserves
user and assistant message IDs, separates Open WebUI's
`[CONVERSATION SUMMARY]` from Assistant policy, and forwards the summary as
conversation context rather than privileged instructions.

The Pipe also removes content that should remain visible in the UI but should
not be replayed to the model:

- `_sanitize_assistant_image_history()` removes legacy inline/base64 images;
- `_sanitize_assistant_tool_history()` removes marked IDEA tool-display
  blocks and recognizes legacy console blocks; and
- `_bounded_model_history_text()` retains a small prefix and the more useful
  final-answer suffix when an old assistant message is oversized.

The checkpoint action/Python ledgers are authoritative for prior tool work;
the rendered chat transcript is not used as a second tool-memory store.

### Branch-to-checkpoint mapping

`langgraph/langgraph_service.py` adds `_resolve_graph_checkpoint()` and the
mapping helpers `_load_message_checkpoint()`, `_store_message_checkpoint()`,
`_load_latest_checkpoint()`, and `_store_latest_checkpoint()`. After a run,
the response's Open WebUI message ID is mapped in Redis to its graph thread and
checkpoint. A later turn walks the visible assistant IDs newest-first and
resumes the checkpoint belonging to that visible branch.

The resolver handles several compatibility cases deliberately:

- explicit message metadata wins when a prior `idea_context` event supplies a
  checkpoint;
- a visible mapped assistant resumes its exact branch;
- an old ID-less history may use the latest durable checkpoint;
- a pre-mapping legacy chat receives a one-time linear continuation path; and
- a first turn or root-message regeneration receives a new branch thread and
  cannot inherit an abandoned latest response.

Mappings expire after `IDEA_CHECKPOINT_MAP_TTL_SECONDS` (one year by default)
and refresh when read. The Pipe stores the final `idea_context` event in Open
WebUI message metadata through its native `message_meta` event, preserving the
input/output checkpoint IDs, checkpoint source, active artifact, execution
references, and run-level model usage.

### Independent identities

`langgraph/idea_graph/identities.py` introduces
`derive_execution_identities()`. It uses an HMAC based on
`IDEA_IDENTITY_SECRET` (falling back to `INTERNAL_SERVICE_TOKEN`, and to a
development-only constant if neither is configured) to derive separate IDs:

- workspace: stable per authenticated user;
- graph thread: stable per user and Open WebUI chat, with branch suffixes when
  needed;
- kernel: per user, per user/chat, or per user/chat/Assistant according to
  `IDEA_KERNEL_SCOPE` (default `chat_assistant`); and
- run: a fresh response ID created by `/chat-runs`.

Anonymous identities and unsupported kernel scopes are rejected. This avoids
using one identifier for files, Python state, conversation history, and an
individual execution.

## Background runs, event recovery, progress, and Stop

### Persisted chat runs

The normal Pipe no longer holds one uninterrupted backend SSE request. It:

1. calls `POST /chat-runs`;
2. polls `GET /chat-runs/<run-id>/events?after=<sequence>`;
3. checks the persisted terminal run status; and
4. requests `POST /chat-runs/<run-id>/stop` if Open WebUI cancels or closes
   the generator.

`start_chat_run()` records a queued Redis status and starts `_run_chat_job()`
on a daemon thread. `_run_chat_job()` records running/terminal state, creates
the graph runtime, invokes the graph, persists its checkpoint mapping, and
emits the final `idea_context`. Run and event keys use
`CHAT_RUN_TTL_SECONDS` (24 hours by default).

`_append_chat_run_event()` uses one Redis Lua operation to increment a
sequence, append the event, and refresh both TTLs atomically. Events are kept
in a Redis list, and `_list_chat_run_events()` starts at the zero-based list
position implied by the last consumed sequence instead of rereading and
decoding the entire event history on every 250 ms poll. This materially
reduces repeated Redis work for long streamed answers.

Status, completed events, and terminal results survive a browser disconnect.
The worker itself remains an in-process thread in `chat_run_threads`; an
unexpected LangGraph process/container exit can still terminate active work.
A durable external job queue remains a production hardening item.

### Progress and streaming

`langgraph/progress.py` centralizes status descriptions through
`progress_chunk()` and tool-call naming through `tool_call_chunk_names()` and
`tool_status_description()`. `partial_python_code_argument()` safely decodes
the complete prefix of a partially streamed JSON `code` string, including
escape and Unicode-surrogate handling.

`TerminalGraphRuntime.call_model()` in `langgraph/idea_graph/runtime.py` now:

- streams normal assistant text immediately;
- announces a tool as soon as its name is known, before all arguments arrive;
- emits `python_code_start`, incremental `python_code_delta`, and
  `python_code_end` while the model is still generating the Python argument;
- closes an incomplete Python stream when a request is cancelled or fails;
- emits waiting/busy statuses during provider silence;
- applies an explicit request timeout and retries only a timeout that occurred
  before any provider chunk was observed; and
- classifies terminal timeout, capacity/rate-limit, and other provider
  failures for the UI.

The Pipe's `_translate_chunk()` renders streamed Python in a single four-backtick
block. `_is_streamed_python_replay()` suppresses only the matching completed
legacy `code` replay, so unrelated code events remain visible. Console blocks
are wrapped in non-rendering delimiters that `_sanitize_assistant_tool_history()`
can remove on the next request. Heartbeats and native Open WebUI status events
remain available for long silent operations.

`_split_streamable_message()` lets ordinary answer text reach the browser
immediately while buffering only a possible `sandbox:/outputs/...` or
`file:/outputs/...` reference until final file IDs are known. It handles
references split across token/event boundaries and caps an unmatched Markdown
label so buffering cannot grow without bound.

### Cooperative cancellation

`langgraph/idea_graph/control.py` adds thread-safe `RunCancellation` state.
`stop_chat_run()` persists a cancellation key (so queued jobs see it), signals
an active graph control, marks the run `stopping`, emits status, and calls the
sandbox interrupt route. The status transition is deliberately persisted
before waiting for the guest interrupt. Because
`tools.persistent_terminal.interrupt_run()` is a synchronous HTTP bridge,
`stop_chat_run()` invokes it with `asyncio.to_thread()`; the interrupt client
also has a dedicated 35-second timeout rather than inheriting the normal
1,800-second Python execution timeout. This prevents a slow sandbox from
blocking LangGraph health checks, event polling, or a subsequent prompt.
`_run_chat_job()` also monitors the Redis cancel key so a Stop can cross
process/request boundaries.

The sandbox service applies the same boundary in `sandbox_service/main.py`.
Both the long-lived `registry.run_python()` call and the synchronous
`registry.interrupt_run()` bridge run through `asyncio.to_thread()`. Previously,
`/run-python` occupied uvicorn's event-loop thread until Python completed, so
the service could not process the concurrent `/runs/<run-id>/interrupt`
request; LangGraph then blocked on that request and delayed all new chat runs.
The interrupt endpoint can now reach `terminal_registry.interrupt_run()` while
the original kernel request is active.

The graph checks Stop before a model call, before tool execution, and after
each tool. `TerminalGraphRuntime.call_model()` cancels an in-flight async model
stream. At the sandbox boundary, `terminal_registry.py` tracks active Python
and Codex runs by run ID; `interrupt_run()` sends a Jupyter interrupt or writes
the Codex cancellation signal without waiting for the execution lock held by
the active call. Completed checkpoint state and already-produced outputs are
retained.

## Model context, routing, caching, and telemetry

`langgraph/idea_config.py` replaces the previous `langgraph/config.py` as the
central configuration module. `IDEA_AGENT_MODEL` now selects the main model,
defaults to `gpt-5.6-terra`, and is used by the graph service and legacy
orchestrator. `IDEA_TOOL_MODEL` independently keeps helper tools
(`station_tool.py` and `web_search_tool.py`) on `gpt-5.6-terra` if the primary
model is changed.

`litellm/litellm_config.yaml` adds the Terra alias while retaining Sol and the
hidden Luna task model. LiteLLM disables same-deployment retries and defines
immediate Terra-to-Luna and Sol-to-Luna router fallbacks for throttling. The
client-side graph runtime owns the longer `IDEA_MODEL_REQUEST_TIMEOUT_SECONDS`
(180 seconds) and safe pre-output retry count (`IDEA_MODEL_MAX_RETRIES=1`).

`langgraph/agents/terminal_agent.py` adds `_prompt_cache_key()` and
`_cacheable_system_message()`. The cache key is stable per model/session but
HMAC/hash-derived so it does not expose user identity. System prompt metadata
marks the cache breakpoint. `TerminalGraphRuntime` measures each model-visible
request without logging its content and `_model_usage_record()` captures:

- message count, text characters, and image count;
- input, cached-input, cache-write-input, output, reasoning, and total tokens
  when the provider exposes them; and
- response ID and resolved model name.

`_summarize_model_usage()` in `langgraph_service.py` aggregates only records
belonging to the current run and includes the totals in run status and
`idea_context`. The documented remaining limitation is verification that the
deployed LiteLLM version forwards explicit `prompt_cache_options` to the
upstream endpoint; provider cache reads are observable, but explicit cache
depth is not yet proven.

## Python execution, errors, artifacts, and vision

### Persistent Python and execution records

Kernel requests now carry both `kernel_id` and `run_id` across:

- `persistent_terminal.run_python()`;
- `sandbox_service.main.RunPythonRequest` and `run_python()`;
- `terminal_registry.run_python()`;
- `MicrosandboxTerminal.run_python()`; and
- `interpreter_kernel/client.py` and `daemon.py`.

The daemon indexes language runners by kernel ID, so state follows the
configured kernel isolation scope rather than merely the sandbox/user.
`persistent_terminal.inspect_python_namespace()` makes a best-effort internal
kernel call after successful code to report selected object names, types,
tuple shapes, and collection lengths, explicitly excluding values and reprs.

`persistent_terminal.run_terminal()` continues to keep a short head/tail
result inline. When `_truncate_output()` must shorten it, the complete command
output is archived under `/tmp/idea_command_outputs` and can be paged with
`read_output_range()`. `write_file_stream()` provides bounded streaming binary
writes through the sandbox service and an atomic final rename.

### Structured Python errors

The Open Interpreter Jupyter adapter now preserves `format="error"` for
tracebacks instead of flattening them into ordinary output. That metadata is
retained by the kernel daemon, the sandbox service, and
`persistent_terminal._normalize_kernel_chunks()`; the latter also upgrades
tracebacks from older guest images through `_looks_like_legacy_kernel_error()`.

`TerminalGraphRuntime.execute_tool()` marks a Python tool outcome failed when
it receives error chunks while continuing to stream the traceback. The Pipe
renders those chunks as a labeled **Python execution error** block. This is a
tool failure the model can correct; it does not automatically convert the
whole assistant run into a generic failed response.

### Plot capture and durable image delivery

Python display-image chunks are no longer embedded as base64 in chat history.
`TerminalGraphRuntime.persist_kernel_image()` validates the image format and
base64 payload, writes it to
`/outputs/.idea/kernel-images/<run>-<index>.<format>`, records it as an
artifact/vision input, and emits a lightweight filename event. Invalid image
payloads become explicit console errors and never fall back to inline bytes.

`show_image_tool` and `inspect_image_tool` now have distinct meanings:

- display emits a user-visible image reference and deduplicates identical
  bytes within a turn; and
- inspection makes the file available to model vision without necessarily
  redisplaying it.

The graph tracks `active_artifact_id` and uses `_requests_visual_context()` to
restore the last relevant image only for an actual visual follow-up (for
example, “change this plot” or “what can you see?”). A new independent request
to create a plot does not automatically receive the previous image.

`_resolve_displayed_images()` in the Pipe matches emitted sandbox paths to the
files uploaded during finalization and produces durable Open WebUI preview
links. Basename fallback is accepted only when unambiguous. Missing mappings
do not expose base64. Model image requests use the configured vision-capable
primary model; attachment detection validates file magic, applies size/count
limits, uses high-detail image parts, and avoids logging data URIs.

### Output synchronization and links

`TerminalGraphRuntime.finalize()` calls
`TerminalAgent._sync_outputs_to_openwebui()` with both paths referenced in the
answer and paths displayed as images. The sync layer still uploads all new or
changed `/outputs` files, reuses its artifact registry for unchanged
referenced files, removes deleted registry entries, checks that HTML previews
are self-contained, and uploads concurrently under the current user's Open
WebUI authorization.

The Pipe's `_resolve_output_links()` replaces model-authored sandbox links
with exact Open WebUI file URLs after finalization. Passive preview-safe
extensions use the canonical `/api/v1/files/.../content` route so shared-chat
pages can rewrite them to their share-scoped authorization endpoint. Active
HTML uses the authenticated, sandboxed `/idea-file-preview/...` route in the
owner's chat. The current IDEA Open WebUI `v0.11.0-idea.0.8` image rewrites that link on shared
pages to an authorized share-scoped HTML response with the same sandbox
restrictions, allowing the webpage to open in a new tab. Other types use the
download endpoint. URL-encoded paths are normalized, Markdown labels are
escaped, duplicate generic attachments are suppressed, and unresolved
references are rendered as explicitly unavailable rather than left as
misleading sandbox URLs.

## Attachments, PaperQA, skills, and prompts

`_attached_resource_descriptors()` in the Pipe collects safe opaque file,
image, and collection IDs from direct attachments, message metadata, body
files, and Assistant knowledge. It rejects paths, external/data URLs, control
characters, invalid types, and duplicates. LangGraph re-authorizes each ID
with the current Open WebUI credential and copies immutable files to the
private sandbox in bounded streamed chunks. Existing copies are still
re-authorized each turn.

The graph preparation boundary now retains the synchronization result in the
first model call. Non-image uploads are named by their exact private sandbox
paths in a model-visible turn message. Uploaded images use Open WebUI's inline
multimodal content when present and otherwise fall back to the validated
sandbox copy, avoiding both invisible attachments and duplicate image pixels.
This correction is confined to the Pipe/LangGraph integration: it does not
require an Open WebUI customization-image or microsandbox-image change. If a
future Open WebUI release changes its attachment payload again, its supported
Pipe attachment contract should be documented and covered by the nested and
top-level descriptor tests before updating the pinned image.
Collection-only Assistant Knowledge resources remain available to lazy
PaperQA setup but do not run input synchronization or emit a misleading
"Preparing attached files…" status.

PaperQA is now prepared lazily. `TerminalAgent._ensure_paperqa_library()` does
not initialize a library during ordinary graph preparation; it runs only when
the PaperQA tool is invoked. This avoids unnecessary collection/PDF work and
allows an early Stop to complete promptly. PaperQA remains enabled only for
configured Assistants and authenticated non-guests.

`langgraph/utils/skill_loader.py` changes model-facing skill handling so the
model receives the complete validated skill document/bundle while logs and
the action ledger use `summarize_skill_result()` rather than copying private
instructions. Package-route results retain their loading plan and explicit
components. Existing traversal, symlink, frontmatter, dependency-cycle,
authorization, and size-limit checks remain enforced.

`langgraph/utils/system_prompt.md` was shortened and aligned with the new
execution memory. It tells the model to prefer one persistent Python analysis
call where practical, avoid redisplaying plots that Python already emitted,
use the dedicated display/inspection distinction, recognize Codex delegation,
and use the Open WebUI deployment's supported MathJax delimiters (`$...$` and
`$$...$$`, not `\(...\)` or `\[...\]`).
`langgraph/utils/skills/review-code/SKILL.md` was updated to fit the current
delegated-review workflow.

## Codex delegation

Codex is a subordinate tool of the LangGraph runtime, not a second graph and
not the primary conversational model.

### Tool and checkpoint integration

`persistent_terminal.make_codex_tool()` exposes only `task`, `cwd`, and
`access` to the model. `TerminalGraphRuntime.execute_tool()` injects the
sandbox identity, configured model/endpoint/key, prior thread ID for the same
working directory, and current run ID. The returned final response and changed
paths are bounded before becoming a model observation. Thread IDs are stored
in `IDEAState.codex_threads`; usage is appended to `IDEAState.codex_usage`.

The tool is registered by `TerminalAgent` only when Codex is enabled and its
endpoint/key are available. It is intended for substantial repository
investigation, implementation, debugging, or review—not trivial shell calls.

### Sandbox execution and security

`sandbox_service/main.py` adds `CodexRunRequest` and
`POST /sandboxes/<id>/codex/runs`. `terminal_registry.run_codex()` fails closed
on the local host-shell backend; no host fallback exists. Only `read-only` and
`workspace-write` are accepted.

`MicrosandboxTerminal.run_codex()` writes a mode-0600 temporary request file,
runs `/opt/oi_kernel/codex_runner.py`, parses its JSON result, and removes the
request and cancellation files. `interpreter_kernel/codex_runner.py` then:

- requires the working directory to be `/workspace` or a descendant;
- starts or resumes the OpenAI Codex SDK thread for that working directory;
- maps access to SDK read-only/workspace-write sandboxes;
- uses `ApprovalMode.deny_all`;
- passes the API key through a child environment variable rather than the
  command line or model tool schema;
- explicitly filters that credential from model-generated shell commands;
- stores Codex state under `/workspace/.idea/codex`;
- bounds summarized SDK events to `IDEA_CODEX_MAX_EVENTS`;
- returns thread ID, final response, changed paths, token usage, status, and
  error; and
- watches a run-specific cancellation file and interrupts the live turn.

The guest image pins the Python `openai-codex` SDK in
`modules/research/requirements.lock` (declared as `0.144.4` in
`requirements.in`) and installs the Codex CLI in the Dockerfile (currently
`0.147.0`).

`IDEA_CODEX_ENABLED` now defaults to true, but effective availability still
requires the rebuilt microsandbox image and a guest-reachable endpoint/key.
During the developer rollout, blank `IDEA_CODEX_BASE_URL` and
`IDEA_CODEX_API_KEY` fall back to external `OPENAI_BASE_URL` and
`OPENAI_API_KEY`. This bypasses LiteLLM budget attribution and is explicitly a
temporary configuration. Before non-developer rollout, the documented target
is a private TLS LiteLLM Responses API route plus a separate revocable,
model-restricted, low-budget virtual key. The master key and primary shared
IDEA virtual key must not be used.

## Microsandbox lifecycle and research guest image

### Runtime behavior

`sandbox_service/msb_sandbox.py` now checks KVM functionally with an ioctl,
not just by testing for a device path. `SANDBOX_BACKEND` supports `auto`,
`microsandbox`, and `local`; production can select `microsandbox` to fail
closed when isolation is unavailable.

`MicrosandboxTerminal._connect_or_create()` reads the named VM's state before
choosing create, connect, or start. This avoids accidentally recreating and
wiping a VM when `start()` fails for a reason other than nonexistence.
`_exec()` reconnects and retries once when a cached handle refers to a VM that
has stopped due to idle/max-duration rules.

Detached microsandbox VMs do not automatically execute the OCI entrypoint.
`_ensure_open_terminal()` therefore starts Open Terminal lazily and
idempotently, health-checks it, and repeats the operation after stop/resume.
`_get_open_terminal_key()` reads and caches the per-VM API key only through the
microsandbox filesystem channel. The persistent kernel is likewise started
lazily by `interpreter_kernel/client.ensure_daemon()`.

`terminal_registry.py` retains one terminal and lock per sandbox/user. Its
shutdown hook now stops all active VMs cleanly so Compose restart does not
kill them mid-write and strand overlays. Stop is state-preserving; destroy is
reserved for explicit workspace deletion.

### Image contents and dependency isolation

`interpreter_kernel/Dockerfile` was reworked into a multi-stage image based on
a digest-pinned Open Terminal image and digest-pinned Node image. It creates:

- `/opt/idea-venv` for the persistent kernel, Codex SDK, and research stack;
- `/opt/guarddog-venv` because GuardDog's Click constraint conflicts with
  Copernicus Marine;
- a pinned Chromium/Playwright installation and Codex CLI; and
- runtime OS support for geospatial libraries, OCR, PDF utilities, LaTeX,
  fonts, archive tools, Git/curl from the base, and explicit `wget`.

The compiler/geospatial headers remain in the builder stage. The runtime
copies only the resolved environments and required runtime libraries. The
kernel daemon now launches with `/opt/idea-venv/bin/python`, keeping research
dependencies isolated from Open Terminal's FastAPI/uvicorn environment.

`interpreter_kernel/config.env` selects the new `research` module.
`modules/research/requirements.in` describes intentional ranges for numerical,
oceanographic, geospatial, plotting, document, OCR, browser, and utility
packages. `requirements.lock` pins the transitive, cross-platform resolution.
The old `modules/original` set remains a historical snapshot and is no longer
build-selectable because its uncoordinated pins break the service environment.

`interpreter_kernel/DEPENDENCY_AUDIT.md` records the current audit. It accepts
one constrained Click finding with a documented exposure/removal condition
and identifies an Intake report as a scanner false positive because the
installed release contains the fix. The older environment produced 176
findings in 32 packages.

### Build, test, publication, and rollout

New image validation paths are:

- `interpreter_kernel/smoke_test.py` for scientific, document, OCR, browser,
  Codex, GuardDog, Open Terminal, and persistent-kernel checks;
- `interpreter_kernel/test_image.sh` for a local build, both virtual-
  environment `pip check`s, smoke tests, service startup, and a 6 GiB unpacked
  image ceiling;
- `interpreter_kernel/test_microsandbox_image.sh` for loading the local image
  into microsandbox and testing one disposable VM through the production SDK
  path; and
- `sandbox_service/test_local_image.py` for the sandbox-side image checks.

`.github/workflows/microsandbox-image.yml` is manual for publication. It first
builds/tests the local architecture, then—only with `publish=true`, a valid
immutable version, and approval through the
`microsandbox-image-publish` GitHub Environment—builds amd64/arm64 images with
SBOM and provenance attestations. It publishes immutable
`research-<version>` and `sha-<commit>` tags and records the digest; it does not
move a `latest`/`slim` tag.

Existing VMs do not adopt a changed `SANDBOX_IMAGE`.
`interpreter_kernel/refresh_sandboxes.sh` now requires the explicit
`--allow-destructive-developer-refresh` flag, pulls the configured image, and
recreates every VM. This wipes writable VM files and is acceptable only for
the current developer-only test population. A tested snapshot/restore or
other non-destructive migration is a release gate before real users are
admitted.

## Open WebUI and Assistant configuration

### Native code runtimes

IDEA's Python state lives in its authenticated persistent sandbox kernel.
Open WebUI's native browser/Jupyter Code Execution and Code Interpreter are
separate runtimes that cannot see that state. `docker-compose.yml` and
`example.env` therefore default `ENABLE_CODE_EXECUTION=false` and
`ENABLE_CODE_INTERPRETER=false`.

`openwebui/configure_openwebui.py` adds
`configure_native_code_execution()`. It reads the complete Open WebUI code-
execution form, changes only those two flags, posts the complete form, and
verifies both results so engine, Jupyter, authentication, timeout, and prompt
settings remain intact.

### Suggested prompts

`assistants/manifest.json` now defines exactly six default Welcome prompts
and six domain prompts each for SEA and Mars. It also allowlists `cindra` to
receive only Welcome suggestion metadata without making that Assistant
deployment-managed.

`assistants/deploy_assistants_openwebui.py` adds:

- `validated_suggestion_prompts()` to require six objects with two nonempty
  title parts and nonempty content;
- inheritance of defaults for manifest Assistants without an override;
- suggestion metadata in `official_assistant_payload()`;
- `deploy_welcome_suggestions()` for the narrow external-Assistant allowlist;
  and
- `verify_welcome_suggestions()` plus expanded `verify_assistants()` checks.

Reconciliation deep-copies prompt structures, preserves unmanaged fields, and
does not broaden ownership of user/community Assistants. The default prompts
cover capability discovery, upload-based analysis, climate-index plotting,
literature collections, custom Assistants, and reproducible outputs; SEA and
Mars use domain-specific starter tasks.

## Incremental persistent-Python output

Python output now streams across every process boundary instead of being
collected until the Jupyter execution ends:

- `interpreter_kernel/daemon.py` adds `/run-stream`, which writes one flushed
  NDJSON envelope for each chunk yielded by `JupyterLanguage.run()` and a final
  `end` record. The existing `/run` response remains available for compatibility.
- `interpreter_kernel/client.py:run_stream()` forwards those records through
  the in-VM process stdout immediately.
- `sandbox_service/msb_sandbox.py:MicrosandboxTerminal.run_python_stream()`
  consumes `Sandbox.shell_stream()` on the terminal's dedicated asyncio loop,
  parses stdout incrementally, and yields normalized chunks to
  `terminal_registry.run_python_stream()` while its per-sandbox execution lock
  and active-run registration remain held.
- `sandbox_service/main.py` exposes
  `/sandboxes/{sandbox_id}/run-python/stream` as `application/x-ndjson` with
  proxy buffering disabled. The legacy non-streaming endpoint is retained.
- `langgraph/tools/persistent_terminal.py:run_python_stream()` consumes the
  response with `httpx.Client.stream()`. `TerminalGraphRuntime.execute_tool()`
  emits the first console event with `start=True`, subsequent deltas with the
  same tool-call identity, and one empty `end=True` closure in `finally`.
- `openwebui/functions/idea_pipe.py:Pipe._translate_chunk()` honors those
  boundaries, so all print/error deltas from one Python execution appear in a
  single live Markdown output block rather than separate completed blocks.

Run-scoped interruption remains independent of the execution lock. If a
stream consumer disconnects unexpectedly, the microsandbox bridge requests a
kernel interrupt before cancelling its SDK producer, preserving the Stop fix
while avoiding an orphaned execution.

## Scientific-tool compatibility

`langgraph/utils/tools/climate_tool.py` was updated for pandas 3 compatibility:

- deprecated `delim_whitespace=True` calls use `sep=r"\s+"`;
- IOD data resamples at month start (`MS`) and shifts to the 15th, replacing
  the removed/ambiguous `M` alias; and
- `_CANONICAL_INDEX_NAMES` maps case-insensitive requests back to the exact
  advertised index key before lookup and output generation.

The existing batch CSV/provenance workflow, safe `/workspace` path checks,
deduplication, missing-value conversion, compact model result, and byte-level
sandbox writes are retained.

## Configuration summary and compatibility paths

The principal new/changed settings are documented in `example.env` and wired
through `docker-compose.yml`:

- graph/runtime: `IDEA_AGENT_RUNTIME`, `IDEA_AGENT_MODEL`,
  `IDEA_MODEL_REQUEST_TIMEOUT_SECONDS`, and `IDEA_MODEL_MAX_RETRIES`;
- persistence/identity: `LANGGRAPH_DB_PASSWORD`, `LANGGRAPH_AES_KEY`,
  `IDEA_IDENTITY_SECRET`, `IDEA_KERNEL_SCOPE`, and
  `IDEA_CHECKPOINT_MAP_TTL_SECONDS`;
- bounded state/context: `IDEA_MAX_STATE_BYTES`,
  `IDEA_MAX_RECENT_ACTIONS`, `IDEA_MAX_RECENT_EXECUTIONS`,
  `IDEA_MAX_CODE_INLINE_BYTES`, `IDEA_MAX_EXECUTION_MEMORY_BYTES`,
  `IDEA_MAX_MODEL_TOOL_OBSERVATION_BYTES`,
  `IDEA_MAX_MODEL_HISTORY_MESSAGE_BYTES`, and
  `IDEA_MAX_IDENTICAL_TOOL_CALLS`;
- Codex: `IDEA_CODEX_ENABLED`, `IDEA_CODEX_MODEL`,
  `IDEA_CODEX_BASE_URL`, `IDEA_CODEX_API_KEY`, and
  `IDEA_CODEX_MAX_EVENTS`;
- sandbox: `SANDBOX_BACKEND` and the existing image/KVM settings; and
- UI: `ENABLE_CODE_EXECUTION` and `ENABLE_CODE_INTERPRETER`.

Compose defaults `IDEA_AGENT_RUNTIME=langgraph`. The `manual` runtime and
direct `/chat` endpoint remain available for rollback and still use the old
`ConversationOrchestrator`/Redis transcript path. `/clear` only clears that
legacy direct-chat history; it is not the mechanism for deleting Open WebUI
conversation history or graph checkpoints.

The shared LiteLLM virtual-key example budget was raised from $50 to $100 and
now includes Terra, Sol, Luna, and the embedding model. Per-user attribution
continues through `x-litellm-end-user-id`.

## Test coverage added or materially expanded

The branch adds focused regression coverage rather than relying only on
end-to-end manual testing:

- `tests/test_langgraph_memory.py`: graph routing, exact source archival,
  bounded ledgers/context, repeated-call blocking, continuation, Stop,
  identities, encryption, usage, visual follow-ups, and Codex checkpoint data.
- `tests/test_chat_run_events.py`: atomic event sequencing, incremental reads,
  model defaults, run-only usage totals, non-blocking sandbox interruption,
  root regeneration, legacy migration, ID-less histories, and
  assistant-message checkpoint mappings.
- `tests/test_model_cancellation.py`: early tool/Python streaming, wait and
  capacity statuses, safe timeout retry, no retry after partial output, live
  model cancellation, and closure of partial Python streams.
- `tests/test_vision.py`: magic-byte validation, attachment limits,
  multimodal messages, safe logging, show/inspect semantics, deduplication,
  durable plot files, invalid-image handling, namespace inspection, active
  visual context, cache metadata, and Python error outcomes.
- `tests/test_codex_integration.py`: path/access rejection, SDK event/result
  collection, credential separation, local-backend fail-closed behavior, and
  absence of the key from the guest command line.
- `tests/test_idea_pipe_assistants.py`: authoritative structured history,
  compaction-policy separation, attachment filtering, PaperQA gating, link and
  image resolution, early text streaming, progress, duplicate-error
  suppression, Stop propagation, Python fences/errors, and next-turn history
  sanitization.
- `tests/test_terminal_output_archiving.py`: full-output archives, streaming
  atomic writes, lazy Open Terminal startup, kernel/run routing, incremental
  microsandbox and HTTP NDJSON delivery, legacy error normalization, bounded
  interrupt requests, and run interruption.
- `sandbox_service/test_service_concurrency.py`: verifies that Python execution
  and run interruption are dispatched off the sandbox service event loop.
- `tests/test_skill_loader.py`: full skill/bundle delivery with redacted logs
  and updated system-prompt requirements.
- `tests/test_climate_tool.py`: every advertised parser under current pandas,
  canonical naming, safe paths, batched datasets/provenance, and malformed
  source rejection.
- `tests/test_deploy_assistants_openwebui.py` and
  `tests/test_configure_openwebui.py`: suggested-prompt validation/reconciliation
  and non-destructive disabling of Open WebUI's unrelated code runtimes.

## Known limitations and development follow-up

The implementation is substantially more durable, but the following are not
closed by this change set:

1. Active chat workers are still process-local daemon threads. Redis events
   survive client disconnects, but a LangGraph container failure can end a run.
2. Codex's developer-stage `OPENAI_*` fallback bypasses LiteLLM budgeting and
   must be replaced before broader rollout.
3. **TODO before non-developer rollout:** replace destructive microVM
   recreation with a tested snapshot/restore or equivalent versioned migration.
   During the current developer-only phase, existing VMs may be recreated to
   adopt this new kernel image and all developers may start with empty state.
4. Production still needs immutable multi-architecture guest-image publication,
   environment provisioning, recovery/concurrency/security smoke testing, and
   backup/restore procedures for all four storage owners.
5. LiteLLM prompt/completion retention policy and forwarding of explicit
   prompt-cache options require production verification.
6. The legacy manual runtime remains in the tree, so maintainers must be clear
   whether an issue is on `/chat-runs` or the compatibility `/chat` path.

Current concise status and release gates are maintained in
`langgraph/IMPLEMENTATION_STATUS.md`; deployment procedure is in
`docs/Quick-Deploy.md`; the guest-image lifecycle is in
`interpreter_kernel/README.md`; and credential/security details for delegated
coding are in `docs/Codex-Integration.md`.
