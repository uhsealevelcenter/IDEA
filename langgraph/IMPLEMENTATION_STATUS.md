# LangGraph implementation status

Last reviewed: 2026-08-10 on `feature/langgraph-memory`.

## Current status

The Open WebUI `/chat-runs` path uses the checkpointed LangGraph runtime by
default (`IDEA_AGENT_RUNTIME=langgraph`). The earlier linear
`ConversationOrchestrator` path remains for rollback and the legacy `/chat`
endpoint, but it is not the normal IDEA request path.

| Area | Status | Current behavior |
| --- | --- | --- |
| Conversation context | Complete | Open WebUI supplies the authoritative selected and compacted branch; LangGraph does not maintain a competing durable transcript for `/chat-runs`. |
| Graph execution | Complete | Explicit prepare, model, one-tool, cancellation, stopped-summary, and finalization nodes are checkpointed. |
| Durable memory | Complete | PostgreSQL stores bounded action, Python, dataset, artifact, continuation, usage, and Codex-thread state; optional AES encryption is supported. |
| Branch handling | Complete | Redis maps visible assistant message IDs to checkpoint IDs so edits and regenerations resume the correct branch. |
| Identity isolation | Complete | Conversation thread, per-user workspace, configurable kernel scope, and per-response run IDs are derived independently with an HMAC secret. |
| Chat-run events | Complete | Status and bounded, sequence-numbered events are persisted in Redis and can be polled after browser disconnects. |
| Cancellation | Complete | Stop requests reach queued/model/tool execution and signal Python or Codex work in the sandbox while preserving completed checkpoint state. |
| Sandbox execution | Complete for Linux/KVM | A singleton authenticated sandbox service owns per-user microsandbox VMs, persistent files, kernel processes, and execution locks. |
| Python and images | Complete | Source streams before execution; kernel state is persistent by configured scope; plots are persisted, displayed, and supplied to model vision. |
| Attachments and artifacts | Complete | Inputs are re-authorized and copied into the private workspace; `/outputs` artifacts are uploaded under the current user's Open WebUI credential. |
| Skills and PaperQA | Complete | Built-in and Workspace skills are supported; authorized non-guests can query Assistant knowledge and direct PDF attachments. |
| Model routing | Complete | `IDEA_AGENT_MODEL` defaults to `gpt-5.6-terra`; regular model traffic uses LiteLLM with end-user attribution and telemetry. |
| Codex delegation | Developer rollout complete | Codex runs inside the same VM, supports read-only/workspace-write, resumes threads, and participates in Stop. It currently reuses external `OPENAI_*` credentials when dedicated values are blank. |
| Guest image | AMD64 candidate published and tested | The research image includes the legacy analysis stack, current CIndRA-oriented additions, Codex, GuardDog, and local/microVM smoke tests. Multi-architecture production publication remains a GitHub workflow task. |

## Storage ownership

- Open WebUI owns conversational messages, branches, summaries, Assistant
  selection, uploaded files, and published output files.
- PostgreSQL's `idea_langgraph` schema owns LangGraph checkpoints.
- Redis owns transient run coordination/events and message-to-checkpoint
  mappings; it is not the `/chat-runs` conversation database.
- Microsandbox owns writable workspace and kernel state.

The old `langgraph/db/conversation_crud.py` integration and the database code
inside `multi_agent.py` are legacy/dead paths. They should not be used as a
description of production persistence and can be removed after the manual
runtime rollback path is retired.

## Remaining production gates

1. Publish and record the immutable multi-architecture microsandbox manifest
   through the protected GitHub workflow; deploy by immutable tag or digest.
2. Provision the `next-dev` GitHub Environment and run production-like smoke,
   restart/recovery, concurrency, attachment, image, PaperQA, Codex, and
   security checks.
3. Replace destructive microVM recreation with a tested snapshot/restore or
   other non-destructive workspace migration before admitting non-developer
   users.
4. Route Codex through a guest-reachable TLS LiteLLM Responses API endpoint
   with a separate revocable, model-restricted, low-budget virtual key.
5. Decide and enforce LiteLLM prompt/completion retention appropriate for
   potentially sensitive conversations and images.
6. Verify or patch LiteLLM forwarding of `prompt_cache_options`; current
   telemetry proves provider cache reads but not explicit-only cache depth.
7. Replace in-process chat-run worker threads with a durable task queue if
   active runs must survive LangGraph process/container failure.
8. Establish backup/restore procedures and production monitoring for Open
   WebUI data, checkpoints, Redis coordination, and user workspaces.

## Validation references

- Focused graph, cancellation, event, output, vision, and Codex tests are
  listed in [`README.md`](README.md).
- Guest-image build, microVM validation, publication, and rollout are in
  [`../interpreter_kernel/README.md`](../interpreter_kernel/README.md).
- The dependency findings and recheck process are in
  [`../interpreter_kernel/DEPENDENCY_AUDIT.md`](../interpreter_kernel/DEPENDENCY_AUDIT.md).
- Codex architecture and credential policy are in
  [`../docs/Codex-Integration.md`](../docs/Codex-Integration.md).
