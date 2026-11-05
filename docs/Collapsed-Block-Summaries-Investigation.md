# Task-model summaries for collapsed code and output

Investigation date: November 2, 2026. This is a proposed implementation;
application behavior and saved user settings have not been changed.

## Findings in the current integration

The IDEA Open WebUI fork already contains presentation scaffolding and a design
note in `docs/collapsed-block-summaries.md`. The source inspected was the
`idea-open-webui-v0.12.5-idea.0.11` checkout at `c600c4437`, matching the release
pinned by IDEA's wrapper.

`CodeBlock.svelte` accepts `collapsedSummary` and `contentComplete`. Its helper,
`collapsedBlockSummary.ts`, displays summary text only when the message is
complete and the supplied source exactly matches the displayed block. Otherwise
it displays the hidden-line count. Svelte renders the summary as escaped text.
`Markdown.svelte` and `MarkdownTokens.svelte` carry an optional summary map, but
no provider populates it. There is no generation endpoint, durable cache, or
summary preference yet. Existing helper tests cover valid, stale, missing,
blank, and incomplete summaries; those are presentation tests only.

| Content | Current rendering | Initial feature coverage |
| --- | --- | --- |
| IDEA Python code and ordinary fenced code | `CodeBlock.svelte` | Yes |
| IDEA Python console output, including fenced execution errors | Fenced `output`, using `CodeBlock.svelte` | Yes |
| General tool failures emitted by the Pipe | Warning plus indented Markdown | Needs separate treatment; does not use the same collapsible fence |
| Native Open WebUI tool results | `ToolCallDisplay.svelte` and structured/details renderers | Separate integration |
| Document previews | Code blocks explicitly expanded | Preserve current document behavior |

The relevant IDEA formatting is in `openwebui/functions/idea_pipe.py`, especially
`Pipe._translate_chunk`. Some general console text is already bounded by
`IDEA_MAX_UI_GENERAL_CONSOLE_BYTES` (default 4,000 bytes). A summary of that text
must not imply access to the omitted output.

## Recommended initial behavior

Collapse code and fenced output by default. For completed messages, replace the
line count with a plain-text summary, usually one to three sentences. Describe
what code does, or the substantive result/error in output. Describe code's intent
without claiming it executed successfully; execution claims require output.
Keep the original content unchanged and available on expansion/copy/download.

Generate asynchronously after a message finishes, when its collapsed blocks are
displayed. Keep the existing line count until the summary is ready or if
generation fails. Do not delay the agent response, repeatedly summarize on
rerender, or generate for every streamed token. Use bounded batches of blocks
from one message to reduce requests; give each block its own returned summary.

The current `contentComplete={done}` gate waits for the whole message. If summaries
should appear while IDEA continues working, a later step must identify individually
closed blocks and replace that gate with per-block completion. This requires
handling streamed fences and edits explicitly; the existing scaffold alone does
not provide that behavior.

## Settings

The existing personal interface setting `collapseCodeBlocks` controls both fenced
code and fenced output. No second collapse preference is required for that scope.
Set the administrator's persisted `DEFAULT_INTERFACE_SETTINGS` to include:

```json
{"collapseCodeBlocks": true}
```

Extend `deployment/post_deploy/configure_openwebui.py` to read the existing
`/api/v1/auths/admin/config`, merge this key into its defaults, and update the full
admin configuration without dropping unrelated values. Environment defaults alone
may not replace persisted configuration.

`routers/users.py` merges these defaults with each user's saved UI settings.
Unset/null values inherit the default; an explicit saved `false` remains false.
This implements a default without overwriting preferences. Requiring every
existing user to collapse blocks would instead require an intentional settings
migration. Add a separate, typed summary-generation preference so users can keep
collapse behavior while disabling summaries. Enable summaries for IDEA's rollout
after validation; keep an administrator switch to disable generation.

## Backend and frontend work

1. Add an authenticated endpoint in `backend/open_webui/routers/tasks.py`, for
   example `POST /api/v1/tasks/block_summaries/completions`. For persisted chats,
   accept chat/message IDs and requested block identities; authorize the saved
   message and extract the actual source on the server. Reject incomplete or
   changed content. Do not trust a client-supplied source or replacement model.
2. Reuse `get_task_model_generation_config`, `apply_task_model_params`, and the
   ordinary task completion path. IDEA configures `TASK_MODEL_EXTERNAL` to
   `gpt-6-luna` via LiteLLM. Add an explicit check that the resolved model is a
   usable task model: `get_task_model_id` otherwise falls back to the chat model,
   which could recursively invoke IDEA's Pipe. Generate without tools, terminal
   selection, attached collections, or inherited chat-execution metadata.
3. Give each block a durable identity scoped to its authorized chat and message,
   including source hash, language/kind, and occurrence/location. Include prompt
   version and resolved task-model/config fingerprint in cache keys. The current
   `${id}-${tokenIdx}` render key is only a frontend mapping; token indices shift
   with edits, nested Markdown, regeneration, and formatting. Reconcile server
   extraction with frontend parsing, including IDEA's four-backtick fences and
   fence-containment preprocessing.
4. Store a persistent, bounded cache separately from message text, with message
   deletion cleanup and invalidation by source hash. A dedicated cache table is
   preferable to blindly attaching metadata: existing chat-history metadata merge
   rules preserve only selected keys on some updates and could discard summaries.
   If using `message.meta` instead, extend and test those merge rules and both the
   chat-history JSON and normalized `ChatMessage` persistence paths.
5. Add frontend request deduplication, cancellation, and caching at message level.
   Map cached summaries into the existing Markdown summary prop and carry it
   through `ContentRenderer.svelte` and any structured-output paths in scope.
   Verify source identity again before displaying a late response. Keep summaries
   out of subsequent model prompts, PaperQA evidence, and compaction history.
6. Handle temporary chats through a separate authenticated, bounded content path
   with session caching, since there may be no saved message to authorize/extract.
   Shared views may display already-generated summaries when allowed by existing
   share access, but anonymous readers must not initiate generation. Define how
   summaries are copied into share snapshots and exported/imported chats.

Bound input and output sizes, execution time, per-user concurrency, and retries.
Deduplicate backend work across tabs/workers as well as within a browser.
Treat code/logs as data, including any instructions embedded within them. Ask for
concise factual summaries in the user's interface language. Validate returned
block IDs and text, render plain text, and fall back independently for failed
blocks. Sentence count should be a prompt target with a reasonable length cap;
naive punctuation splitting would damage filenames, numbers, and abbreviations.

## Delivery and validation

Implement the endpoint/cache, frontend provider, and renderer plumbing against
the IDEA Open WebUI source fork. The existing frontend can display a supplied
summary, but needs additional code to request summaries and pass them to the
renderer. Because Open WebUI serves compiled JavaScript, those changes require
rebuilt frontend assets. A separately published Open WebUI image is optional.

There are two packaging options:

- Publish a new pinned IDEA Open WebUI image containing the backend changes and
  rebuilt frontend assets, then update IDEA's Dockerfile pin.
- Keep the existing pinned base image and package the rebuilt frontend assets
  and matching backend changes in IDEA's Docker wrapper. Build the assets from
  the matching source revision, then copy the generated `build` directory to
  `/app/build` alongside the backend additions. This produces a customized IDEA
  image without requiring a separately published base-image release. The build
  must include a reproducible frontend compilation step or explicitly prepared
  assets; editing Svelte source alone does not update the served interface.

Update IDEA's configuration script for either option. The current wrapper
patches backend middleware only. Retain the Knowledge-status and internal-sandbox
tool fixes in the new release or continue applying their compatible wrapper
patch, and validate frontend/backend compatibility with the selected base.

Acceptance checks should cover completed code/output, short and long blocks,
mixed three/four-backtick fences, nested blocks, duplicate sources, streaming,
edits/regeneration/branches, stale in-flight responses, refresh and cache reuse,
temporary chats, share snapshots, settings inheritance and explicit overrides,
cross-user access denial, model fallback rejection, timeout/failure recovery, and
preservation of original bytes. Confirm no extra sandbox calls, no citations
created from summaries, and no summary text in the agent's next input.

Measure Task-model requests/tokens per message, cache hit rate, summary latency,
failure rate, and accuracy on representative code, successful output, and errors.
With collapse enabled by default, most displayed messages containing blocks will
trigger generation; batching and caching therefore materially affect cost and
latency. No live Task-model experiment, application implementation, settings
change, or deployment was performed during this investigation.
