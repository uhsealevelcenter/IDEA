"""Server-side derivation of conversation, workspace, kernel, and run IDs."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import unicodedata
from dataclasses import dataclass
from typing import Literal

WorkspaceScope = Literal["browser", "integration_session"]

BROWSER_SCOPE: WorkspaceScope = "browser"
INTEGRATION_SESSION_SCOPE: WorkspaceScope = "integration_session"
# sandbox_service recognizes this prefix and refuses to boot such a VM
# without the microsandbox backend and a lifecycle reservation.
INTEGRATION_SANDBOX_PREFIX = "sb_n8n_"
MAX_INTEGRATION_SESSION_ID_BYTES = 256
MAX_INTEGRATION_ID_BYTES = 128
DEFAULT_ASSISTANT_CONTEXT = "idea-terminal-agent"


@dataclass(frozen=True)
class IntegrationContext:
    integration_id: str
    integration_session_id: str


@dataclass(frozen=True)
class ExecutionIdentities:
    thread_id: str
    workspace_id: str
    kernel_id: str
    run_id: str
    sandbox_id: str = ""
    scope: WorkspaceScope = BROWSER_SCOPE


def _identity_secret() -> str:
    secret = os.getenv("IDEA_IDENTITY_SECRET") or os.getenv("INTERNAL_SERVICE_TOKEN")
    if not secret:
        # Development remains deterministic; production configuration is
        # validated by the service readiness check.
        secret = "idea-development-identity-key"
    return secret


def _digest(kind: str, *parts: str) -> str:
    material = "\x1f".join(("v1", kind, *(str(part) for part in parts)))
    return hmac.new(
        _identity_secret().encode(), material.encode(), hashlib.sha256
    ).hexdigest()[:40]


def _digest_v2(kind: str, parts: list[str]) -> str:
    # A canonical JSON array cannot be confused by delimiters inside parts.
    material = json.dumps(
        ["v2", kind, *(str(part) for part in parts)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hmac.new(
        _identity_secret().encode(), material.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:40]


def _validate_opaque_identifier(value: object, *, name: str, max_bytes: int) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{name} exceeds {max_bytes} UTF-8 bytes")
    if any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in value):
        raise ValueError(f"{name} contains control characters")
    return value


def validate_integration_session_id(value: object) -> str:
    """Accept an opaque session ID exactly as given; never normalize it."""
    return _validate_opaque_identifier(
        value,
        name="integration_session_id",
        max_bytes=MAX_INTEGRATION_SESSION_ID_BYTES,
    )


def validate_integration_id(value: object) -> str:
    return _validate_opaque_identifier(
        value, name="integration_id", max_bytes=MAX_INTEGRATION_ID_BYTES,
    )


def integration_workspace_parts(user_id: str, integration: IntegrationContext) -> list[str]:
    return [user_id, integration.integration_id, integration.integration_session_id]


def derive_integration_sandbox_id(user_id: str, integration: IntegrationContext) -> str:
    parts = integration_workspace_parts(user_id, integration)
    return INTEGRATION_SANDBOX_PREFIX + _digest_v2("sandbox-v2", parts)


def derive_integration_workspace_id(user_id: str, integration: IntegrationContext) -> str:
    parts = integration_workspace_parts(user_id, integration)
    return "ws_" + _digest_v2("workspace-v2", parts)


def is_integration_sandbox_id(sandbox_id: str) -> bool:
    return str(sandbox_id or "").startswith(INTEGRATION_SANDBOX_PREFIX)


def derive_execution_identities(
    *, user_id: str, chat_id: str, assistant_id: str | None, run_id: str,
    kernel_scope: str = "chat_assistant",
    integration: IntegrationContext | None = None,
) -> ExecutionIdentities:
    if not user_id or user_id == "anonymous":
        raise ValueError("A stable authenticated user identity is required for execution")
    if kernel_scope not in {"user", "chat", "chat_assistant"}:
        raise ValueError(f"Unsupported IDEA_KERNEL_SCOPE: {kernel_scope}")
    assistant_context = assistant_id or DEFAULT_ASSISTANT_CONTEXT

    if integration is not None:
        validate_integration_id(integration.integration_id)
        validate_integration_session_id(integration.integration_session_id)
        parts = integration_workspace_parts(user_id, integration)
        # The session is the conversation, so "user" and "chat" kernel scopes
        # both resolve to the session workspace; none fall back to the
        # owner's user-wide kernel.
        kernel_parts = (
            [*parts, assistant_context]
            if kernel_scope == "chat_assistant"
            else parts
        )
        return ExecutionIdentities(
            thread_id="th_" + _digest_v2("thread-v2", [*parts, assistant_context]),
            workspace_id=derive_integration_workspace_id(user_id, integration),
            kernel_id="kn_" + _digest_v2("kernel-v2", kernel_parts),
            run_id=run_id,
            sandbox_id=derive_integration_sandbox_id(user_id, integration),
            scope=INTEGRATION_SESSION_SCOPE,
        )

    if not chat_id:
        raise ValueError("A stable Open WebUI chat_id is required for execution")
    workspace_id = f"ws_{_digest('workspace', user_id)}"
    thread_id = f"th_{_digest('thread', user_id, chat_id)}"
    if kernel_scope == "user":
        kernel_parts_v1: tuple[str, ...] = (user_id,)
    elif kernel_scope == "chat":
        kernel_parts_v1 = (user_id, chat_id)
    else:
        kernel_parts_v1 = (user_id, chat_id, assistant_context)
    return ExecutionIdentities(
        thread_id=thread_id,
        workspace_id=workspace_id,
        kernel_id=f"kn_{_digest('kernel', *kernel_parts_v1)}",
        run_id=run_id,
        sandbox_id=str(user_id),
        scope=BROWSER_SCOPE,
    )
