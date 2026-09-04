# IDEA LangGraph service

This service is IDEA's tool-using conversation runtime. Open WebUI supplies the
selected Assistant policy and the authoritative, branch-aware conversation;
LangGraph performs model/tool iterations, checkpoints bounded execution memory,
and streams progress and results back through IDEA's Open WebUI Pipe.

The Compose deployment runs the service on internal port `8010`. Its HTTP API
must remain private and protected with `INTERNAL_SERVICE_TOKEN`.

## Request flow

1. `openwebui/functions/idea_pipe.py` normalizes the selected conversation
   branch, attachments, Assistant instructions, and compact IDEA context.
2. `POST /chat-runs` persists a queued run record in Redis and starts its
   worker thread. The Pipe polls the status and sequence-numbered events.
3. `langgraph_service.py` derives separate thread, workspace, kernel, and run
   identities and resolves the checkpoint for the visible parent assistant
   message.
4. `idea_graph/graph.py` runs the checkpointed state graph: prepare the turn,
   call the model, execute at most one tool, pass through the cancellation
   gate, and either continue or finalize.
5. Terminal, file, Python, image, and Codex operations go through the
   authenticated `sandbox_service`; LangGraph never executes user code in its
   own container.
6. Generated artifacts are synchronized into Open WebUI under the current
   user's authorization and returned as durable file links.

## State and persistence

- **Open WebUI PostgreSQL data** is the authoritative conversational record.
  Edits, regenerations, branch selection, and context compaction happen there.
- **LangGraph PostgreSQL checkpoints** retain bounded execution state: action
  and Python ledgers, artifact/dataset references, continuation state, model
  usage, and Codex thread IDs. A dedicated role/schema is created by
  `db/setup_langgraph_db.sh`; `LANGGRAPH_AES_KEY` enables checkpoint
  encryption.
- **Redis** stores transient chat-run status/events and maps Open WebUI
  assistant message IDs to LangGraph checkpoint branches. It is not the
  authoritative conversation transcript for `/chat-runs`.
- **Microsandbox** owns persistent per-user workspace files. Python kernels
  default to a separate user/chat/Assistant identity, controlled by
  `IDEA_KERNEL_SCOPE`.

State limits are configured through the `IDEA_MAX_*` variables in
`example.env`. Large tool output is archived in the sandbox and represented to
the model by bounded excerpts.

## Tools

`agents/terminal_agent.py` registers the current tool surface:

- persistent shell execution and direct file writes;
- a persistent Python kernel with automatic plot capture;
- artifact publication and paged retrieval of archived output;
- explicit image display and model-side image inspection;
- built-in and Open WebUI Workspace skills;
- scientific data, station, climate, and web tools;
- PaperQA for authorized, non-guest knowledge collections; and
- optional `delegate_to_codex` for substantial coding work.

Codex runs inside the same microsandbox workspace. It is subordinate to the
LangGraph graph, supports only `read-only` and `workspace-write`, and resumes a
checkpointed thread by working directory. See
[`../docs/Codex-Integration.md`](../docs/Codex-Integration.md).

## Model routing

The primary IDEA model is selected with `IDEA_AGENT_MODEL` and defaults to
`gpt-5.6-terra`. `IDEA_TOOL_MODEL` keeps station and web helper inference on
Terra if the primary changes. Primary chat uses the internal
LiteLLM proxy and `LITELLM_VIRTUAL_KEY`; the end user's email is forwarded for
usage attribution. The station/web helpers currently use the direct
`OPENAI_*` endpoint rather than the proxy.

During the developer-only Codex rollout, blank `IDEA_CODEX_BASE_URL` and
`IDEA_CODEX_API_KEY` values fall back to `OPENAI_BASE_URL` and
`OPENAI_API_KEY`. This is intentionally temporary because it bypasses LiteLLM
budgeting and per-user attribution.

## API

All endpoints except `/health` require the internal bearer token when
`INTERNAL_SERVICE_TOKEN` is configured.

- `GET /health` — service health.
- `POST /chat-runs` — queue a run and return its `run_id`.
- `GET /chat-runs/{run_id}` — read queued/running/terminal status.
- `GET /chat-runs/{run_id}/events?after=<seq>` — poll ordered events.
- `POST /chat-runs/{run_id}/stop` — request cooperative cancellation.
- `POST /chat` — legacy direct-streaming compatibility path.
- `POST /clear` — clears only the legacy direct-streaming Redis history.

The normal Open WebUI integration uses `/chat-runs`. Event records have a
24-hour default TTL (`CHAT_RUN_TTL_SECONDS=86400`) and are compacted to avoid
unbounded Redis growth.

## Configuration and startup

Copy `example.env` to `.env`, configure the provider, database, LiteLLM,
identity, and internal-service secrets, then initialize the database roles:

```bash
./litellm/setup_litellm_db.sh
./langgraph/db/setup_langgraph_db.sh
docker compose up -d --build
```

Compose sets `IDEA_AGENT_RUNTIME=langgraph`; `manual` remains a rollback path.
The main deployment instructions are in [`../README.md`](../README.md), with a
short production checklist in [`../docs/Quick-Deploy.md`](../docs/Quick-Deploy.md).

Important variables are documented inline in `example.env`:

- `IDEA_AGENT_RUNTIME`, `IDEA_AGENT_MODEL`, and model timeout/retry settings;
- `LANGGRAPH_DATABASE_URL`, `LANGGRAPH_AES_KEY`, and `IDEA_IDENTITY_SECRET`;
- `LITELLM_PROXY_URL` and `LITELLM_VIRTUAL_KEY`;
- `INTERNAL_SERVICE_TOKEN` and `SANDBOX_SERVICE_URL`;
- `IDEA_KERNEL_SCOPE` and the `IDEA_MAX_*` state bounds; and
- `IDEA_CODEX_ENABLED`, `IDEA_CODEX_MODEL`, endpoint/key overrides, and event
  limit.

## Verification

Run the focused unit suite from the repository root:

```bash
pytest -q \
  tests/test_langgraph_memory.py \
  tests/test_chat_run_events.py \
  tests/test_model_cancellation.py \
  tests/test_terminal_output_archiving.py \
  tests/test_vision.py \
  tests/test_codex_integration.py
```

For a deployed stack, also verify an ordinary answer, Python state across
turns, an uploaded file and image, a generated downloadable artifact,
Stop/cancellation, branch regeneration, PaperQA, and a Codex read-only and
workspace-write turn.

## Known limitations

- Chat-run execution uses in-process worker threads. Status and events survive
  a client disconnect, but an unexpected LangGraph container restart can
  terminate an active run; a durable external task queue remains future work.
- The direct `/chat` path retains the old Redis transcript behavior and is not
  the production Open WebUI path.
- Codex currently uses the developer-stage external `OPENAI_*` fallback until
  a guest-reachable, restricted LiteLLM Responses API route is available.
- Existing user microVMs do not automatically adopt a new guest image. The
  current refresh script is destructive and is acceptable only while all
  workspaces belong to developers.

See [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) for the concise
status and remaining production gates. `INTEGRATION_PLAN.md` records the
current rollout plan; the original adapter-era plan is available in Git
history rather than being presented as current architecture.
