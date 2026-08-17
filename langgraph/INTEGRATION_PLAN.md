# LangGraph integration and rollout plan

Last reviewed: 2026-08-17 on `debug/ui-langgraph-general`.

The original version of this document described an Open Interpreter adapter,
Redis conversation transcripts, and direct `app.py` integration. That design
predates the current Open WebUI Pipe, checkpointed graph, standalone sandbox
service, and branch-aware context contract. It is retained in Git history but
must not be used as the implementation plan for the current system.

## Implemented architecture

The integration boundary is now:

```text
Open WebUI conversation and Assistant
                |
                v
       IDEA Pipe /chat-runs
                |
                v
  checkpointed LangGraph runtime ---- PostgreSQL checkpoints
        |                 |
        |                 +---------- Redis status/events + branch map
        v
 authenticated sandbox service
        |
        v
 per-user microsandbox workspace
   | persistent Python kernel
   | Open Terminal helpers
   + on-demand Codex runtime
```

Open WebUI owns conversation history. LangGraph owns execution decisions and
bounded durable action memory. The sandbox service owns all user-code process
state. Codex is a LangGraph tool operating inside the existing workspace, not
a separate graph or a replacement primary model.

## Branch completion plan

Before merging this feature branch into `next-dev`:

1. Keep the focused automated tests green, including checkpoint branching,
   cancellation, event compaction, attachments, vision, and Codex.
2. Review the final diff for secrets, generated build products, local `.env`
   changes, and accidental image tags.
3. Confirm `example.env`, the quick-deploy guide, Codex guide, guest-image
   guide, and this LangGraph documentation agree on defaults and rollback
   switches.
4. Push the branch and use the normal pull-request/CI review path.

## Microsandbox image rollout

1. Test the image locally with `interpreter_kernel/test_image.sh`.
2. On Linux/KVM, test one disposable VM with
   `interpreter_kernel/test_microsandbox_image.sh`.
3. Run the protected **Microsandbox image** GitHub workflow with an immutable
   version and `publish=true`. The workflow must build both amd64 and arm64,
   run validation, and record the manifest digest and attestations.
4. Set `SANDBOX_IMAGE` on the deployment host to the immutable tag or digest.
5. During developer-only testing, existing workspaces may be recreated with
   `refresh_sandboxes.sh --allow-destructive-developer-refresh` after an
   explicit decision to discard them.
6. Before non-developer users exist, replace step 5 with a rehearsed,
   non-destructive workspace migration and rollback process.

Local publication of only amd64 was useful for developer testing, but it does
not complete the production multi-architecture release. ARM64 should be built
by the remote workflow, not on the development workstation.

## Reliability and capacity decisions requiring review

The localized model-context fix is implemented: every `ToolMessage` is bounded
before checkpointing and again at the LLM boundary. The remaining reliability
work should be reviewed as one design because Stop escalation, kernel recovery,
and capacity admission affect the same sandbox lifecycle:

1. Add a final assembled-prompt preflight and archive oversized Python output
   with an execution/path reference that the model can page. The per-message
   ceiling prevents one print from overflowing context, but it is not a total
   prompt budget.
2. Replace cooperative-only Stop with observable escalation: interrupt,
   confirm within a short grace period, replace only the affected kernel, then
   recycle only that user's microVM if recovery fails. Add dead-kernel/OOM
   detection and an execution deadline outside user Python.
3. Support Python thread/process pools only after Stop can terminate pooled
   work without waiting indefinitely in an executor context manager, killing
   healthy persistent state unnecessarily, or retaining the sandbox execution
   lock. Until then, system instructions advise sequential or single-worker
   execution.
4. Benchmark realistic scientific workloads before changing
   `SANDBOX_MEMORY_MB`/CPU defaults. Evaluate at least a low-resource local tier
   and a production tier; 1 GiB has already failed, but globally increasing the
   default can make local multi-user testing unusable.
5. Define admission behavior when production capacity is exhausted: whether
   users wait during onboarding, whether busy periods offer a smaller sandbox,
   how workspaces move between tiers, and whether a clear “No Sandbox
   available” read-only/no-execution fallback is required.

The currently observed emergency recovery is
`docker compose exec sandbox msb restart idea_sandbox`, with a sandbox
container restart as the fallback when the CLI cannot recover it. This must be
verified against the actual per-user Microsandbox name before becoming a
runbook; either form is an operational workaround, not the target user-facing
recovery path.

The recent reduction of successful tool-result text in the chat UI remains a
deferred presentation decision. Reassess it with real workflows before either
restoring broader tool output or making the compact status-only behavior the
long-term default; preserve full observations for the agent independently of
what the UI displays.

## Inference routing status

Inference does not yet pass universally through LiteLLM. Primary IDEA chat
(`gpt-5.6-sol`), PaperQA (`gpt-5.6-terra`), embeddings, and Open WebUI's hidden
task model (`gpt-5.6-luna`) use the internal LiteLLM proxy. Station lookup and
web-search helper inference currently use the provider's OpenAI-compatible
endpoint directly. Codex also uses a direct `OPENAI_*` fallback during the
developer rollout. Consequently, LiteLLM budgeting and end-user attribution
do not yet cover those helper and Codex calls; routing them through restricted
LiteLLM endpoints remains rollout work.

## Codex rollout

Codex is enabled by default but is registered only when a usable endpoint and
credential resolve and the rebuilt guest image is deployed. For the current
developer stage, blank dedicated variables reuse `OPENAI_BASE_URL` and
`OPENAI_API_KEY`.

Before non-developer rollout:

1. Expose a private, TLS-protected LiteLLM Responses API URL reachable from a
   microsandbox guest.
2. Issue Codex a separate revocable virtual key restricted to the intended
   model and a low budget; do not reuse the LiteLLM master key or IDEA's
   shared conversation key.
3. Set the dedicated `IDEA_CODEX_BASE_URL` and `IDEA_CODEX_API_KEY` values,
   then verify key redaction from tool schemas, logs, checkpoints, child shell
   environments, and persisted events.
4. Smoke-test read-only investigation, workspace-write implementation with a
   focused test, thread resumption, invalid working directories/access modes,
   and Stop/cancellation.

## Production promotion gates

- The `next-dev` GitHub Environment has its deployment variables, protected
  secrets, reviewers, DNS/TLS route, and smoke URL configured.
- Database roles and checkpoint encryption are initialized and backed up.
- The deployed guest image is pinned immutably and available for every host
  architecture.
- Restart/recovery and concurrent-chat behavior are tested, including the
  documented limitation that an in-process active worker does not survive a
  LangGraph container failure.
- Attachment authorization, output ownership, guest/pending-user policy,
  PaperQA isolation, model/credential redaction, and LiteLLM retention have
  explicit acceptance results.
- A non-destructive sandbox upgrade and rollback procedure exists before any
  persistent non-developer workspace is created.

## Rollback

- Set `IDEA_CODEX_ENABLED=false` and restart LangGraph to remove Codex without
  changing the main agent.
- Set `IDEA_AGENT_MODEL=gpt-5.6-terra` to roll back the primary model through
  the same LiteLLM route. `IDEA_TOOL_MODEL` remains independently configurable.
- Set `IDEA_AGENT_RUNTIME=manual` only as a short-lived emergency rollback;
  that legacy path does not provide the checkpointed graph's current memory,
  branch, and cancellation semantics.
- Roll a sandbox image back by pinning the previous immutable digest. Do not
  recreate persistent user VMs destructively once non-developer workspaces
  exist.

Current implementation facts and remaining work are summarized in
[`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md); operational details are
in [`README.md`](README.md) and
[`../docs/Quick-Deploy.md`](../docs/Quick-Deploy.md).
