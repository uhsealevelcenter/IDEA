# Open WebUI Integration

Adds [Open WebUI](https://github.com/open-webui/open-webui) as an additional
frontend for the existing `langgraph` agent (`ConversationOrchestrator` /
`TerminalAgent`) and `sandbox_service` per-user microVM isolation. Nothing in
`langgraph/` or `sandbox_service/` changes - this integration is purely a
translation layer.

## How it works

`functions/idea_pipe.py` is an Open WebUI [Pipe
function](https://docs.openwebui.com/features/plugin/functions/pipe) that:

1. Registers as a selectable model ("IDEA Terminal Agent") in Open WebUI's
   model dropdown.
2. On each chat turn, POSTs to the existing `langgraph_service.py` `/chat`
   SSE endpoint (see `langgraph/langgraph_service.py`), mapping Open WebUI's
   `user.id` / `chat_id` into the `user_id` / `session_id` fields
   `ConversationOrchestrator` already expects.
3. Translates each streamed chunk (`{role, type, content, format, start,
   end}` - the same format `frontend/assistant.js` renders) into markdown
   text/images for Open WebUI's chat pane.

## Running it

The `openwebui` service is already wired into the root `docker-compose.yml`
alongside `langgraph`, `sandbox`, and `redis`:

```bash
docker compose up -d openwebui langgraph sandbox redis
```

Open WebUI will be available at `http://localhost:3001` (see
`docker-compose.yml` - remapped from the default 3000 if that port is
already taken on your host).

**One-time manual step (Open WebUI Functions have no filesystem
auto-discovery - they live in its own database):**
1. Sign up / log in (first account becomes admin).
2. **Admin Panel > Functions > "+"** and paste the full contents of
   `./openwebui/functions/idea_pipe.py`, or use **Import from URL** if
   this repo is pushed somewhere reachable.
3. Enable the function once created.
4. Back in a new chat, "IDEA Terminal Agent" will now appear in the model
   dropdown (top-left of the chat page) - not in Admin Panel > Settings >
   Models, which only manages Ollama/OpenAI connections, unrelated to Pipe
   Functions.

## External task model (`gpt-5.6-luna`)

Open WebUI uses an external task model for auxiliary work such as titles,
tags, follow-up suggestions, and search queries. IDEA keeps this separate
from the user-facing Pipe model:

- `IDEA Terminal Agent` remains the only visible chat model and continues
  to use `gpt-5.6-sol` through LangGraph.
- `gpt-5.6-luna` is exposed by the internal LiteLLM proxy, registered with
  Open WebUI, and marked hidden so it remains available to backend tasks
  without appearing in the chat model selector.

After Open WebUI and LiteLLM are running, apply the configuration with:

```bash
./openwebui/configure_openwebui.py
```

The script reads `.env`, authenticates with the admin
`OPENWEBUI_API_KEY`, and idempotently reconciles the internal LiteLLM
connection, Luna's hidden/public model metadata, `TASK_MODEL_EXTERNAL`,
context compaction, and the title-generation prompt. Context compaction is
enabled at 136,000 tokens by default, matching legacy IDEA's production
policy of compacting at 50% of its 272,000-token long-context threshold.
The existing compaction prompt remains independently editable.

The configurator deliberately leaves
`ENABLE_PERSISTENT_CONFIG` at its default (`true`), so other Admin Panel
changes remain persistent and editable. `WEBUI_ADMIN_EMAIL` and
`WEBUI_ADMIN_PASSWORD` may be supplied as a fallback if no admin API key
is available. When run interactively, the script instead prompts for any
missing admin credentials and does not save the password.

## Automatic file sync (`/outputs` → Open WebUI Files)

Any file the agent creates or modifies under `/outputs` in its sandbox (see
`langgraph/utils/system_prompt.md`) is automatically uploaded to Open
WebUI's own Files storage at the end of that turn
(`TerminalAgent._sync_outputs_to_openwebui` in
`langgraph/agents/terminal_agent.py`) and shown as a filename-only link in
the chat response - no extra tool call or user action needed. Open WebUI's
Markdown renderer opens the link in a new tab. In local development, nginx's
authenticated `/idea-file-preview/` route displays browser-safe formats
(including PNG and HTML) inline; other formats retain the normal download
behavior. Generated HTML is served with a sandbox Content Security Policy.
The model is free to reorganize/rename files under `/outputs` mid-turn
(`run_terminal_tool`); unchanged files from earlier turns are not attached
again. Successful path/signature-to-file-ID mappings are retained in a
per-user Redis artifact registry. If a later response references an unchanged
`/outputs` file, the existing Open WebUI file ID is reused without another
upload. A missing registry entry is recovered by uploading that explicitly
referenced file once.

`/workspace` remains private working storage and is never scanned or uploaded
automatically. The agent's `publish_artifact_tool` explicitly copies one
selected regular file from `/workspace` into `/outputs`; the resulting output
snapshot then follows the same upload and registry flow. This publication
boundary prevents scratch files and intermediate data from becoming
deliverables accidentally.

HTML outputs must be self-contained: generated images use `data:` URLs,
CSS is placed in `<style>` blocks, and custom JavaScript is placed in
`<script>` blocks. This is required because the browser security sandbox
intentionally withholds the Open WebUI session from page subresource
requests. Output sync uploads the HTML bytes unchanged and performs a
non-mutating validation for local references in `src`, `srcset`, `href`,
`poster`, and CSS `url(...)`; any remaining local dependency is logged as
a warning. Large resources should remain separate downloadable outputs
instead of being embedded.

The Pipe forwards the current user's Open WebUI bearer/session credential
only for the active internal chat request. LangGraph uses that credential
for the final upload, so Open WebUI owns the generated files as that user
and its normal `/api/v1/files/{id}/content` authorization succeeds. The
credential is not included in model messages or Redis conversation history.
Uploads are concurrent and bounded by `OUTPUT_SYNC_TIMEOUT_SECONDS` (30
seconds by default), with at most `OUTPUT_SYNC_MAX_WORKERS` active uploads.

## Environment variables (`.env`)

These are read via `docker-compose.yml` and `.env`; see `example.env` for
the canonical template.

- **`WEBUI_SECRET_KEY`** - signs Open WebUI's session/auth JWTs. Generate a
  random value once per deployment and keep it stable (changing it
  invalidates every existing login session):
  ```bash
  openssl rand -hex 32
  ```
  Paste the output as `WEBUI_SECRET_KEY=...` in `.env`.

- **`ENABLE_SIGNUP`** - `true`/`false`, no generation needed. Controls
  whether Open WebUI's own sign-up page accepts new accounts. Leave `true`
  for the first deploy (the first account created becomes admin - see
  "Running it" above), then set to `false` afterward to stop further
  self-service sign-ups if this instance isn't meant to be open to anyone.

- **`OPENWEBUI_BASE_URL`** - no generation needed for the default setup.
  This is `langgraph`'s address for reaching `openwebui` over the Docker
  Compose network, already defaulted to `http://openwebui:8080` in
  `docker-compose.yml` (see line ~93) - only set this in `.env` if
  `openwebui` is proxied/renamed/run on a different host than the default
  compose network.

- **`OPENWEBUI_API_KEY`** - optional administrator API key used by
  deployment/configuration scripts such as `configure_openwebui.py`. It is
  not used for per-user output syncing.

- **`OUTPUT_SYNC_TIMEOUT_SECONDS`** - whole-batch deadline for final output
  uploads; defaults to 30 seconds.

- **`OUTPUT_SYNC_MAX_WORKERS`** - maximum concurrent output uploads;
  defaults to 4.

## Status

This runs *alongside* the existing custom frontend (`frontend/`, `nginx`,
`app.py`) rather than replacing it - both currently point at the same
`langgraph` + `sandbox` backend. See `langgraph/IMPLEMENTATION_STATUS.md`
for the underlying agent/sandbox architecture this depends on.

**Not yet handled by the Pipe function:**
- Conversation history persistence beyond what `langgraph_service.py`
  already does in Redis (`langgraph_messages:{session_key}`) - Open WebUI's
  own chat history and this Redis history are not reconciled.
- Interruption/stop button (`ConversationOrchestrator` has no cancel
  endpoint exposed yet).
- Mapping Open WebUI's guest/pending-approval users to this repo's own
  guest-model/guest-scrutiny policies beyond the basic `is_guest` flag.
