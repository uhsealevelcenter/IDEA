# Recent Changes

## Changes in `feature/langgraph-memory`

This listing compares `feature/langgraph-memory` with its parent branch,
`next-dev`, at merge base `e6dc507`. It covers the 18 commits currently unique
to this branch.

1. The agent loop is now a checkpointed LangGraph state graph with explicit model, tool, finalization, and cancellation stages instead of relying on an in-process linear loop for durable state.
2. LangGraph checkpoints use a dedicated least-privilege PostgreSQL role and schema, with deployment setup scripts and optional AES encryption for persisted checkpoint data.
3. Open WebUI's selected, compacted conversation branch is now authoritative model context. Assistant-message-to-checkpoint mappings preserve the correct execution branch across edits and regenerations without restoring a competing Redis conversation transcript.
4. User, chat, Assistant, workspace, kernel, thread, checkpoint, and run identities are derived separately so persistent files, Python state, conversation branches, and individual responses have the intended isolation boundaries.
5. Chat runs execute as durable background jobs with persisted, sequence-numbered events. Open WebUI can poll after a disconnect, recover terminal results, and avoid duplicating streamed errors; event storage is compacted to limit Redis growth.
6. Stop requests now cooperatively cancel queued work, active model streams, and sandbox execution while retaining completed checkpoint state. PaperQA setup is deferred until it is actually needed so cancellation and ordinary turns do not pay its initialization cost.
7. Tool execution memory now records bounded action ledgers, Python source and namespace metadata, artifacts, warnings, and error summaries. Repeated identical calls are blocked, oversized outputs can be archived and paged, and model context receives compact tool-history summaries.
8. Resumed microVMs lazily restore Open Terminal, and persistent-kernel behavior is more reliable across service restarts without routinely recreating users' writable sandboxes.
9. Python source streams into the chat while the model generates it, before kernel execution begins. Open WebUI renders one code block and suppresses the backward-compatible completed-code replay.
10. Python-generated plots are captured automatically, persisted as durable sandbox artifacts, synchronized to Open WebUI, displayed inline without base64 history growth, and supplied to model vision when appropriate.
11. Visual follow-ups can restore the most recent relevant image, distinguish display from model inspection, avoid duplicate previews, and route image-bearing model requests consistently through the configured vision-capable model.
12. Model-call telemetry now records provider token usage, cached and reasoning token details when available, model-visible text size, image counts, and run totals. Stable per-conversation prompt-cache keys were added; the documented remaining limitation is LiteLLM forwarding of `prompt_cache_options` to the upstream endpoint.
13. Agent reliability and context efficiency were improved through bounded retries, safer tool dispatch, heartbeat and progress events, output-finalization recovery, streamlined prompts, and more compact skill and tool results.
14. Open WebUI's separate native Code Execution and Code Interpreter runtimes are disabled for IDEA to prevent confusion with the persistent sandbox kernel, and the documentation now clarifies supported math-delimiter formatting.
15. Deployment-managed Assistants now receive curated suggested prompts, including domain-specific SEA and Mars prompts and narrowly scoped Welcome-prompt reconciliation for selected existing Assistants.
16. Climate-index parsing is compatible with pandas 3 while preserving the existing climate-data workflow and test coverage.
17. The primary agent model is centrally selected through `IDEA_AGENT_MODEL`, defaults to `gpt-5.6-terra`, and retains `gpt-5.6-sol` as a one-variable rollback through the same LiteLLM endpoint and credentials.
18. Python kernel failures retain structured error metadata through the kernel, sandbox, and LangGraph layers, are marked as failed tool executions, and render in Open WebUI as labeled traceback blocks without marking the entire assistant response as failed.

## Previous change listing

This summary compares `idea-next/system-prompt-AND-functions` with its parent
branch, `cleanup/ideav2_migration` (`b02913f`), and intentionally lists only
major changes.

1. IDEA now uses the newest pinned UHSLC Open WebUI customization, incorporating the interface and integration changes needed by the IDEA deployment.
2. Official Welcome, SEA, and Mars Assistants now preserve the legacy prompts and branding while being deployed and managed through Open WebUI.
3. The user-facing base model is named **IDEA Agent**, replacing **IDEA Terminal Agent**, while stable internal function and model identifiers retain their existing machine-readable names for compatibility.
4. The main agent model is selected by `IDEA_AGENT_MODEL` (`gpt-5.6-terra` by default, with `gpt-5.6-sol` retained as a one-variable rollback), while a hidden `gpt-5.6-luna` task model handles Open WebUI titles and other auxiliary work through LiteLLM.
5. Deployment configuration now reconciles Open WebUI context compaction, token limits, title generation, Assistant permissions, and shared Workspace feature policies.
6. Data uploaded through Open WebUI is authorized and copied without format changes into each user's persistent private sandbox, with exact file paths supplied to the agent for analysis.
7. Generated artifacts are synchronized back to Open WebUI with reusable links, and browser-safe HTML and image previews are supported.
8. Uploaded and sandbox images can now be supplied directly to the vision-capable IDEA agent, with configurable size and count limits.
9. A centrally maintained shared scientific-data volume is mounted read-only in every user sandbox, and climate tools write full datasets and provenance directly into the user's workspace.
10. IDEA now has unified skill discovery and loading for built-in and user Workspace skills, including validated hierarchical packages and the ported CIndRA skill suite.
11. PaperQA2 is integrated with Open WebUI Assistant knowledge collections and direct PDF attachments, with cited visual contexts returned alongside literature answers.
12. Chat responses, tool progress, and output-finalization status now stream through Open WebUI so long-running work remains visible to the user.

## Major TODOs

1. Make Open WebUI's branch-aware, compacted message history the authoritative conversation context and retire the competing durable LangGraph transcript in Redis.
2. Separate persistent user workspaces from per-chat Python kernels and per-response run identities so concurrent conversations cannot leak or overwrite in-memory state.
3. Add a true cancellation endpoint so Open WebUI's Stop action can interrupt an active LangGraph/tool run safely.
4. Finish production hardening for guest and pending users, LiteLLM prompt/completion retention, transport heartbeats, sandbox-image pinning, backups, and non-destructive sandbox upgrades.
5. Provision and map the `next-dev` environment in the deployment workflow, then complete production-like smoke, persistence, recovery, concurrency, attachment, PaperQA, and security testing before promotion.

## Minor TODOs

1. Integrate the Codex CLI into the IDEA agent and sandbox workflow.
2. Add cron-based scheduling for routine scientific-data updates inside the user VM environment.
3. Reconcile and clean up inconsistencies between the LangGraph implementation-status and integration-plan documents.
