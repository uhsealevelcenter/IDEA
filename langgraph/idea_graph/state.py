"""Serializable state and records checkpointed by IDEA's graph."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage


ExecutionStatus = Literal[
    "planned", "running", "completed", "failed", "interrupted", "outcome_unknown"
]


class ConversationMessage(TypedDict, total=False):
    id: str
    role: Literal["user", "assistant", "system"]
    content: Any


class PythonExecutionRecord(TypedDict, total=False):
    execution_id: str
    run_id: str
    tool_call_id: str
    kernel_id: str
    submitted_code: str
    code_sha256: str
    source_path: str
    started_at: str
    completed_at: str
    status: ExecutionStatus
    console_excerpt: str
    error_summary: str
    defined_names: list[str]
    output_artifacts: list[str]


class ActionRecord(TypedDict, total=False):
    execution_id: str
    run_id: str
    tool_call_id: str
    tool_name: str
    arguments_hash: str
    safe_arguments: dict[str, Any]
    status: ExecutionStatus
    started_at: str
    completed_at: str
    result_excerpt: str
    error_summary: str


class ArtifactRecord(TypedDict, total=False):
    artifact_id: str
    path: str
    description: str
    source_execution_id: str
    content_hash: str
    openwebui_file_id: str
    status: str


class DatasetRecord(TypedDict, total=False):
    dataset_id: str
    path: str
    provenance_path: str
    source_url: str
    row_count: int
    content_hash: str


class IDEAState(TypedDict, total=False):
    schema_version: int
    conversation_messages: list[ConversationMessage]
    # Replaced as a complete list by each node. This permits prepare_turn to
    # clear last turn's raw tool transcript while checkpoints retain it in
    # history and the durable execution ledger retains exact actions.
    turn_messages: list[AnyMessage]
    objective: str
    plan: list[dict[str, Any]]
    pending_tool_calls: list[dict[str, Any]]
    current_action: ActionRecord | None
    completed_actions: Annotated[list[ActionRecord], operator.add]
    python_executions: Annotated[list[PythonExecutionRecord], operator.add]
    datasets: list[DatasetRecord]
    artifacts: list[ArtifactRecord]
    active_artifact_id: str | None
    warnings: Annotated[list[str], operator.add]
    stop_requested: bool
    stop_reason: str | None
    final_status: Literal["running", "completed", "stopping", "stopped", "failed"]
    final_response: str | None
    run_id: str
    thread_id: str
    workspace_id: str
    kernel_id: str
    iteration: int
