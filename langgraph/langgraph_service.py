"""
LangGraph Microservice - FastAPI server exposing the ConversationOrchestrator
"""
import hashlib
import hmac
import json
import os
import uuid
import threading
from typing import Optional, Any
from datetime import datetime
from fastapi import Depends, FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import redis

from multi_agent import ConversationOrchestrator
from idea_config import (
    IDEA_AGENT_RUNTIME,
    IDEA_CHECKPOINT_MAP_TTL_SECONDS,
    IDEA_KERNEL_SCOPE,
    IDEA_MAX_TOOL_RESULT_EXCERPT_BYTES,
)
from idea_graph.checkpoints import get_checkpointer
from idea_graph.control import RunCancellation
from idea_graph.graph import build_idea_graph
from idea_graph.identities import derive_execution_identities
from idea_graph.runtime import TerminalGraphRuntime
from tools.persistent_terminal import interrupt_run as interrupt_sandbox_run

app = FastAPI(title="LangGraph Service", version="1.0.0")

# Shared secret between this service's callers (openwebui/functions/
# idea_pipe.py's Valve of the same name) and this service itself - not a
# per-user credential. See sandbox_service/main.py for the identical
# pattern guarding that service. Empty/unset fails OPEN (no check) rather
# than locking out dev setups that haven't configured it yet - every
# production .env should set this; see example.env.
INTERNAL_SERVICE_TOKEN = os.getenv("INTERNAL_SERVICE_TOKEN", "")


def require_internal_token(request: Request) -> None:
    if not INTERNAL_SERVICE_TOKEN:
        return
    auth_header = request.headers.get("authorization", "")
    provided = auth_header[7:] if auth_header.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(provided, INTERNAL_SERVICE_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid or missing internal service token")

# Redis connection
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

# chat_run_threads intentionally NOT persisted/shared - see
# IMPLEMENTATION_STATUS.md "item 6" for the plan to close this remaining
# statelessness gap with a proper job queue. Note there is deliberately no
# `orchestrators` cache anymore: ConversationOrchestrator is now cheap to
# construct (terminal/sandbox state lives in sandbox_service, not here), so
# every request rebuilds it fresh from Redis-backed conversation history
# instead of relying on an in-memory dict that would tie a session to
# whichever replica happened to handle its first message.
chat_run_threads = {}

# Constants
CHAT_RUN_TTL_SECONDS = int(os.getenv("CHAT_RUN_TTL_SECONDS", "86400"))
CHAT_RUN_PREFIX = "langgraph_run:"
CHAT_RUN_EVENTS_PREFIX = "langgraph_run_events:"
CHAT_RUN_SEQ_PREFIX = "langgraph_run_seq:"
CHAT_RUN_CANCEL_PREFIX = "langgraph_run_cancel:"
MESSAGE_CHECKPOINT_PREFIX = "langgraph_message_checkpoint:"
chat_run_controls: dict[str, RunCancellation] = {}
chat_run_controls_lock = threading.Lock()

_APPEND_CHAT_RUN_EVENT_SCRIPT = """
local seq = redis.call('INCR', KEYS[1])
local event = cjson.encode({
    seq = seq,
    created_at = ARGV[1],
    chunk = cjson.decode(ARGV[2])
})
redis.call('RPUSH', KEYS[2], event)
redis.call('EXPIRE', KEYS[1], ARGV[3])
redis.call('EXPIRE', KEYS[2], ARGV[3])
return event
"""


# Helper functions for chat runs
def _chat_run_key(run_id: str) -> str:
    return f"{CHAT_RUN_PREFIX}{run_id}"


def _chat_run_events_key(run_id: str) -> str:
    return f"{CHAT_RUN_EVENTS_PREFIX}{run_id}"


def _chat_run_seq_key(run_id: str) -> str:
    return f"{CHAT_RUN_SEQ_PREFIX}{run_id}"


def _chat_run_cancel_key(run_id: str) -> str:
    return f"{CHAT_RUN_CANCEL_PREFIX}{run_id}"


def _message_checkpoint_key(base_thread_id: str, message_id: str) -> str:
    message_digest = hashlib.sha256(message_id.encode("utf-8")).hexdigest()
    return f"{MESSAGE_CHECKPOINT_PREFIX}{base_thread_id}:{message_digest}"


def _branch_thread_id(
    base_thread_id: str,
    response_message_id: str | None,
    run_id: str,
) -> str:
    branch_seed = response_message_id or run_id
    digest = hashlib.sha256(branch_seed.encode("utf-8")).hexdigest()[:20]
    return f"{base_thread_id}:branch:{digest}"


def _load_message_checkpoint(
    base_thread_id: str,
    message_id: str,
) -> tuple[str, str] | None:
    if not message_id:
        return None
    raw = redis_client.get(_message_checkpoint_key(base_thread_id, message_id))
    if not raw:
        return None
    try:
        value = json.loads(raw)
        thread_id = str(value.get("thread_id") or "")
        checkpoint_id = str(value.get("checkpoint_id") or "")
    except Exception:
        return None
    if not thread_id or not checkpoint_id:
        return None
    redis_client.expire(
        _message_checkpoint_key(base_thread_id, message_id),
        IDEA_CHECKPOINT_MAP_TTL_SECONDS,
    )
    return thread_id, checkpoint_id


def _store_message_checkpoint(
    base_thread_id: str,
    response_message_id: str | None,
    thread_id: str,
    checkpoint_id: str | None,
) -> None:
    if not response_message_id or not checkpoint_id:
        return
    redis_client.set(
        _message_checkpoint_key(base_thread_id, response_message_id),
        json.dumps({
            "schema_version": 1,
            "thread_id": thread_id,
            "checkpoint_id": checkpoint_id,
        }),
        ex=IDEA_CHECKPOINT_MAP_TTL_SECONDS,
    )


def _resolve_graph_checkpoint(
    *,
    base_thread_id: str,
    messages: list[dict[str, Any]],
    response_message_id: str | None,
    run_id: str,
    explicit_checkpoint_id: str | None = None,
    explicit_thread_id: str | None = None,
) -> tuple[str, str | None, str]:
    """Resolve the checkpoint belonging to the visible Open WebUI branch."""
    if explicit_checkpoint_id:
        return (
            explicit_thread_id or base_thread_id,
            explicit_checkpoint_id,
            "message_metadata",
        )

    visible_assistant_ids = [
        str(item.get("id") or "")
        for item in messages
        if isinstance(item, dict) and item.get("role") == "assistant"
    ]
    for message_id in reversed(visible_assistant_ids):
        mapping = _load_message_checkpoint(base_thread_id, message_id)
        if mapping:
            return mapping[0], mapping[1], "message_mapping"

    if visible_assistant_ids:
        # One-time migration path for chats created before message mappings:
        # continue their existing linear thread, then map the new response.
        return base_thread_id, None, "legacy_latest"

    # A first turn or regeneration of the root user message must not inherit
    # an abandoned latest checkpoint from another response branch.
    return (
        _branch_thread_id(base_thread_id, response_message_id, run_id),
        None,
        "new_branch",
    )


def _set_chat_run_status(run_id: str, status: dict[str, Any]) -> None:
    redis_client.set(
        _chat_run_key(run_id),
        json.dumps(status, default=str),
        ex=CHAT_RUN_TTL_SECONDS,
    )


def _get_chat_run_status(run_id: str) -> dict[str, Any] | None:
    raw_status = redis_client.get(_chat_run_key(run_id))
    if not raw_status:
        return None
    try:
        return json.loads(raw_status)
    except Exception:
        return None


def _update_chat_run_status(run_id: str, **updates: Any) -> dict[str, Any]:
    status = _get_chat_run_status(run_id) or {"run_id": run_id}
    status.update(updates)
    status.setdefault("updated_at", datetime.utcnow().isoformat())
    _set_chat_run_status(run_id, status)
    return status


def _append_chat_run_event(run_id: str, chunk: dict[str, Any] | str) -> dict[str, Any]:
    raw_event = redis_client.eval(
        _APPEND_CHAT_RUN_EVENT_SCRIPT,
        2,
        _chat_run_seq_key(run_id),
        _chat_run_events_key(run_id),
        datetime.utcnow().isoformat(),
        json.dumps(chunk, default=str),
        CHAT_RUN_TTL_SECONDS,
    )
    return json.loads(raw_event)


def _list_chat_run_events(run_id: str, after: int = 0) -> list[dict[str, Any]]:
    # Sequence numbers start at one and events are appended exactly once, so
    # the first unseen event is at the zero-based list index ``after``. Avoid
    # rereading and JSON-decoding the run's complete token/event history on
    # every UI poll.
    raw_events = redis_client.lrange(
        _chat_run_events_key(run_id), max(int(after), 0), -1
    )
    events: list[dict[str, Any]] = []
    for raw_event in raw_events:
        try:
            event = json.loads(raw_event)
            if int(event.get("seq", 0)) > after:
                events.append(event)
        except Exception:
            continue
    return events


class ChatRequest(BaseModel):
    session_key: str
    user_id: str
    # Used only for LiteLLM per-end-user spend tracking (see
    # agents/terminal_agent.py) - optional so older callers without it
    # still work (falls back to user_id there).
    user_email: Optional[str] = None
    is_guest: bool
    message: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    chat_id: Optional[str] = None
    input_checkpoint_id: Optional[str] = None
    idea_context: dict[str, Any] = Field(default_factory=dict)
    model: Optional[str] = "gpt-5.6-sol"
    temperature: Optional[float] = None
    max_iterations: Optional[int] = 20
    restore_history: Optional[bool] = True
    assistant_id: Optional[str] = None
    assistant_system_prompt: Optional[str] = None
    attached_files: list[dict[str, Any]] = Field(default_factory=list)
    openwebui_authorization: Optional[str] = None
    paperqa_enabled: bool = False


class ChatRunRequest(BaseModel):
    session_id: str
    user_id: str
    user_email: Optional[str] = None
    is_guest: bool
    messages: list[dict[str, Any]]
    response_message_id: Optional[str] = None
    input_checkpoint_id: Optional[str] = None
    idea_context: dict[str, Any] = Field(default_factory=dict)
    model: Optional[str] = "gpt-5.6-sol"
    assistant_id: Optional[str] = None
    assistant_system_prompt: Optional[str] = None
    attached_files: list[dict[str, Any]] = Field(default_factory=list)
    openwebui_authorization: Optional[str] = None
    paperqa_enabled: bool = False


class HealthResponse(BaseModel):
    status: str
    service: str


def _run_chat_job(
    run_id: str,
    session_id: str,
    user_id: str,
    is_guest: bool,
    messages: list[dict[str, Any]],
    model: str,
    user_email: Optional[str] = None,
    assistant_id: Optional[str] = None,
    assistant_system_prompt: Optional[str] = None,
    attached_files: Optional[list[dict[str, Any]]] = None,
    openwebui_authorization: Optional[str] = None,
    paperqa_enabled: bool = False,
    input_checkpoint_id: Optional[str] = None,
    idea_context: Optional[dict[str, Any]] = None,
    response_message_id: Optional[str] = None,
):
    """Background job to execute chat and stream events to Redis"""
    session_key = f"{user_id}:{session_id}"
    
    try:
        _update_chat_run_status(
            run_id,
            status="running",
            started_at=datetime.utcnow().isoformat(),
            updated_at=datetime.utcnow().isoformat(),
        )
        
        if IDEA_AGENT_RUNTIME == "langgraph":
            identities = derive_execution_identities(
                user_id=user_id,
                chat_id=session_id,
                assistant_id=assistant_id,
                run_id=run_id,
                kernel_scope=IDEA_KERNEL_SCOPE,
            )
            graph_thread_id, resolved_checkpoint_id, checkpoint_source = (
                _resolve_graph_checkpoint(
                    base_thread_id=identities.thread_id,
                    messages=messages,
                    response_message_id=response_message_id,
                    run_id=run_id,
                    explicit_checkpoint_id=input_checkpoint_id,
                    explicit_thread_id=str(
                        (idea_context or {}).get("thread_id") or ""
                    ) or None,
                )
            )
            control = RunCancellation()
            queued_cancel_reason = redis_client.get(_chat_run_cancel_key(run_id))
            if queued_cancel_reason:
                control.request(queued_cancel_reason)
            with chat_run_controls_lock:
                chat_run_controls[run_id] = control

            monitor_done = threading.Event()

            def _monitor_cancel() -> None:
                while not monitor_done.wait(0.25):
                    reason = redis_client.get(_chat_run_cancel_key(run_id))
                    if reason:
                        control.request(reason)
                        return

            monitor = threading.Thread(target=_monitor_cancel, daemon=True)
            monitor.start()
            try:
                runtime = TerminalGraphRuntime(
                    user_id=user_id,
                    user_email=user_email,
                    session_id=session_key,
                    model=model,
                    assistant_id=assistant_id,
                    assistant_system_prompt=assistant_system_prompt,
                    attached_files=list(attached_files or []),
                    openwebui_authorization=openwebui_authorization,
                    is_guest=is_guest,
                    paperqa_enabled=paperqa_enabled,
                    event_callback=lambda chunk: _append_chat_run_event(run_id, chunk),
                )
                graph = build_idea_graph(
                    runtime,
                    cancellation=control,
                    checkpointer=get_checkpointer(),
                    result_excerpt_bytes=IDEA_MAX_TOOL_RESULT_EXCERPT_BYTES,
                )
                config: dict[str, Any] = {
                    "configurable": {"thread_id": graph_thread_id},
                    "recursion_limit": 100,
                }
                if resolved_checkpoint_id:
                    config["configurable"]["checkpoint_id"] = (
                        resolved_checkpoint_id
                    )
                normalized_messages = [
                    {
                        "id": str(item.get("id") or ""),
                        "role": str(item.get("role") or "user"),
                        "content": item.get("content", ""),
                    }
                    for item in messages
                    if isinstance(item, dict)
                    and item.get("role") in {"user", "assistant", "system"}
                ]
                result = graph.invoke(
                    {
                        "schema_version": 1,
                        "conversation_messages": normalized_messages,
                        "run_id": run_id,
                        "thread_id": graph_thread_id,
                        "workspace_id": identities.workspace_id,
                        "kernel_id": identities.kernel_id,
                    },
                    config=config,
                )
                snapshot = graph.get_state({
                    "configurable": {"thread_id": graph_thread_id}
                })
                checkpoint_id = snapshot.config.get("configurable", {}).get(
                    "checkpoint_id"
                )
                _store_message_checkpoint(
                    identities.thread_id,
                    response_message_id,
                    graph_thread_id,
                    checkpoint_id,
                )
                _append_chat_run_event(run_id, {
                    "type": "idea_context",
                    "schema_version": 1,
                    "thread_id": graph_thread_id,
                    "run_id": run_id,
                    "input_checkpoint_id": resolved_checkpoint_id,
                    "output_checkpoint_id": checkpoint_id,
                    "checkpoint_source": checkpoint_source,
                    "active_artifact_id": result.get("active_artifact_id"),
                    "execution_refs": [
                        item.get("execution_id")
                        for item in (result.get("python_executions") or [])[-20:]
                    ],
                })
                final_status = result.get("final_status") or "completed"
                _update_chat_run_status(
                    run_id,
                    status=final_status,
                    thread_id=graph_thread_id,
                    checkpoint_id=checkpoint_id,
                    completed_at=datetime.utcnow().isoformat(),
                    updated_at=datetime.utcnow().isoformat(),
                )
                return
            finally:
                monitor_done.set()
                with chat_run_controls_lock:
                    chat_run_controls.pop(run_id, None)

        # Manual rollback path. Build a fresh orchestrator for this request.
        # - see the note above chat_run_threads) and restore its history from
        # Redis, the durable/shared store for conversation state.
        orchestrator = ConversationOrchestrator(
            user_id=user_id,
            session_id=session_key,
            is_guest=is_guest,
            db=None,
            model=model,
            temperature=None,
            max_iterations=20,
            user_email=user_email,
            assistant_id=assistant_id,
            assistant_system_prompt=assistant_system_prompt,
            attached_files=attached_files,
            openwebui_authorization=openwebui_authorization,
            paperqa_enabled=paperqa_enabled,
        )
        
        stored_messages = redis_client.get(f"langgraph_messages:{session_key}")
        if stored_messages:
            try:
                orchestrator.conversation_history = json.loads(stored_messages)
            except Exception as e:
                print(f"Failed to restore history: {e}")
        
        # Extract last user message
        last_message = messages[-1]
        if isinstance(last_message, dict):
            last_message = last_message.get("content", "")
        
        # Stream chat responses
        for chunk in orchestrator.chat(message=last_message, stream=True):
            _append_chat_run_event(run_id, chunk)
        
        # Save conversation history
        try:
            redis_client.set(
                f"langgraph_messages:{session_key}",
                json.dumps(orchestrator.conversation_history)
            )
        except Exception as e:
            print(f"Failed to save history: {e}")
        
        _update_chat_run_status(
            run_id,
            status="completed",
            completed_at=datetime.utcnow().isoformat(),
            updated_at=datetime.utcnow().isoformat(),
        )
        
    except Exception as exc:
        print(f"Error in chat run {run_id}: {exc}")
        _append_chat_run_event(run_id, {"error": str(exc)})
        _update_chat_run_status(
            run_id,
            status="failed",
            error=str(exc),
            updated_at=datetime.utcnow().isoformat(),
        )
    finally:
        chat_run_threads.pop(run_id, None)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "langgraph"}


@app.post("/chat-runs", dependencies=[Depends(require_internal_token)])
async def start_chat_run(request: ChatRunRequest):
    """Start an async chat run and return immediately with run_id"""
    run_id = uuid.uuid4().hex
    status = {
        "run_id": run_id,
        "session_id": request.session_id,
        "user_id": request.user_id,
        "status": "queued",
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    _set_chat_run_status(run_id, status)
    redis_client.delete(_chat_run_events_key(run_id))
    redis_client.delete(_chat_run_seq_key(run_id))
    
    # Start background thread
    thread = threading.Thread(
        target=_run_chat_job,
        kwargs={
            "run_id": run_id,
            "session_id": request.session_id,
            "user_id": request.user_id,
            "user_email": request.user_email,
            "is_guest": request.is_guest,
            "messages": request.messages,
            "response_message_id": request.response_message_id,
            "model": request.model,
            "assistant_id": request.assistant_id,
            "assistant_system_prompt": request.assistant_system_prompt,
            "attached_files": request.attached_files,
            "openwebui_authorization": request.openwebui_authorization,
            "paperqa_enabled": request.paperqa_enabled,
            "input_checkpoint_id": request.input_checkpoint_id,
            "idea_context": request.idea_context,
        },
        daemon=True,
    )
    chat_run_threads[run_id] = thread
    thread.start()
    
    return {"run_id": run_id, "status": "queued"}


@app.post("/chat-runs/{run_id}/stop", dependencies=[Depends(require_internal_token)])
async def stop_chat_run(run_id: str):
    """Request a cooperative stop and preserve completed checkpoints."""
    status = _get_chat_run_status(run_id)
    if not status:
        raise HTTPException(status_code=404, detail="Chat run not found")
    current = status.get("status")
    if current in {"completed", "stopped", "failed", "cancelled-before-start"}:
        return {"run_id": run_id, "status": current, "stop_requested": False}
    redis_client.set(
        _chat_run_cancel_key(run_id),
        "user_requested",
        ex=CHAT_RUN_TTL_SECONDS,
    )
    with chat_run_controls_lock:
        control = chat_run_controls.get(run_id)
    if control:
        control.request("user_requested")
    user_id = str(status.get("user_id") or "")
    if user_id:
        interrupt_sandbox_run(user_id, run_id)
    _update_chat_run_status(
        run_id,
        status="stopping",
        stop_requested_at=datetime.utcnow().isoformat(),
        updated_at=datetime.utcnow().isoformat(),
    )
    _append_chat_run_event(run_id, {
        "type": "status", "phase": "stopping",
        "description": "Stopping at the next safe point…", "done": False,
    })
    return {"run_id": run_id, "status": "stopping", "stop_requested": True}


@app.get("/chat-runs/{run_id}", dependencies=[Depends(require_internal_token)])
async def get_chat_run(run_id: str):
    """Get chat run status"""
    status = _get_chat_run_status(run_id)
    if not status:
        raise HTTPException(status_code=404, detail="Chat run not found")
    return status


@app.get("/chat-runs/{run_id}/events", dependencies=[Depends(require_internal_token)])
async def get_chat_run_events(run_id: str, after: int = 0):
    """Get chat run events (for polling)"""
    status = _get_chat_run_status(run_id)
    if not status:
        raise HTTPException(status_code=404, detail="Chat run not found")
    
    events = _list_chat_run_events(run_id, after=after)
    latest_seq = after
    if events:
        latest_seq = max(int(event.get("seq", after)) for event in events)
    
    return {
        "run_id": run_id,
        "status": status.get("status"),
        "events": events,
        "next_after": latest_seq,
        "error": status.get("error"),
    }


@app.post("/chat", dependencies=[Depends(require_internal_token)])
async def chat_endpoint(request: ChatRequest):
    """
    Direct streaming chat endpoint (alternative to /chat-runs)
    """
    try:
        session_key = request.session_key
        
        # Build a fresh orchestrator for this request (no cross-request
        # cache - see the note above chat_run_threads).
        orchestrator = ConversationOrchestrator(
            user_id=request.user_id,
            session_id=session_key,
            is_guest=request.is_guest,
            db=None,
            model=request.model,
            temperature=request.temperature,
            max_iterations=request.max_iterations,
            user_email=request.user_email,
            assistant_id=request.assistant_id,
            assistant_system_prompt=request.assistant_system_prompt,
            attached_files=request.attached_files,
            openwebui_authorization=request.openwebui_authorization,
            paperqa_enabled=request.paperqa_enabled,
        )
        
        # Restore conversation history from Redis if requested
        if request.restore_history:
            stored_messages = redis_client.get(f"langgraph_messages:{session_key}")
            if stored_messages:
                try:
                    orchestrator.conversation_history = json.loads(stored_messages)
                except Exception as e:
                    print(f"Failed to restore history: {e}")
        
        # Stream responses
        def event_stream():
            try:
                for chunk in orchestrator.chat(message=request.message, stream=True):
                    yield f"data: {json.dumps(chunk)}\n\n"
            except GeneratorExit:
                print(f"Stream disconnected for session {session_key}")
                raise
            except Exception as e:
                print(f"Error in chat stream: {e}")
                error_message = {"error": str(e)}
                yield f"data: {json.dumps(error_message)}\n\n"
            finally:
                # Save conversation history to Redis
                try:
                    redis_client.set(
                        f"langgraph_messages:{session_key}",
                        json.dumps(orchestrator.conversation_history)
                    )
                except Exception as e:
                    print(f"Failed to save history: {e}")
        
        return StreamingResponse(event_stream(), media_type="text/event-stream")
    
    except Exception as e:
        print(f"Error in chat endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/clear", dependencies=[Depends(require_internal_token)])
async def clear_session(request: Request):
    """Clear a session's orchestrator and history"""
    body = await request.json()
    session_key = body.get("session_key")
    
    if not session_key:
        raise HTTPException(status_code=400, detail="session_key is required")
    
    # Remove from Redis (no in-memory orchestrator cache to clear anymore)
    redis_client.delete(f"langgraph_messages:{session_key}")
    
    return {"status": "cleared", "session_key": session_key}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8010)
