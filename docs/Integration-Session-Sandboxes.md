# Integration session sandboxes (public n8n chat)

Each public n8n conversation runs in its own temporary microVM, owned by the dedicated Open WebUI integration account. Regular IDEA browser chats keep the existing per-user VM. Design rationale: `IDEA-n8n-sandbox-implementation-guide.md`.

## Request envelope

Use the Pipe model `idea-terminal-agent-integration` (enable the Pipe valve `INTEGRATION_MODEL_ENABLED`, restrict model access to the integration account, and list that account's user ID in the valve `INTEGRATION_ACCOUNT_IDS` so every request it makes, including to Assistants used as specialists, must carry session context and runs in that session's VM). Every request to it must carry the Chat Trigger `sessionId`, unchanged, through **one** of:

| Transport | Field |
|---|---|
| HTTP header | `X-IDEA-Integration-Session: <sessionId>` |
| Body | `"idea_integration": {"integration_session_id": "<sessionId>"}` |
| Body metadata | `"metadata": {"integration_session_id": "<sessionId>"}` |

Optional: `X-IDEA-Integration-Id` / `integration_id` must equal the Pipe valve `INTEGRATION_ID` when present. Conflicting values across transports are rejected.

Session IDs are opaque: 1–256 UTF-8 bytes, no control characters, never trimmed or normalized. A missing or invalid session on the integration model, or a session sent to a browser model, fails before any run or VM starts.

**End a conversation** (website "End" / "New conversation") by sending the same model the same session with `X-IDEA-Integration-Action: end` (or `"action": "end"` in `idea_integration`). Later messages with that session receive "This conversation has ended."

### Transport spike (must be done on the deployed versions)

The n8n model nodes may not be able to send per-request headers or body fields built from expressions. Before relying on either transport:

1. `curl` Open WebUI's `/api/chat/completions` with the integration account token, model `idea_terminal_agent.idea-terminal-agent-integration`, and each transport above; confirm the reply is not the "requires an integration session ID" error.
2. Confirm the chosen n8n node sends the value on **every** specialist call, from `{{$('Chat Trigger').item.json.sessionId}}`, not a model-generated argument.
3. If a node cannot, use an HTTP Request node or a small adapter for those calls, preserving tool-call, streaming, and error behaviour.

## Identity

`user_id` always comes from Open WebUI's authenticated user. LangGraph derives, from `[user_id, integration_id, session_id]` with HMAC over canonical JSON (`IDEA_IDENTITY_SECRET`):

- `sandbox_id = sb_n8n_<40 hex>` — the VM, shared by all specialists in the conversation;
- `workspace_id`, and per-assistant `thread_id`/`kernel_id`.

Changing `IDEA_IDENTITY_SECRET` changes every integration VM name.

## Lifecycle and limits

State lives in Redis (`langgraph/integration_sessions.py`): `OPEN → CLOSING → EXPORTING → DELETING → DELETED`. Deleted sessions stay as tombstones (`IDEA_INTEGRATION_TOMBSTONE_TTL_SECONDS`, default 7 days).

- Admission (before each turn): one active turn per conversation; open-session, creation-rate, message-rate, turn, per-session and daily token budgets; resident-VM and disk reservations. Rejections return 409/410/413/429/503 with `Retry-After` where useful.
- The sandbox service refuses to boot an `sb_n8n_*` VM without the microsandbox backend and a Redis reservation (`idea:isess:vm:<sandbox_id>`), and applies `SANDBOX_INTEGRATION_*` resources and `SANDBOX_HOST_MAX_RESIDENT_VMS`.
- A leader-elected sweeper in LangGraph stops idle VMs (releasing their slot), expires idle/over-age sessions, exports results, deletes the VM (including VMs not cached after a restart), and deletes orphaned `sb_n8n_*` VMs. It never touches regular user VMs.
- Results: after every turn and at close, regular files under `/outputs` (excluding hidden paths; newest first; capped by `IDEA_INTEGRATION_EXPORT_MAX_SESSION_BYTES`/`_FILES`) are copied to `IDEA_INTEGRATION_EXPORT_DIR` with a per-session manifest and retained for `IDEA_INTEGRATION_EXPORT_RETENTION_SECONDS`.

Public traffic is refused until `IDEA_INTEGRATION_SESSIONS_ENABLED=true`, `IDEA_INTEGRATION_IDS` lists the integration, and `IDEA_INTEGRATION_DAILY_TOKEN_BUDGET` is positive. `GET /integration-sessions/readiness` reports configuration errors. All limits are `IDEA_INTEGRATION_<FIELD>` for the fields of `IntegrationLimits`; defaults are the guide's pilot values.

## Internal LangGraph endpoints (service token)

| Endpoint | Purpose |
|---|---|
| `POST /integration-sessions/end` | Idempotent close; body `{user_id, integration_id, integration_session_id}` |
| `POST /integration-sessions/status` | State and export manifest for that session |
| `POST /integration-sessions/exports/{object_id}` | Download one object from that session's manifest only |

## Not covered here

- **Visitor authentication.** The widget's `sessionId` is not a credential. Until a gateway issues signed per-conversation handles, anyone who learns another visitor's `sessionId` can continue that conversation. Per-session limits stand in for per-visitor limits.
- **Public downloads.** No public route serves exports yet; a gateway should call the export endpoints above after checking the visitor handle. Outputs are also still uploaded to the integration account's Open WebUI file store, which is not a visitor boundary.
- **PaperQA** is disabled for integration sessions unless `IDEA_INTEGRATION_PAPERQA_ENABLED=true`.
- **Multiple sandbox-service replicas** are not supported; boot serialization and host capacity are process-local there.
- **Real-VM acceptance** (marker files, `/outputs/plot.png` isolation, interrupt of a long run, uncached deletion) must be run on a KVM host before enabling the public workflow.
