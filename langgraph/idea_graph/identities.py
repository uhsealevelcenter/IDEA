"""Server-side derivation of conversation, workspace, kernel, and run IDs."""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionIdentities:
    thread_id: str
    workspace_id: str
    kernel_id: str
    run_id: str


def _digest(kind: str, *parts: str) -> str:
    secret = os.getenv("IDEA_IDENTITY_SECRET") or os.getenv("INTERNAL_SERVICE_TOKEN")
    if not secret:
        # Development remains deterministic; production configuration is
        # validated by the service readiness check.
        secret = "idea-development-identity-key"
    material = "\x1f".join(("v1", kind, *(str(part) for part in parts)))
    return hmac.new(secret.encode(), material.encode(), hashlib.sha256).hexdigest()[:40]


def derive_execution_identities(
    *, user_id: str, chat_id: str, assistant_id: str | None, run_id: str,
    kernel_scope: str = "chat_assistant",
) -> ExecutionIdentities:
    if not user_id or user_id == "anonymous":
        raise ValueError("A stable authenticated user identity is required for execution")
    if not chat_id:
        raise ValueError("A stable Open WebUI chat_id is required for execution")
    workspace_id = f"ws_{_digest('workspace', user_id)}"
    thread_id = f"th_{_digest('thread', user_id, chat_id)}"
    if kernel_scope == "user":
        kernel_parts = (user_id,)
    elif kernel_scope == "chat":
        kernel_parts = (user_id, chat_id)
    elif kernel_scope == "chat_assistant":
        kernel_parts = (user_id, chat_id, assistant_id or "idea-terminal-agent")
    else:
        raise ValueError(f"Unsupported IDEA_KERNEL_SCOPE: {kernel_scope}")
    return ExecutionIdentities(
        thread_id=thread_id,
        workspace_id=workspace_id,
        kernel_id=f"kn_{_digest('kernel', *kernel_parts)}",
        run_id=run_id,
    )
