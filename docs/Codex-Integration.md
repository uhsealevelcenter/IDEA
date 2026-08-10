# Codex integration

Codex is implemented as a delegated coding runtime, not as a second
LangGraph graph and not as a replacement for IDEA's primary model.

LangGraph decides when to call the model-facing `delegate_to_codex` tool and
checkpoints the returned Codex thread ID by working directory. The sandbox
service launches the pinned `openai-codex` SDK and its bundled CLI inside the
same per-user microsandbox VM that owns `/workspace`. Codex therefore sees the
same files as the terminal and Python tools without receiving host filesystem
access. The existing run interrupt route signals the active Codex turn as well
as Python execution.

## Model and endpoint policy

`IDEA_CODEX_MODEL` defaults to `IDEA_AGENT_MODEL`, so both can use the same
model alias, but it remains independently configurable. During the current
developer-only rollout, blank `IDEA_CODEX_BASE_URL` and `IDEA_CODEX_API_KEY`
values fall back to `OPENAI_BASE_URL` and `OPENAI_API_KEY`. This lets Codex use
the same external OpenAI-compatible endpoint and provider credential as IDEA;
it deliberately bypasses LiteLLM budgeting and per-user attribution.

TODO (low priority before non-developer rollout): expose a private,
TLS-protected LiteLLM Responses API address that microVM guests can reach, and
set the dedicated Codex variables to that URL plus a separate, revocable,
model-restricted, low-budget virtual key. Docker's `http://litellm:8080`
service name is only available on the Compose network. Never use the LiteLLM
master key or IDEA's shared conversation virtual key for Codex.

Whichever credential is selected is absent from the model's tool schema and
command line. It is passed by LangGraph through the authenticated sandbox API,
written to a short-lived request file in the VM, exposed to the child process
through its environment, and removed after the turn.

## Security defaults

- The feature defaults to enabled, but its tool is only registered when a key
  and guest-reachable base URL are configured. Set
  `IDEA_CODEX_ENABLED=false` for an explicit rollback.
- Delegation fails closed when the sandbox backend is the local host fallback.
- Only `read-only` and `workspace-write` are accepted; full access is not.
- The working directory must resolve to `/workspace` or a descendant.
- Approval mode is `deny_all`; Codex cannot expand its own permissions.
- Codex's shell environment explicitly filters out `IDEA_CODEX_API_KEY`, so
  model-generated commands cannot read the credential used by app-server.
- Codex state is stored under `/workspace/.idea/codex` in the user's VM.

## Deployment

1. Build and publish `interpreter_kernel/`, which installs
   `openai-codex==0.144.4` and its pinned CLI runtime.
2. Roll the new guest image out according to `interpreter_kernel/README.md`.
3. For developer testing, leave the default `IDEA_CODEX_ENABLED=true` and the
   dedicated endpoint/key blank to reuse the external `OPENAI_*` values.
4. Before non-developer rollout, provision the restricted virtual key and a
   guest-reachable LiteLLM URL, then set the dedicated Codex overrides.
5. Restart LangGraph.
6. Smoke-test a read-only repository summary, a workspace-write edit with a
   focused test, thread resumption, Stop/cancellation, and key redaction in
   service logs and persisted graph actions.

The current sandbox shell transport returns SDK events after the child process
finishes, so the UI shows a live "Codex is working" status and a completion
event rather than token-by-token Codex progress. Cancellation remains live
because it uses an independent sandbox filesystem signal.
