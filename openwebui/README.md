# Open WebUI Integration

Adds [Open WebUI](https://github.com/open-webui/open-webui) as an additional
frontend for the existing `langgraph` agent (`ConversationOrchestrator` /
`TerminalAgent`) and `sandbox_service` per-user microVM isolation. Nothing in
`langgraph/` or `sandbox_service/` changes - this integration is purely a
translation layer.

## How it works

`functions/idea_pipe.py` is an Open WebUI [Pipe
function](https://docs.openwebui.com/features/plugin/functions/pipe) that:

1. Registers as a selectable model ("IDEA Terminal Agent") in Open WebUI's
   model dropdown.
2. On each chat turn, POSTs to the existing `langgraph_service.py` `/chat`
   SSE endpoint (see `langgraph/langgraph_service.py`), mapping Open WebUI's
   `user.id` / `chat_id` into the `user_id` / `session_id` fields
   `ConversationOrchestrator` already expects.
3. Translates each streamed chunk (`{role, type, content, format, start,
   end}` - the same format `frontend/assistant.js` renders) into markdown
   text/images for Open WebUI's chat pane.

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
4. Back in a new chat, "IDEA Terminal Agent" will now appear in the model
   dropdown (top-left of the chat page) - not in Admin Panel > Settings >
   Models, which only manages Ollama/OpenAI connections, unrelated to Pipe
   Functions.

## Automatic file sync (`/outputs` → Open WebUI Files)

Any file the agent places under `/outputs` in its sandbox (see
`langgraph/utils/system_prompt.md`) is automatically uploaded to Open
WebUI's own Files storage at the end of each turn
(`TerminalAgent._sync_outputs_to_openwebui` in
`langgraph/agents/terminal_agent.py`) and shown as a download link in the
chat response - no extra tool call or user action needed. The model is free
to reorganize/rename files under `/outputs` mid-turn (`run_terminal_tool`);
only the final state at the end of the turn gets synced.

**One-time setup required:** generate a static Open WebUI API key (log in,
**Settings > Account > API Keys**) and set it as `OPENWEBUI_API_KEY` in
`.env`. Without it, syncing silently no-ops (files stay in the sandbox only,
same as before this feature existed).

## Status

This runs *alongside* the existing custom frontend (`frontend/`, `nginx`,
`app.py`) rather than replacing it - both currently point at the same
`langgraph` + `sandbox` backend. See `langgraph/IMPLEMENTATION_STATUS.md`
for the underlying agent/sandbox architecture this depends on.

**Not yet handled by the Pipe function:**
- Conversation history persistence beyond what `langgraph_service.py`
  already does in Redis (`langgraph_messages:{session_key}`) - Open WebUI's
  own chat history and this Redis history are not reconciled.
- Interruption/stop button (`ConversationOrchestrator` has no cancel
  endpoint exposed yet).
- Mapping Open WebUI's guest/pending-approval users to this repo's own
  guest-model/guest-scrutiny policies beyond the basic `is_guest` flag.
