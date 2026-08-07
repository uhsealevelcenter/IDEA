# Open WebUI Integration

Adds [Open WebUI](https://github.com/open-webui/open-webui) as an additional
frontend for the existing `langgraph` agent (`ConversationOrchestrator` /
`TerminalAgent`) and `sandbox_service` per-user microVM isolation. The Pipe
is the browser/API translation boundary; trusted attachment and PaperQA
processing continues inside LangGraph.

## How it works

`functions/idea_pipe.py` is an Open WebUI [Pipe
function](https://docs.openwebui.com/features/plugin/functions/pipe) that:

1. Registers as a selectable model ("IDEA Agent") in Open WebUI's
   model dropdown.
2. On each chat turn, POSTs to the existing `langgraph_service.py` `/chat`
   SSE endpoint (see `langgraph/langgraph_service.py`), mapping Open WebUI's
   `user.id` / `chat_id` into the `user_id` / `session_id` fields
   `ConversationOrchestrator` already expects.
3. Translates each streamed chunk (`{role, type, content, format, start,
   end}` - the same format `frontend/assistant.js` renders) into markdown
   text/images for Open WebUI's chat pane. LangGraph `status` chunks are
   forwarded through Open WebUI's injected `__event_emitter__` and appear in
   its native response-status UI; this requires no Open WebUI source changes.

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
4. Back in a new chat, "IDEA Agent" will now appear in the model
   dropdown (top-left of the chat page) - not in Admin Panel > Settings >
   Models, which only manages Ollama/OpenAI connections, unrelated to Pipe
   Functions.

## External task model (`gpt-5.6-luna`)

Open WebUI uses an external task model for auxiliary work such as titles,
tags, follow-up suggestions, and search queries. IDEA keeps this separate
from the user-facing Pipe model:

- `IDEA Agent` remains the only visible chat model and continues
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
Open WebUI's native execution settings, context compaction, and the
title-generation prompt. Native Code Execution and Code Interpreter are
disabled by default because they run in a separate Pyodide/Jupyter runtime
that cannot access IDEA's persistent LangGraph kernel or workspace. This
does not disable IDEA's own Python tools. Context compaction is enabled at
136,000 tokens by default, matching legacy IDEA's production policy of
compacting at 50% of its 272,000-token long-context threshold.
The configurator also raises Open WebUI's Token Cap to at least that
threshold, while preserving an existing higher cap, so a stale lower cap
cannot cause earlier-than-configured compaction. The existing compaction
prompt remains independently editable.

The configurator deliberately leaves
`ENABLE_PERSISTENT_CONFIG` at its default (`true`), so other Admin Panel
changes remain persistent and editable. `WEBUI_ADMIN_EMAIL` and
`WEBUI_ADMIN_PASSWORD` may be supplied as a fallback if no admin API key
is available. When run interactively, the script instead prompts for any
missing admin credentials and does not save the password.

## PaperQA2 literature collections

Welcome, SEA, and Mars are PaperQA-enabled by `assistants/manifest.json`.
Their deployment metadata forces legacy function handling so attached
Knowledge collection descriptors reach the IDEA Pipe, while
`capabilities.file_context` is disabled to prevent Open WebUI's native RAG
from injecting a second copy of the same literature context.

The Pipe's `PAPERQA_ASSISTANT_IDS` Valve defaults to
`welcome-assistant,sea,mars-assistant`. Keep this list in sync with manifest
entries whose `paperqa_enabled` value is true. LangGraph resolves each
collection through Open WebUI's authenticated
`/api/v1/knowledge/{id}/files` API and downloads only authorized PDFs.
Collection-only indexes are isolated by user, Assistant, and collection set
and reused across chats. Direct PDF attachments are additive only within the
current chat. Guest/pending users never receive the PaperQA tool.

PaperQA answer generation, evidence summaries, and agent planning all use
`gpt-5.6-luna`; embeddings use `text-embedding-3-small`. Both routes go
through the internal LiteLLM proxy and shared virtual key. The PaperQA
library and index live on the durable `idea_paperqa_data` volume. If an
existing virtual key predates this integration, regenerate or update it to
allow `gpt-5.6-luna` and `text-embedding-3-small` as documented in
`example.env`.

## Unified IDEA and Open WebUI Skills

IDEA exposes one `view_skill(source, id, route="", components=None)` tool for
two authoritative skill stores. Flat built-in skills are discovered from
`langgraph/utils/skills/*/SKILL.md`, with their catalog generated from YAML
frontmatter on each run. A built-in directory becomes a hierarchical package
when it also contains `manifest.yaml` with `schema_version: 1`. The manifest
may declare shared `references`, modular `components`, dependency edges, and
named `routes`. The LangGraph process reads these files directly; skill
content never passes through the sandbox terminal or its output truncation.

For a package, the generated catalog advertises its named routes. A route
load always returns the root `SKILL.md` followed by the complete dependency
closure in deterministic dependency-first order. Shared documents are
deduplicated. Unknown IDs, dependency cycles, excessive depth/count/size,
path traversal, and symlinks fail the entire request; no partial package is
returned. Package-root policy takes precedence if component instructions
conflict. CIndRA is the first package using this general mechanism.

A minimal package manifest is:

```yaml
schema_version: 1
id: example
entrypoint: SKILL.md
references:
  shared-policy:
    path: shared/policy.md
components:
  example-workflow:
    path: skills/example-workflow/SKILL.md
    requires: [shared-policy]
routes:
  standard:
    description: Apply the standard example workflow.
    components: [example-workflow]
```

Open WebUI Workspace skills retain Open WebUI's native activation behavior.
Skills selected with a `$` mention or the per-chat Skills toggle arrive as
complete system context. Model-attached skills arrive as an
`<available_skills>` manifest and IDEA loads the selected entry with
`view_skill(source="workspace", id=...)`. The Workspace read uses the current
user's forwarded bearer/session credential against Open WebUI's Skills API,
preserving ownership, active-state, and access-control checks. IDEA does not
fall back between same-named built-in and Workspace skills. Workspace skills
remain single-document skills: package route/component requests are rejected
until Open WebUI can store and return a package atomically under one
authorization and version boundary.

Skill documents are returned to the model in full, subject to the per-document
limit. Hierarchical selections also have aggregate document-count,
dependency-depth, and byte limits. Oversized skills and packages fail
explicitly instead of being partially returned. Application logs record only
skill source, ID, route, document/byte count, and SHA-256—not private skill
instructions or user credentials.

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

Files attached to a user message flow in the other direction. The Pipe sends
only their Open WebUI file IDs and display metadata to LangGraph. Before
invoking the model, LangGraph uses that same user's current credential to
re-authorize each ID through Open WebUI and streams the original bytes into
the user's sandbox at:

```text
/workspace/uploads/<file-id>/<sanitized-original-name>
```

The exact paths are appended to that turn's model context. Files are copied
atomically and binary formats such as NetCDF are preserved unchanged. A
previously copied ID is reused only after authorization is checked again.
If authorization, transfer, or size validation fails, the run stops instead
of asking the model to work without the attachment. Input files are retained
with the user's persistent sandbox; they are not published as outputs.

PNG, JPEG, GIF, and WebP attachments are additionally sent to the configured
vision-capable model as high-detail multimodal content. The model therefore
receives the actual pixels as well as the sandbox path. This is separate from
`show_image_tool`, which only renders an image in the user's browser.
`inspect_image_tool` supplies an existing sandbox image to model vision in
the next agent iteration. Vision input is bounded by
`VISION_MAX_IMAGE_BYTES` and `VISION_MAX_IMAGES_PER_TURN`; an image outside
those limits remains available as a normal sandbox file.

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
for attachment downloads and final output uploads, so Open WebUI's normal
file ownership and sharing authorization applies in both directions. The
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

- **`INPUT_SYNC_TIMEOUT_SECONDS`** - whole-turn deadline for copying input
  attachments into the sandbox; defaults to 120 seconds.

- **`INPUT_SYNC_MAX_FILE_BYTES`** - maximum size of one input attachment,
  enforced by both LangGraph and the sandbox service; defaults to 1 GiB.

- **`VISION_MAX_IMAGE_BYTES`** - maximum size of one synchronized or
  inspected image sent to model vision; defaults to 20 MiB.

- **`VISION_MAX_IMAGES_PER_TURN`** - maximum number of uploaded images sent
  to model vision in one turn; defaults to 8.

- **`OUTPUT_SYNC_TIMEOUT_SECONDS`** - whole-batch deadline for final output
  uploads; defaults to 30 seconds.

- **`OUTPUT_SYNC_MAX_WORKERS`** - maximum concurrent output uploads;
  defaults to 4.

- **`PQA_LLM_MODEL`** / **`PQA_EMBEDDING_MODEL`** - PaperQA model aliases;
  default to `gpt-5.6-luna` and `text-embedding-3-small`.

- **`PQA_SYNC_TIMEOUT_SECONDS`** / **`PQA_MAX_PDF_BYTES`** - authenticated
  collection/direct-PDF synchronization deadline and per-PDF size limit;
  default to 300 seconds and 1 GiB.

- **`MAX_SKILL_BYTES`** - maximum size of one complete built-in or Workspace
  skill returned to IDEA; defaults to 100,000 bytes. Larger skills fail
  explicitly and are never partially loaded.

- **`MAX_SKILL_COMPONENT_BYTES`** - maximum size of one hierarchical package
  root, reference, component, or manifest; defaults to `MAX_SKILL_BYTES`.

- **`MAX_SKILL_BUNDLE_BYTES`** - maximum aggregate size of one resolved
  hierarchical package bundle; defaults to 200,000 bytes.

- **`MAX_SKILL_COMPONENTS`** - maximum declared package documents (excluding
  the root); defaults to 32.

- **`MAX_SKILL_DEPENDENCY_DEPTH`** - maximum package dependency depth;
  defaults to 16.

## Status

This runs *alongside* the existing custom frontend (`frontend/`, `nginx`,
`app.py`) rather than replacing it - both currently point at the same
`langgraph` + `sandbox` backend. See `langgraph/IMPLEMENTATION_STATUS.md`
for the underlying agent/sandbox architecture this depends on.

**Context and interruption behavior:**
- Open WebUI's selected, compacted branch is the authoritative conversation
  history. The Pipe preserves user/assistant roles, sends conversation
  summaries as context rather than Assistant policy, and removes legacy
  inline generated-image bytes before LangGraph model assembly.
- LangGraph stores bounded execution memory in PostgreSQL checkpoints.
  Redis maps each Open WebUI assistant message ID to its graph thread and
  checkpoint, preserving the correct execution branch across edits and
  regenerations without requiring custom frontend event support. Mappings
  expire after one year by default and refresh when used.
- Open WebUI Stop requests cooperatively cancel the active model or sandbox
  operation and preserve completed checkpoints.
- A complete preflight token budget covering provider-specific tool schemas
  remains future work; the bounded execution-memory block and Open WebUI's
  136,000-token compaction threshold currently provide conservative headroom.
- Chat deletion does not yet eagerly remove PostgreSQL checkpoints or Redis
  message mappings; mappings expire according to
  `IDEA_CHECKPOINT_MAP_TTL_SECONDS`.
- Still not handled by the Pipe function: mapping Open WebUI's
  guest/pending-approval users to this repo's own guest-model/guest-scrutiny
  policies beyond the basic `is_guest` flag.
