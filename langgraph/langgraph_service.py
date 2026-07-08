"""
LangGraph Microservice - FastAPI server exposing the ConversationOrchestrator
"""
import json
import os
import uuid
import threading
from typing import Optional, Any
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import redis

from multi_agent import ConversationOrchestrator

app = FastAPI(title="LangGraph Service", version="1.0.0")

# Redis connection
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

# In-memory orchestrator storage
orchestrators = {}
chat_run_threads = {}

# Constants
CHAT_RUN_TTL_SECONDS = int(os.getenv("CHAT_RUN_TTL_SECONDS", "86400"))
CHAT_RUN_PREFIX = "langgraph_run:"
CHAT_RUN_EVENTS_PREFIX = "langgraph_run_events:"
CHAT_RUN_SEQ_PREFIX = "langgraph_run_seq:"


# Helper functions for chat runs
def _chat_run_key(run_id: str) -> str:
    return f"{CHAT_RUN_PREFIX}{run_id}"


def _chat_run_events_key(run_id: str) -> str:
    return f"{CHAT_RUN_EVENTS_PREFIX}{run_id}"


def _chat_run_seq_key(run_id: str) -> str:
    return f"{CHAT_RUN_SEQ_PREFIX}{run_id}"


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
    seq = int(redis_client.incr(_chat_run_seq_key(run_id)))
    event = {
        "seq": seq,
        "created_at": datetime.utcnow().isoformat(),
        "chunk": chunk,
    }
    events_key = _chat_run_events_key(run_id)
    redis_client.rpush(events_key, json.dumps(event, default=str))
    redis_client.expire(events_key, CHAT_RUN_TTL_SECONDS)
    redis_client.expire(_chat_run_seq_key(run_id), CHAT_RUN_TTL_SECONDS)
    return event


def _list_chat_run_events(run_id: str, after: int = 0) -> list[dict[str, Any]]:
    raw_events = redis_client.lrange(_chat_run_events_key(run_id), 0, -1)
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
    is_guest: bool
    message: str
    model: Optional[str] = "gpt-4o-mini"
    temperature: Optional[float] = None
    max_iterations: Optional[int] = 20
    restore_history: Optional[bool] = True


class ChatRunRequest(BaseModel):
    session_id: str
    user_id: str
    is_guest: bool
    messages: list[dict[str, Any]]
    model: Optional[str] = "gpt-4o-mini"


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
        
        # Get or create orchestrator
        if session_key not in orchestrators:
            orchestrators[session_key] = ConversationOrchestrator(
                user_id=user_id,
                session_id=session_key,
                is_guest=is_guest,
                db=None,
                model=model,
                temperature=None,
                max_iterations=20
            )
        
        orchestrator = orchestrators[session_key]
        
        # Restore conversation history from Redis
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


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "langgraph"}


@app.post("/chat-runs")
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
            "is_guest": request.is_guest,
            "messages": request.messages,
            "model": request.model,
        },
        daemon=True,
    )
    chat_run_threads[run_id] = thread
    thread.start()
    
    return {"run_id": run_id, "status": "queued"}


@app.get("/chat-runs/{run_id}")
async def get_chat_run(run_id: str):
    """Get chat run status"""
    status = _get_chat_run_status(run_id)
    if not status:
        raise HTTPException(status_code=404, detail="Chat run not found")
    return status


@app.get("/chat-runs/{run_id}/events")
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


@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    """
    Direct streaming chat endpoint (alternative to /chat-runs)
    """
    try:
        session_key = request.session_key
        
        # Get or create orchestrator
        if session_key not in orchestrators:
            orchestrators[session_key] = ConversationOrchestrator(
                user_id=request.user_id,
                session_id=session_key,
                is_guest=request.is_guest,
                db=None,
                model=request.model,
                temperature=request.temperature,
                max_iterations=request.max_iterations
            )
        
        orchestrator = orchestrators[session_key]
        
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


@app.post("/clear")
async def clear_session(request: Request):
    """Clear a session's orchestrator and history"""
    body = await request.json()
    session_key = body.get("session_key")
    
    if not session_key:
        raise HTTPException(status_code=400, detail="session_key is required")
    
    # Remove from memory
    if session_key in orchestrators:
        del orchestrators[session_key]
    
    # Remove from Redis
    redis_client.delete(f"langgraph_messages:{session_key}")
    
    return {"status": "cleared", "session_key": session_key}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8010)
