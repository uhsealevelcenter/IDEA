"""
LangGraph Microservice - FastAPI server exposing the ConversationOrchestrator
"""
import asyncio
import hashlib
import hmac
import json
import os
import uuid
import threading
import time
from dataclasses import dataclass
from typing import Optional, Any, Literal
from datetime import datetime
from fastapi import Depends, FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
import redis

from multi_agent import ConversationOrchestrator
from idea_config import (
    IDEA_AGENT_MODEL,
    IDEA_AGENT_RUNTIME,
    IDEA_CHECKPOINT_MAP_TTL_SECONDS,
    IDEA_KERNEL_SCOPE,
    IDEA_MAX_TOOL_RESULT_EXCERPT_BYTES,
    resolve_idea_agent_profile,
)
from idea_graph.checkpoints import get_checkpointer
from idea_graph.control import RunCancellation
from idea_graph.graph import build_idea_graph
from idea_graph.identities import (
    BROWSER_SCOPE,
    INTEGRATION_SESSION_SCOPE,
    ExecutionIdentities,
    IntegrationContext,
    derive_execution_identities,
    derive_integration_sandbox_id,
    derive_integration_workspace_id,
    validate_integration_id,
    validate_integration_session_id,
)
from idea_graph.runtime import TerminalGraphRuntime
from integration_exports import ExportLimits, IntegrationExportStore
from integration_sessions import (
    REASON_EXPLICIT_END,
    Admission,
    AdmissionError,
    IntegrationSessionStore,
    IntegrationSessionSweeper,
    IntegrationSettings,
    SessionRecord,
    SweepActions,
)
from tools.persistent_terminal import (
    delete_integration_sandbox,
    interrupt_run as interrupt_sandbox_run,
    list_file_metadata,
    list_integration_sandboxes,
    read_file_bytes,
    stop_sandbox,
)

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

INTEGRATION_SETTINGS = IntegrationSettings.from_env()
integration_store = IntegrationSessionStore(redis_client, INTEGRATION_SETTINGS)
integration_exports = IntegrationExportStore(
    redis_client,
    os.getenv("IDEA_INTEGRATION_EXPORT_DIR", "/app/data/integration_exports"),
    ExportLimits.from_env(),
)
INTEGRATION_SWEEP_INTERVAL_SECONDS = float(
    os.getenv("IDEA_INTEGRATION_SWEEP_INTERVAL_SECONDS", "15")
)
INTEGRATION_PAPERQA_ENABLED = os.getenv(
    "IDEA_INTEGRATION_PAPERQA_ENABLED", ""
).strip().lower() in {"1", "true", "yes", "on"}

# chat_run_threads intentionally NOT persisted/shared - see
# IMPLEMENTATION_STATUS.md "item 6" for the plan to close this remaining
# statelessness gap with a proper job queue. Note there is deliberately no
# `orchestrators` cache anymore: ConversationOrchestrator is now cheap to
# construct (terminal/sandbox state lives in sandbox_service, not here), so
# every request rebuilds it fresh from Redis-backed conversation history
# instead of relying on an in-memory dict that would tie a session to
# whichever replica happened to handle its first message.
chat_run_threads = {}


def _summarize_model_usage(records: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    """Aggregate provider usage for one run without including prompt content."""
    selected = [
        item for item in records
        if isinstance(item, dict) and str(item.get("run_id") or "") == run_id
    ]
    summary: dict[str, Any] = {"model_calls": len(selected)}
    for key in (
        "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
        "output_tokens",
        "reasoning_tokens", "total_tokens", "model_text_characters",
        "model_image_count",
    ):
        values = [item.get(key) for item in selected]
        numeric = [value for value in values if isinstance(value, (int, float))]
        if numeric:
            summary[key] = int(sum(numeric))
    return summary

# Constants
CHAT_RUN_TTL_SECONDS = int(os.getenv("CHAT_RUN_TTL_SECONDS", "86400"))
CHAT_RUN_PREFIX = "langgraph_run:"
CHAT_RUN_EVENTS_PREFIX = "langgraph_run_events:"
CHAT_RUN_SEQ_PREFIX = "langgraph_run_seq:"
CHAT_RUN_CANCEL_PREFIX = "langgraph_run_cancel:"
MESSAGE_CHECKPOINT_PREFIX = "langgraph_message_checkpoint:"
LATEST_CHECKPOINT_PREFIX = "langgraph_latest_checkpoint:"
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


def _latest_checkpoint_key(base_thread_id: str) -> str:
    return f"{LATEST_CHECKPOINT_PREFIX}{base_thread_id}"


def _branch_thread_id(
    base_thread_id: str,
    response_message_id: str | None,
    run_id: str,
) -> str:
    branch_seed = response_message_id or run_id
    digest = hashlib.sha256(branch_seed.encode("utf-8")).hexdigest()[:20]
    return f"{base_thread_id}:branch:{digest}"


def _thread_belongs_to(thread_id: str, base_thread_id: str) -> bool:
    """True for the derived thread itself or one of its response branches."""
    return thread_id == base_thread_id or thread_id.startswith(
        f"{base_thread_id}:branch:"
    )


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


def _store_latest_checkpoint(
    base_thread_id: str,
    thread_id: str,
    checkpoint_id: str | None,
) -> None:
    if not checkpoint_id:
        return
    redis_client.set(
        _latest_checkpoint_key(base_thread_id),
        json.dumps({
            "schema_version": 1,
            "thread_id": thread_id,
            "checkpoint_id": checkpoint_id,
        }),
        ex=IDEA_CHECKPOINT_MAP_TTL_SECONDS,
    )


def _load_latest_checkpoint(base_thread_id: str) -> tuple[str, str] | None:
    key = _latest_checkpoint_key(base_thread_id)
    raw = redis_client.get(key)
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
    redis_client.expire(key, IDEA_CHECKPOINT_MAP_TTL_SECONDS)
    return thread_id, checkpoint_id


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
    if explicit_checkpoint_id and _thread_belongs_to(
        explicit_thread_id or base_thread_id, base_thread_id
    ):
        return (
            explicit_thread_id or base_thread_id,
            explicit_checkpoint_id,
            "message_metadata",
        )
    if explicit_checkpoint_id:
        print("Ignoring a checkpoint hint outside the derived conversation thread")

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
        if not any(visible_assistant_ids):
            latest = _load_latest_checkpoint(base_thread_id)
            if latest:
                return latest[0], latest[1], "latest_idless_history"
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
    model: Optional[str] = None
    agent_variant: Optional[Literal["standard", "advanced"]] = None
    temperature: Optional[float] = None
    max_iterations: Optional[int] = 20
    restore_history: Optional[bool] = True
    assistant_id: Optional[str] = None
    assistant_system_prompt: Optional[str] = None
    attached_files: list[dict[str, Any]] = Field(default_factory=list)
    openwebui_authorization: Optional[str] = None
    paperqa_enabled: bool = False
    # The legacy endpoint has no session-VM routing; these fields exist only
    # so an integration request is rejected instead of silently reaching the
    # owner's personal VM.
    workspace_scope: Optional[str] = None
    integration_id: Optional[str] = None
    integration_session_id: Optional[str] = None


class ChatRunRequest(BaseModel):
    session_id: str
    user_id: str
    user_email: Optional[str] = None
    is_guest: bool
    messages: list[dict[str, Any]]
    response_message_id: Optional[str] = None
    input_checkpoint_id: Optional[str] = None
    idea_context: dict[str, Any] = Field(default_factory=dict)
    agent_variant: Literal["standard", "advanced"] = "standard"
    assistant_id: Optional[str] = None
    assistant_system_prompt: Optional[str] = None
    attached_files: list[dict[str, Any]] = Field(default_factory=list)
    openwebui_authorization: Optional[str] = None
    paperqa_enabled: bool = False
    workspace_scope: Literal["browser", "integration_session"] = "browser"
    integration_id: Optional[str] = None
    integration_session_id: Optional[str] = None
    # The real Open WebUI chat, when one exists; kept apart from the
    # integration workspace identity.
    openwebui_chat_id: Optional[str] = None


class IntegrationSessionRequest(BaseModel):
    user_id: str
    integration_id: str
    integration_session_id: str


class HealthResponse(BaseModel):
    status: str
    service: str


@dataclass(frozen=True)
class ResolvedRunContext:
    """Execution identity resolved once, before any job or VM starts."""

    scope: str
    user_id: str
    session_key: str
    identities: ExecutionIdentities
    integration: IntegrationContext | None = None
    admission: Admission | None = None

    @property
    def sandbox_id(self) -> str:
        return self.identities.sandbox_id


def _admission_http_error(exc: AdmissionError) -> HTTPException:
    headers = (
        {"Retry-After": str(exc.retry_after)} if exc.retry_after is not None else None
    )
    return HTTPException(
        status_code=exc.status_code, detail=exc.as_detail(), headers=headers,
    )


def _validated_integration(
    user_id: str,
    integration_id: object,
    integration_session_id: object,
) -> IntegrationContext:
    """Validate integration fields at the trusted boundary; raise HTTP 4xx/5xx."""
    if not INTEGRATION_SETTINGS.enabled:
        raise HTTPException(
            status_code=503,
            detail={"code": "integration_disabled",
                    "message": "Integration sessions are not enabled."},
        )
    if IDEA_AGENT_RUNTIME != "langgraph":
        raise HTTPException(
            status_code=400,
            detail={"code": "integration_requires_langgraph",
                    "message": "Integration sessions require IDEA_AGENT_RUNTIME=langgraph."},
        )
    if not user_id or user_id == "anonymous":
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthenticated",
                    "message": "An authenticated integration account is required."},
        )
    try:
        integration = IntegrationContext(
            integration_id=validate_integration_id(integration_id),
            integration_session_id=validate_integration_session_id(
                integration_session_id
            ),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_integration_context", "message": str(exc)},
        )
    if integration.integration_id not in INTEGRATION_SETTINGS.integration_ids:
        raise HTTPException(
            status_code=403,
            detail={"code": "integration_not_allowed", "message": "Unknown integration."},
        )
    return integration


def _request_input_bytes(request: ChatRunRequest) -> int:
    latest_user = next(
        (
            item for item in reversed(request.messages)
            if isinstance(item, dict) and item.get("role") == "user"
        ),
        {},
    )
    return len(json.dumps(latest_user.get("content", ""), default=str).encode("utf-8"))


def _resolve_run_context(request: ChatRunRequest, run_id: str) -> ResolvedRunContext:
    if request.workspace_scope == BROWSER_SCOPE:
        if request.integration_id or request.integration_session_id:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_integration_context",
                        "message": "Integration fields require workspace_scope=integration_session."},
            )
        identities = derive_execution_identities(
            user_id=request.user_id,
            chat_id=request.session_id,
            assistant_id=request.assistant_id,
            run_id=run_id,
            kernel_scope=IDEA_KERNEL_SCOPE,
        ) if IDEA_AGENT_RUNTIME == "langgraph" else ExecutionIdentities(
            thread_id="", workspace_id="", kernel_id="", run_id=run_id,
            sandbox_id=str(request.user_id), scope=BROWSER_SCOPE,
        )
        return ResolvedRunContext(
            scope=BROWSER_SCOPE,
            user_id=request.user_id,
            session_key=f"{request.user_id}:{request.session_id}",
            identities=identities,
        )

    integration = _validated_integration(
        request.user_id, request.integration_id, request.integration_session_id,
    )
    identities = derive_execution_identities(
        user_id=request.user_id,
        chat_id=request.session_id,
        assistant_id=request.assistant_id,
        run_id=run_id,
        kernel_scope=IDEA_KERNEL_SCOPE,
        integration=integration,
    )
    try:
        admission = integration_store.admit_turn(
            owner_user_id=request.user_id,
            integration_id=integration.integration_id,
            workspace_id=identities.workspace_id,
            sandbox_id=identities.sandbox_id,
            run_id=run_id,
            input_bytes=_request_input_bytes(request),
        )
    except AdmissionError as exc:
        raise _admission_http_error(exc)
    return ResolvedRunContext(
        scope=INTEGRATION_SESSION_SCOPE,
        user_id=request.user_id,
        session_key=f"{request.user_id}:{identities.workspace_id}",
        identities=identities,
        integration=integration,
        admission=admission,
    )


def _run_sandbox_id(status: dict[str, Any]) -> str:
    """The VM a stored run executed in; legacy records used the owner's VM."""
    sandbox_id = str(status.get("sandbox_id") or "")
    if sandbox_id:
        return sandbox_id
    if status.get("workspace_scope", BROWSER_SCOPE) == BROWSER_SCOPE:
        return str(status.get("user_id") or "")
    return ""


def _export_integration_outputs(
    workspace_id: str, sandbox_id: str, deadline_at: float | None,
) -> str:
    outcome = integration_exports.export_outputs(
        workspace_id=workspace_id,
        list_metadata=lambda directory: list_file_metadata(
            directory, session_id=sandbox_id,
        ),
        read_bytes=lambda path: read_file_bytes(path, session_id=sandbox_id),
        retention_floor=deadline_at,
    )
    for error in outcome.errors:
        print(f"Integration export issue for {workspace_id}: {error}")
    return outcome.status


def _run_chat_job(
    run_id: str,
    session_id: str,
    user_id: str,
    is_guest: bool,
    messages: list[dict[str, Any]],
    model: str,
    reasoning_effort: Optional[str] = None,
    use_responses_api: bool = False,
    user_email: Optional[str] = None,
    assistant_id: Optional[str] = None,
    assistant_system_prompt: Optional[str] = None,
    attached_files: Optional[list[dict[str, Any]]] = None,
    openwebui_authorization: Optional[str] = None,
    paperqa_enabled: bool = False,
    input_checkpoint_id: Optional[str] = None,
    idea_context: Optional[dict[str, Any]] = None,
    response_message_id: Optional[str] = None,
    resolved: Optional[ResolvedRunContext] = None,
):
    """Background job to execute chat and stream events to Redis"""
    session_key = resolved.session_key if resolved else f"{user_id}:{session_id}"
    graph = None
    graph_thread_id = None
    base_thread_id = None
    model_usage_tokens: int | None = None
    admission = resolved.admission if resolved else None

    try:
        if admission is not None and IDEA_AGENT_RUNTIME != "langgraph":
            raise RuntimeError("Integration sessions require the LangGraph runtime")
        _update_chat_run_status(
            run_id,
            status="running",
            started_at=datetime.utcnow().isoformat(),
            updated_at=datetime.utcnow().isoformat(),
        )
        
        if IDEA_AGENT_RUNTIME == "langgraph":
            identities = (
                resolved.identities if resolved and resolved.identities.thread_id
                else derive_execution_identities(
                    user_id=user_id,
                    chat_id=session_id,
                    assistant_id=assistant_id,
                    run_id=run_id,
                    kernel_scope=IDEA_KERNEL_SCOPE,
                )
            )
            base_thread_id = identities.thread_id
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
            lease_interval = max(
                1.0, integration_store.limits.run_lease_seconds / 3
            )

            def _monitor_cancel() -> None:
                next_renewal = time.monotonic() + lease_interval
                while not monitor_done.wait(0.25):
                    reason = redis_client.get(_chat_run_cancel_key(run_id))
                    if reason:
                        control.request(reason)
                        return
                    if admission is None or time.monotonic() < next_renewal:
                        continue
                    next_renewal = time.monotonic() + lease_interval
                    try:
                        reason = integration_store.renew_lease(
                            admission.workspace_id, run_id, admission.epoch,
                        )
                    except Exception as exc:
                        print(f"Integration lease renewal failed for {run_id}: {exc}")
                        continue
                    if reason:
                        redis_client.set(
                            _chat_run_cancel_key(run_id), reason,
                            ex=CHAT_RUN_TTL_SECONDS,
                        )
                        control.request(reason)
                        interrupt_sandbox_run(identities.sandbox_id, run_id)
                        return

            monitor = threading.Thread(target=_monitor_cancel, daemon=True)
            monitor.start()
            try:
                runtime = TerminalGraphRuntime(
                    user_id=user_id,
                    user_email=user_email,
                    session_id=session_key,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    use_responses_api=use_responses_api,
                    assistant_id=assistant_id,
                    assistant_system_prompt=assistant_system_prompt,
                    attached_files=list(attached_files or []),
                    openwebui_authorization=openwebui_authorization,
                    is_guest=is_guest,
                    paperqa_enabled=paperqa_enabled,
                    event_callback=lambda chunk: _append_chat_run_event(run_id, chunk),
                    sandbox_id=identities.sandbox_id or None,
                )
                graph = build_idea_graph(
                    runtime,
                    cancellation=control,
                    checkpointer=get_checkpointer(),
                    result_excerpt_bytes=IDEA_MAX_TOOL_RESULT_EXCERPT_BYTES,
                )
                config: dict[str, Any] = {
                    "configurable": {"thread_id": graph_thread_id},
                    # Tool nodes make LangGraph steps grow faster than model
                    # iterations. IDEA's explicit model-iteration boundary
                    # should create a resumable checkpoint before LangGraph's
                    # structural recursion guard can raise.
                    "recursion_limit": 240,
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
                _store_latest_checkpoint(
                    identities.thread_id,
                    graph_thread_id,
                    checkpoint_id,
                )
                usage_summary = _summarize_model_usage(
                    list(result.get("model_usage") or []), run_id
                )
                if "total_tokens" in usage_summary:
                    model_usage_tokens = int(usage_summary["total_tokens"])
                _append_chat_run_event(run_id, {
                    "type": "idea_context",
                    "schema_version": 1,
                    "thread_id": graph_thread_id,
                    "run_id": run_id,
                    "input_checkpoint_id": resolved_checkpoint_id,
                    "output_checkpoint_id": checkpoint_id,
                    "checkpoint_source": checkpoint_source,
                    "active_artifact_id": result.get("active_artifact_id"),
                    "model_usage": usage_summary,
                    "execution_refs": [
                        item.get("execution_id")
                        for item in (result.get("python_executions") or [])[-20:]
                    ],
                })
                final_status = result.get("final_status") or "completed"
                _publish_integration_exports(admission)
                _update_chat_run_status(
                    run_id,
                    status=final_status,
                    thread_id=graph_thread_id,
                    checkpoint_id=checkpoint_id,
                    model_usage=usage_summary,
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
            reasoning_effort=reasoning_effort,
            use_responses_api=use_responses_api,
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
        
        _publish_integration_exports(admission)
        _update_chat_run_status(
            run_id,
            status="completed",
            completed_at=datetime.utcnow().isoformat(),
            updated_at=datetime.utcnow().isoformat(),
        )
        
    except Exception as exc:
        print(f"Error in chat run {run_id}: {exc}")
        failed_checkpoint_id = None
        if graph is not None and graph_thread_id:
            try:
                snapshot = graph.get_state({
                    "configurable": {"thread_id": graph_thread_id}
                })
                failed_checkpoint_id = snapshot.config.get(
                    "configurable", {}
                ).get("checkpoint_id")
                if base_thread_id:
                    _store_message_checkpoint(
                        base_thread_id,
                        response_message_id,
                        graph_thread_id,
                        failed_checkpoint_id,
                    )
                    _store_latest_checkpoint(
                        base_thread_id,
                        graph_thread_id,
                        failed_checkpoint_id,
                    )
            except Exception as checkpoint_exc:
                print(
                    f"Could not retain failed-run checkpoint for {run_id}: "
                    f"{checkpoint_exc}"
                )
        if failed_checkpoint_id:
            _append_chat_run_event(run_id, {
                "type": "idea_context",
                "schema_version": 1,
                "thread_id": graph_thread_id,
                "run_id": run_id,
                "output_checkpoint_id": failed_checkpoint_id,
                "checkpoint_source": "failed_run",
            })
        _append_chat_run_event(run_id, {"error": str(exc)})
        _publish_integration_exports(admission)
        _update_chat_run_status(
            run_id,
            status="failed",
            error=str(exc),
            updated_at=datetime.utcnow().isoformat(),
        )
    finally:
        chat_run_threads.pop(run_id, None)
        if admission is not None:
            _finish_integration_turn(admission, run_id, model_usage_tokens)


def _publish_integration_exports(admission: Admission | None) -> None:
    """Copy selected outputs before the run is marked finished.

    The Pipe reads the manifest as soon as it sees a terminal status, so the
    export has to be in place before that status is published.
    """
    if admission is None:
        return
    try:
        record = integration_store.get(admission.workspace_id)
        if (
            record is None
            or record.epoch != admission.epoch
            or not record.vm_resident
        ):
            return
        status = _export_integration_outputs(
            admission.workspace_id, admission.sandbox_id, record.deadline_at,
        )

        def remember(current: SessionRecord, now: float) -> None:
            current.export_status = status
        integration_store.mutate(admission.workspace_id, remember)
    except Exception as exc:
        print(f"Integration export for {admission.workspace_id} failed: {exc}")


def _finish_integration_turn(
    admission: Admission, run_id: str, tokens_used: int | None,
) -> None:
    """Export this turn's selected results, then release the turn lease."""
    _publish_integration_exports(admission)
    try:
        integration_store.finish_turn(admission.workspace_id, run_id, tokens_used)
    except Exception as exc:
        print(f"Could not release integration turn {run_id}: {exc}")


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "langgraph"}


@app.post("/chat-runs", dependencies=[Depends(require_internal_token)])
async def start_chat_run(request: ChatRunRequest):
    """Start an async chat run and return immediately with run_id"""
    profile = resolve_idea_agent_profile(request.agent_variant)
    run_id = uuid.uuid4().hex
    # Validation and admission happen before any status record, job, or VM.
    resolved = _resolve_run_context(request, run_id)
    integration_scope = resolved.scope == INTEGRATION_SESSION_SCOPE
    status = {
        "run_id": run_id,
        "user_id": request.user_id,
        "workspace_scope": resolved.scope,
        "sandbox_id": resolved.sandbox_id,
        "workspace_id": resolved.identities.workspace_id,
        "base_thread_id": resolved.identities.thread_id,
        "status": "queued",
        "agent_variant": request.agent_variant,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    if integration_scope:
        status["integration_id"] = resolved.integration.integration_id
        status["integration_epoch"] = resolved.admission.epoch
    else:
        status["session_id"] = request.session_id
    try:
        _set_chat_run_status(run_id, status)
        redis_client.delete(_chat_run_events_key(run_id))
        redis_client.delete(_chat_run_seq_key(run_id))

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
                "model": profile.model,
                "reasoning_effort": profile.reasoning_effort,
                "use_responses_api": profile.use_responses_api,
                "assistant_id": request.assistant_id,
                "assistant_system_prompt": request.assistant_system_prompt,
                "attached_files": request.attached_files,
                "openwebui_authorization": request.openwebui_authorization,
                "paperqa_enabled": request.paperqa_enabled and (
                    not integration_scope or INTEGRATION_PAPERQA_ENABLED
                ),
                "input_checkpoint_id": request.input_checkpoint_id,
                "idea_context": request.idea_context,
                "resolved": resolved,
            },
            daemon=True,
        )
        chat_run_threads[run_id] = thread
        thread.start()
    except Exception:
        if resolved.admission is not None:
            integration_store.finish_turn(resolved.admission.workspace_id, run_id, 0)
        raise

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
    # Publish the transition before contacting the sandbox.  The interrupt is
    # normally fast, but callers polling this run should immediately observe
    # that no more work will be scheduled even if the guest is slow to answer.
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
    sandbox_id = _run_sandbox_id(status)
    sandbox_interrupted = None
    if sandbox_id:
        # persistent_terminal is intentionally synchronous because graph jobs
        # execute in worker threads.  Do not call it directly from FastAPI's
        # event loop: a blocked sandbox would otherwise freeze every LangGraph
        # endpoint, including a user's next prompt.
        sandbox_interrupted = await asyncio.to_thread(
            interrupt_sandbox_run,
            sandbox_id,
            run_id,
        )
        print(
            f"Sandbox interrupt for chat run {run_id}: "
            f"interrupted={sandbox_interrupted}"
        )
    return {
        "run_id": run_id,
        "status": "stopping",
        "stop_requested": True,
        "sandbox_interrupted": sandbox_interrupted,
    }


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
    if (
        (request.workspace_scope or BROWSER_SCOPE) != BROWSER_SCOPE
        or request.integration_id
        or request.integration_session_id
    ):
        raise HTTPException(
            status_code=400,
            detail={"code": "integration_not_supported",
                    "message": "Integration sessions must use /chat-runs."},
        )
    try:
        session_key = request.session_key
        
        # Build a fresh orchestrator for this request (no cross-request
        # cache - see the note above chat_run_threads).
        # Legacy callers without a selected variant use the standard IDEA
        # profile. Explicit custom models retain their prior endpoint behavior.
        profile = (
            resolve_idea_agent_profile(request.agent_variant or "standard")
            if request.agent_variant or not request.model
            else None
        )
        orchestrator = ConversationOrchestrator(
            user_id=request.user_id,
            session_id=session_key,
            is_guest=request.is_guest,
            db=None,
            model=profile.model if profile else request.model or IDEA_AGENT_MODEL,
            reasoning_effort=(
                profile.reasoning_effort if profile
                else "medium" if request.model and request.model.startswith("gpt-6-")
                else None
            ),
            use_responses_api=(
                profile.use_responses_api if profile
                else bool(request.model and request.model.startswith("gpt-6-"))
            ),
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


def _integration_identity(request: IntegrationSessionRequest) -> tuple[IntegrationContext, str, str]:
    integration = _validated_integration(
        request.user_id, request.integration_id, request.integration_session_id,
    )
    return (
        integration,
        derive_integration_workspace_id(request.user_id, integration),
        derive_integration_sandbox_id(request.user_id, integration),
    )


def _owned_record(request: IntegrationSessionRequest) -> tuple[SessionRecord, str]:
    integration, workspace_id, _ = _integration_identity(request)
    record = integration_store.get(workspace_id)
    if (
        record is None
        or record.owner_user_id != request.user_id
        or record.integration_id != integration.integration_id
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "session_not_found", "message": "Session not found."},
        )
    return record, workspace_id


def _cancel_chat_run(run_id: str, reason: str) -> None:
    redis_client.set(_chat_run_cancel_key(run_id), reason, ex=CHAT_RUN_TTL_SECONDS)


@app.post("/integration-sessions/end", dependencies=[Depends(require_internal_token)])
async def end_integration_session(request: IntegrationSessionRequest):
    """Explicitly end a conversation: reject new turns, then export and delete."""
    integration, workspace_id, sandbox_id = _integration_identity(request)
    try:
        record = await asyncio.to_thread(
            integration_store.request_close,
            owner_user_id=request.user_id,
            integration_id=integration.integration_id,
            workspace_id=workspace_id,
            sandbox_id=sandbox_id,
            reason=REASON_EXPLICIT_END,
        )
    except AdmissionError as exc:
        raise _admission_http_error(exc)
    if record.active_run_id:
        _cancel_chat_run(record.active_run_id, record.close_reason or REASON_EXPLICIT_END)
    integration_sweep_wakeup.set()
    return record.public_status()


@app.post("/integration-sessions/status", dependencies=[Depends(require_internal_token)])
async def integration_session_status(request: IntegrationSessionRequest):
    record, workspace_id = _owned_record(request)
    status = record.public_status()
    status["exports"] = [
        {
            "object_id": item.object_id,
            "filename": item.path.rsplit("/", 1)[-1],
            "path": item.path,
            "size": item.size,
            "sha256": item.sha256,
            "content_type": item.content_type,
            "expires_at": datetime.utcfromtimestamp(item.expires_at).isoformat() + "Z",
        }
        for item in integration_exports.manifest(workspace_id)
    ]
    return status


@app.post(
    "/integration-sessions/exports/{object_id}",
    dependencies=[Depends(require_internal_token)],
)
async def download_integration_export(object_id: str, request: IntegrationSessionRequest):
    """Serve one object from the requesting session's export manifest only."""
    _, workspace_id, _ = _integration_identity(request)
    found = integration_exports.get_object(workspace_id, object_id)
    if found is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "export_not_found", "message": "Export not found."},
        )
    item, path = found
    return _export_file_response(item, path)


_INLINE_EXPORT_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def _export_download_name(path: str) -> str:
    name = path.rsplit("/", 1)[-1] or "download"
    name = name.replace("\\", "").replace('"', "").replace("\r", "").replace("\n", "")
    return name[:180] or "download"


def _export_file_response(item, path):
    """Serve one exported object. Images render inline; everything else downloads.

    HTML and SVG stay attachments so a public link cannot run as a page on
    this origin.
    """
    filename = _export_download_name(item.path)
    disposition = (
        "inline" if item.content_type in _INLINE_EXPORT_TYPES else "attachment"
    )
    return FileResponse(
        path,
        media_type=item.content_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "X-Content-SHA256": item.sha256,
            "Cache-Control": "private, no-store",
        },
    )


@app.get("/integration-exports/{object_id}")
@app.get("/integration-exports/{object_id}/{filename}")
async def download_public_integration_export(object_id: str, filename: str | None = None):
    """Public download. The object id is the capability; the filename is ignored."""
    del filename
    found = integration_exports.resolve_public(object_id)
    if found is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "export_not_found", "message": "Export not found."},
        )
    item, path = found
    return _export_file_response(item, path)


@app.get("/integration-sessions/readiness", dependencies=[Depends(require_internal_token)])
async def integration_readiness():
    errors = INTEGRATION_SETTINGS.readiness_errors()
    return JSONResponse(
        status_code=200 if not errors else 503,
        content={
            "enabled": INTEGRATION_SETTINGS.enabled,
            "integration_ids": sorted(INTEGRATION_SETTINGS.integration_ids),
            "errors": errors,
        },
    )


def _sweep_export_session(record: SessionRecord, final_attempt: bool) -> str:
    if not record.vm_resident:
        # Every turn exported its results before the VM was stopped; booting
        # it again only to re-export would bypass VM capacity reservations.
        status = record.export_status or "complete"
        return "unavailable" if status == "failed" else status
    return _export_integration_outputs(
        record.workspace_id, record.sandbox_id, record.deadline_at,
    )


integration_sweeper = IntegrationSessionSweeper(
    integration_store,
    SweepActions(
        stop_vm=stop_sandbox,
        delete_vm=delete_integration_sandbox,
        interrupt_run=interrupt_sandbox_run,
        cancel_run=_cancel_chat_run,
        export_session=_sweep_export_session,
        list_vms=list_integration_sandboxes,
    ),
    leader_ttl_seconds=max(30, int(INTEGRATION_SWEEP_INTERVAL_SECONDS * 4)),
)
integration_sweep_wakeup = threading.Event()
integration_sweep_stop = threading.Event()


def _integration_sweep_loop() -> None:
    while not integration_sweep_stop.is_set():
        integration_sweep_wakeup.wait(INTEGRATION_SWEEP_INTERVAL_SECONDS)
        integration_sweep_wakeup.clear()
        if integration_sweep_stop.is_set():
            return
        try:
            if (
                not INTEGRATION_SETTINGS.enabled
                and not integration_store.indexed_workspace_ids()
            ):
                continue
            if integration_sweeper.acquire_leadership():
                integration_sweeper.run_once()
                integration_exports.purge_expired()
        except Exception as exc:
            print(f"Integration session sweep failed: {exc}")


@app.on_event("startup")
def _start_integration_sweeper() -> None:
    # Sessions created while the feature was enabled still need cleanup after
    # it is disabled, so the sweeper runs whenever records may exist.
    for error in INTEGRATION_SETTINGS.readiness_errors():
        print(f"Integration sessions misconfigured: {error}")
    if os.getenv("IDEA_INTEGRATION_SWEEPER_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        return
    threading.Thread(
        target=_integration_sweep_loop, name="integration-sweeper", daemon=True,
    ).start()


@app.on_event("shutdown")
def _stop_integration_sweeper() -> None:
    integration_sweep_stop.set()
    integration_sweep_wakeup.set()


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
