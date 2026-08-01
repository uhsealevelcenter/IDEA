# Recent Changes

This summary compares `idea-next/system-prompt-AND-functions` with its parent
branch, `cleanup/ideav2_migration` (`b02913f`), and intentionally lists only
major changes.

1. IDEA now uses the newest pinned UHSLC Open WebUI customization, incorporating the interface and integration changes needed by the IDEA deployment.
2. Official Welcome, SEA, and Mars Assistants now preserve the legacy prompts and branding while being deployed and managed through Open WebUI.
3. The user-facing base model is named **IDEA Agent**, replacing **IDEA Terminal Agent**, while stable internal function and model identifiers retain their existing machine-readable names for compatibility.
4. The main agent now uses `gpt-5.6-sol`, while a hidden `gpt-5.6-luna` task model handles Open WebUI titles and other auxiliary work through LiteLLM.
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
