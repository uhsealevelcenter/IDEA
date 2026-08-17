# Recent Changes

## Work started in `debug/ui-langgraph-general`

1. Open WebUI is updated to the `v0.11.0-idea.0.8` customization release.
   Compose pins its published multi-platform OCI index digest.
2. Every model-visible tool observation now has a hard configurable byte
   ceiling, including the newest two. The bound is applied before checkpoint
   persistence and again while assembling model input; the manual rollback
   loop has the same protection.
3. IDEA's system instructions now call for concise plans and milestone updates
   during complex work, without narrating every command. They also discourage
   Python worker pools until Stop can terminate them safely without locking or
   killing the persistent kernel.
4. The primary conversation model now defaults to `gpt-5.6-sol`. Auxiliary
   station/web inference, Codex, and PaperQA remain explicitly on
   `gpt-5.6-terra`; Open WebUI task generation remains on `gpt-5.6-luna`.

## Changes in `feature/langgraph-memory`

This listing compares `feature/langgraph-memory` with its parent branch,
`next-dev`, at merge base `e6dc507`. It covers the 23 commits currently unique
to this branch. See [`Recent-Changes-Details.md`](Recent-Changes-Details.md) for
the developer-oriented, file- and function-level description of the resulting
code.

1. The agent loop is now a checkpointed LangGraph state graph with explicit model, tool, finalization, and cancellation stages instead of relying on an in-process linear loop for durable state.
2. LangGraph checkpoints use a dedicated least-privilege PostgreSQL role and schema, with deployment setup scripts and optional AES encryption for persisted checkpoint data.
3. Open WebUI's selected, compacted conversation branch is now authoritative model context. Assistant-message-to-checkpoint mappings preserve the correct execution branch across edits and regenerations without restoring a competing Redis conversation transcript.
4. User, chat, Assistant, workspace, kernel, thread, checkpoint, and run identities are derived separately so persistent files, Python state, conversation branches, and individual responses have the intended isolation boundaries.
5. Chat runs execute as background jobs with durable, sequence-numbered events. Open WebUI can poll after a disconnect, recover terminal results, and avoid duplicating streamed errors; event storage is compacted to limit Redis growth. Active workers remain in-process and do not survive a LangGraph container failure.
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
17. The primary agent model is centrally selected through `IDEA_AGENT_MODEL`; the later `debug/ui-langgraph-general` work changes its default from `gpt-5.6-terra` to `gpt-5.6-sol` while keeping auxiliary models separate.
18. Python kernel failures retain structured error metadata through the kernel, sandbox, and LangGraph layers, are marked as failed tool executions, and render in Open WebUI as labeled traceback blocks without marking the entire assistant response as failed.
19. IDEA can now delegate substantial repository investigation, implementation, debugging, and review to Codex inside the user's existing microsandbox workspace. LangGraph remains the conversation orchestrator, checkpoints resumable Codex threads, applies read-only or workspace-write policy, and propagates Stop requests to active Codex turns.
20. The microsandbox guest image now includes the pinned Codex runtime and a reviewed research software environment that restores the broadly useful legacy IDEA analysis stack while adding commonly needed CIndRA, document, OCR, browser, geospatial, and ocean-data packages. Local and microVM smoke tests, dependency auditing, immutable multi-architecture publication, and an explicitly destructive developer-only refresh workflow are documented.

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

1. Finish production hardening for guest and pending users, LiteLLM prompt/completion retention, sandbox-image pinning, backups, and non-destructive sandbox upgrades.
2. Finish provisioning the mapped `next-dev` GitHub Environment and complete production-like smoke, persistence, recovery, concurrency, attachment, PaperQA, Codex, and security testing before promotion.
3. Implement confirmed, escalating Stop and kernel/OOM recovery, including
   safe handling of Python worker pools without leaving the persistent kernel
   locked or requiring a whole `idea_sandbox` restart.
4. Decide Microsandbox capacity and admission policy: configurable CPU/RAM
   tiers (1 GiB is insufficient for observed workloads but local hosts may be
   constrained), a waiting/onboarding experience and possibly smaller
   sandboxes when capacity is busy, and a production “No Sandbox available”
   fallback.

## Minor TODOs

1. Add cron-based scheduling for routine scientific-data updates inside the user VM environment.
2. Route Codex through a guest-reachable LiteLLM endpoint with its own model-restricted, low-budget virtual key instead of the temporary developer-stage `OPENAI_*` credential fallback.
3. Archive oversized Python observations with a model-visible paging reference
   and add a final whole-prompt size preflight in addition to the per-tool hard
   limit.
