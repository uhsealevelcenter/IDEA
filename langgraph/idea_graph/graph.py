"""IDEA's model -> one tool -> checkpoint -> cancellation graph."""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from idea_config import (
    IDEA_MAX_CODE_INLINE_BYTES,
    IDEA_MAX_RECENT_ACTIONS,
    IDEA_MAX_RECENT_EXECUTIONS,
    IDEA_MAX_STATE_BYTES,
)

from .control import RunCancellation
from .memory import (
    bounded_excerpt,
    bounded_records,
    defined_names,
    execution_memory_block,
    safe_arguments,
    sha256_text,
    utc_now,
)
from .runtime import GraphRuntime, ModelCallCancelled
from .state import IDEAState


def _tool_id(call: dict[str, Any]) -> str:
    return str(call.get("id") or uuid.uuid4().hex)


def build_idea_graph(
    runtime: GraphRuntime,
    *,
    cancellation: RunCancellation | None = None,
    checkpointer: Any = None,
    max_iterations: int = 20,
    result_excerpt_bytes: int = 12000,
):
    cancellation = cancellation or RunCancellation()
    python_ledger_bytes = max(1, IDEA_MAX_STATE_BYTES * 2 // 3)
    action_ledger_bytes = max(1, IDEA_MAX_STATE_BYTES - python_ledger_bytes)

    def bounded_actions(
        state: IDEAState,
        *new: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return bounded_records(
            [*(state.get("completed_actions") or []), *new],
            max_count=IDEA_MAX_RECENT_ACTIONS,
            max_bytes=action_ledger_bytes,
        )

    def bounded_python(
        state: IDEAState,
        *new: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return bounded_records(
            [*(state.get("python_executions") or []), *new],
            max_count=IDEA_MAX_RECENT_EXECUTIONS,
            max_bytes=python_ledger_bytes,
        )

    def prepare_turn(state: IDEAState) -> dict[str, Any]:
        runtime.emit({
            "type": "status", "phase": "starting",
            "description": "Preparing conversation memory…", "done": False,
        })
        runtime.prepare(state)
        objective = ""
        for message in reversed(state.get("conversation_messages") or []):
            if message.get("role") == "user":
                objective = str(message.get("content") or "")
                break
        return {
            "schema_version": 1,
            "objective": objective,
            "turn_messages": [],
            "pending_tool_calls": [],
            "current_action": None,
            "completed_actions": bounded_actions(state),
            "python_executions": bounded_python(state),
            "warnings": list(state.get("warnings") or [])[-20:],
            "stop_requested": cancellation.requested,
            "stop_reason": cancellation.reason,
            "final_status": "stopping" if cancellation.requested else "running",
            "final_response": None,
            "iteration": 0,
        }

    def call_model(state: IDEAState) -> dict[str, Any]:
        if cancellation.requested:
            return {
                "stop_requested": True,
                "stop_reason": cancellation.reason,
                "final_status": "stopping",
            }
        iteration = int(state.get("iteration") or 0) + 1
        if iteration > max_iterations:
            return {
                "warnings": [
                    *(state.get("warnings") or [])[-19:],
                    f"Stopped after the {max_iterations}-iteration safety limit.",
                ],
                "stop_requested": True,
                "stop_reason": "iteration_limit",
                "final_status": "stopping",
            }
        runtime.emit({
            "type": "status", "phase": "thinking",
            "description": "Thinking…", "done": False,
        })
        messages = runtime.model_messages(state)  # type: ignore[attr-defined]
        try:
            response = runtime.call_model(messages, cancellation=cancellation)
        except ModelCallCancelled as exc:
            return {
                "stop_requested": True,
                "stop_reason": str(exc) or cancellation.reason or "user_requested",
                "final_status": "stopping",
            }
        tool_calls = []
        for raw in response.tool_calls or []:
            call = dict(raw)
            call["id"] = _tool_id(call)
            tool_calls.append(call)
        return {
            "turn_messages": list(state.get("turn_messages") or []) + [response],
            "pending_tool_calls": tool_calls,
            "iteration": iteration,
            "final_response": str(response.content or "") if not tool_calls else None,
        }

    def execute_one_tool(state: IDEAState) -> dict[str, Any]:
        pending = list(state.get("pending_tool_calls") or [])
        if not pending:
            return {"current_action": None}
        call = pending.pop(0)
        name = str(call.get("name") or "")
        args = dict(call.get("args") or {})
        call_id = _tool_id(call)
        execution_id = f"exec_{uuid.uuid4().hex}"
        started = utc_now()
        sanitized_arguments = safe_arguments(name, args)
        if (
            len(json.dumps(sanitized_arguments, default=str).encode("utf-8"))
            > result_excerpt_bytes
        ):
            sanitized_arguments = {
                "summary": bounded_excerpt(
                    sanitized_arguments, result_excerpt_bytes
                )
            }
        action = {
            "execution_id": execution_id,
            "run_id": state.get("run_id", ""),
            "tool_call_id": call_id,
            "tool_name": name,
            "arguments_hash": sha256_text(json.dumps(args, sort_keys=True, default=str)),
            "safe_arguments": sanitized_arguments,
            "status": "running",
            "started_at": started,
        }
        runtime.emit({
            "type": "status", "phase": "running_tool",
            "description": f"Running {name}…", "tool_name": name, "done": False,
        })
        python_record: dict[str, Any] | None = None
        if name == "run_python_tool":
            code = str(args.get("code") or "")
            python_record = {
                "execution_id": execution_id,
                "run_id": state.get("run_id", ""),
                "tool_call_id": call_id,
                "kernel_id": state.get("kernel_id", ""),
                "submitted_code": bounded_excerpt(
                    code, IDEA_MAX_CODE_INLINE_BYTES
                ),
                "code_sha256": sha256_text(code),
                "started_at": started,
                "status": "running",
                "defined_names": defined_names(code),
                "output_artifacts": [],
            }
            try:
                python_record["source_path"] = runtime.persist_python_source(
                    execution_id, code, state
                )
            except Exception as exc:
                python_record["source_path"] = ""
                python_record["error_summary"] = f"Could not archive source: {exc}"
        try:
            if cancellation.requested:
                outcome_content = "Interrupted before tool execution."
                outcome_status = "interrupted"
                outcome_error = cancellation.reason
            else:
                outcome = runtime.execute_tool(call, state)
                outcome_content = outcome.content
                outcome_status = outcome.status
                outcome_error = outcome.error
        except Exception as exc:
            outcome_content = f"✗ {name} failed: {exc}"
            outcome_status = "failed"
            outcome_error = str(exc)
        completed = utc_now()
        action.update({
            "status": outcome_status,
            "completed_at": completed,
            "result_excerpt": bounded_excerpt(outcome_content, result_excerpt_bytes),
            **({"error_summary": bounded_excerpt(outcome_error, 2000)} if outcome_error else {}),
        })
        update: dict[str, Any] = {
            "pending_tool_calls": pending,
            "current_action": None,
            "completed_actions": bounded_actions(state, action),
            "turn_messages": list(state.get("turn_messages") or []) + [
                ToolMessage(content=outcome_content, tool_call_id=call_id)
            ],
        }
        if python_record is not None:
            python_record.update({
                "status": outcome_status,
                "completed_at": completed,
                "console_excerpt": bounded_excerpt(outcome_content, result_excerpt_bytes),
                **({"error_summary": bounded_excerpt(outcome_error, 2000)} if outcome_error else {}),
            })
            update["python_executions"] = bounded_python(
                state, python_record
            )
        if cancellation.requested or outcome_status == "interrupted":
            update.update({
                "stop_requested": True,
                "stop_reason": cancellation.reason or "tool_interrupted",
                "final_status": "stopping",
            })
        return update

    def cancellation_gate(state: IDEAState) -> dict[str, Any]:
        if cancellation.requested:
            return {
                "stop_requested": True,
                "stop_reason": cancellation.reason,
                "final_status": "stopping",
            }
        return {}

    def route_after_prepare(state: IDEAState) -> Literal["call_model", "stopped_summary"]:
        return "stopped_summary" if state.get("stop_requested") else "call_model"

    def route_after_model(state: IDEAState) -> Literal["execute_one_tool", "finalize", "stopped_summary"]:
        if state.get("stop_requested"):
            return "stopped_summary"
        if state.get("pending_tool_calls"):
            return "execute_one_tool"
        return "finalize"

    def route_after_tool(state: IDEAState) -> Literal["execute_one_tool", "call_model", "stopped_summary"]:
        if state.get("stop_requested") or cancellation.requested:
            return "stopped_summary"
        if state.get("pending_tool_calls"):
            return "execute_one_tool"
        return "call_model"

    def stopped_summary(state: IDEAState) -> dict[str, Any]:
        completed = [
            action for action in state.get("completed_actions") or []
            if action.get("status") == "completed"
        ]
        interrupted = [
            action for action in state.get("completed_actions") or []
            if action.get("status") in {"interrupted", "outcome_unknown", "running"}
        ]
        if state.get("stop_reason") == "user_requested":
            lines = ["Stopped at your request."]
        else:
            lines = ["Stopped before completion."]
        if completed:
            lines.append("\nCompleted:")
            lines.extend(
                f"- {item.get('tool_name')}: {item.get('result_excerpt', '')[:300]}"
                for item in completed[-8:]
            )
        if interrupted:
            lines.append("\nInterrupted or uncertain:")
            lines.extend(
                f"- {item.get('tool_name')} ({item.get('status')})"
                for item in interrupted[-8:]
            )
        artifacts = state.get("artifacts") or []
        if artifacts:
            lines.append("\nUsable outputs:")
            lines.extend(f"- {item.get('path')}" for item in artifacts[-8:])
        response = "\n".join(lines)
        runtime.emit(response)
        return {"final_status": "stopped", "final_response": response}

    def finalize(state: IDEAState) -> dict[str, Any]:
        runtime.emit({
            "type": "status", "phase": "syncing_outputs",
            "description": "Finalizing outputs…", "done": False,
        })
        runtime.finalize(state)
        runtime.emit({
            "type": "status", "phase": "completed",
            "description": "Finished", "done": True,
        })
        return {"final_status": "completed"}

    def finalize_stopped(state: IDEAState) -> dict[str, Any]:
        runtime.finalize(state)
        runtime.emit({
            "type": "status", "phase": "stopped",
            "description": "Stopped", "done": True,
        })
        return {}

    builder = StateGraph(IDEAState)
    builder.add_node("prepare_turn", prepare_turn)
    builder.add_node("call_model", call_model)
    builder.add_node("execute_one_tool", execute_one_tool)
    builder.add_node("cancellation_gate", cancellation_gate)
    builder.add_node("stopped_summary", stopped_summary)
    builder.add_node("finalize", finalize)
    builder.add_node("finalize_stopped", finalize_stopped)
    builder.add_edge(START, "prepare_turn")
    builder.add_conditional_edges("prepare_turn", route_after_prepare)
    builder.add_conditional_edges("call_model", route_after_model)
    builder.add_edge("execute_one_tool", "cancellation_gate")
    builder.add_conditional_edges("cancellation_gate", route_after_tool)
    builder.add_edge("stopped_summary", "finalize_stopped")
    builder.add_edge("finalize_stopped", END)
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)
