# LangGraph integration and rollout plan

Last reviewed: 2026-08-10 on `feature/langgraph-memory`.

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
- Set `IDEA_AGENT_MODEL=gpt-5.6-sol` to roll back the primary model through the
  same LiteLLM route.
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
