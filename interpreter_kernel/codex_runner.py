"""Run one Codex SDK turn inside an IDEA user microVM.

The host writes a short-lived JSON request file and invokes this module.  The
only stdout emitted is the final JSON response, which keeps credentials out of
the command line and gives sandbox_service a stable protocol to parse.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path("/workspace")
CODEX_HOME = WORKSPACE_ROOT / ".idea" / "codex"
MAX_EVENT_TEXT = 4_000


def validate_cwd(value: str) -> Path:
    """Resolve a requested working directory and keep it inside /workspace."""
    requested = Path(value or str(WORKSPACE_ROOT))
    if not requested.is_absolute():
        requested = WORKSPACE_ROOT / requested
    resolved = requested.resolve(strict=False)
    if resolved != WORKSPACE_ROOT and WORKSPACE_ROOT not in resolved.parents:
        raise ValueError("Codex cwd must be /workspace or a directory beneath it")
    return resolved


def validate_access(value: str) -> str:
    if value not in {"read-only", "workspace-write"}:
        raise ValueError("Codex access must be 'read-only' or 'workspace-write'")
    return value


def _safe_run_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)[:160]


def cancellation_path(run_id: str) -> Path | None:
    return Path(f"/tmp/.idea_codex_cancel_{_safe_run_id(run_id)}") if run_id else None


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _dump(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_dump(item) for item in value]
    return str(value)


def _event_summary(event: Any) -> dict[str, Any]:
    payload = {
        "method": str(getattr(event, "method", type(event).__name__)),
        "payload": _dump(getattr(event, "payload", event)),
    }
    encoded = json.dumps(payload, default=str)
    if len(encoded) <= MAX_EVENT_TEXT:
        return payload
    return {"method": payload["method"], "summary": encoded[:MAX_EVENT_TEXT] + "…"}


async def _run(request: dict[str, Any]) -> dict[str, Any]:
    # Imported lazily so validation can be unit-tested outside the guest image.
    from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox

    task = str(request.get("task", "")).strip()
    if not task:
        raise ValueError("Codex task must not be empty")
    cwd = validate_cwd(str(request.get("cwd", WORKSPACE_ROOT)))
    access = validate_access(str(request.get("access", "read-only")))
    api_key = str(request.get("api_key", "")).strip()
    base_url = str(request.get("base_url", "")).rstrip("/")
    model = str(request.get("model", "")).strip()
    if not api_key or not base_url or not model:
        raise ValueError("Codex model, base_url, and scoped api_key are required")

    cwd.mkdir(parents=True, exist_ok=True)
    CODEX_HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(CODEX_HOME, 0o700)
    env = {
        "CODEX_HOME": str(CODEX_HOME),
        "IDEA_CODEX_API_KEY": api_key,
    }
    overrides = (
        'model_providers.idea.name="IDEA LiteLLM"',
        f"model_providers.idea.base_url={json.dumps(base_url)}",
        'model_providers.idea.env_key="IDEA_CODEX_API_KEY"',
        'model_providers.idea.wire_api="responses"',
        'shell_environment_policy.filters.IDEA_CODEX_API_KEY="exclude"',
    )
    client = AsyncCodex(CodexConfig(cwd=str(cwd), env=env, config_overrides=overrides))
    sandbox = Sandbox.read_only if access == "read-only" else Sandbox.workspace_write
    thread_id = str(request.get("thread_id", "")).strip()
    if thread_id:
        thread = await client.thread_resume(
            thread_id,
            model=model,
            model_provider="idea",
            cwd=str(cwd),
            approval_mode=ApprovalMode.deny_all,
            sandbox=sandbox,
        )
    else:
        thread = await client.thread_start(
            model=model,
            model_provider="idea",
            cwd=str(cwd),
            approval_mode=ApprovalMode.deny_all,
            sandbox=sandbox,
            developer_instructions=(
                "Work only inside /workspace. Do not reveal credentials or inspect "
                "IDEA service internals. Summarize changes and verification clearly."
            ),
        )

    turn = await thread.turn(task)
    cancel_file = cancellation_path(str(request.get("run_id", "")))

    async def watch_cancel() -> None:
        if cancel_file is None:
            return
        while True:
            if cancel_file.exists():
                await turn.interrupt()
                return
            await asyncio.sleep(0.25)

    watcher = asyncio.create_task(watch_cancel())
    events: list[dict[str, Any]] = []
    final_response = ""
    changed_paths: list[str] = []
    usage: dict[str, Any] = {}
    status = "completed"
    error = ""
    max_events = max(1, min(int(request.get("max_events", 100)), 500))
    try:
        async for event in turn.stream():
            method = str(getattr(event, "method", ""))
            event_payload = _dump(getattr(event, "payload", {}))
            if len(events) < max_events:
                events.append(_event_summary(event))
            item = event_payload.get("item", {}) if isinstance(event_payload, dict) else {}
            if isinstance(item, dict) and item.get("type") == "agentMessage":
                text = item.get("text") or item.get("content")
                if text:
                    final_response = str(text)
            if isinstance(item, dict) and item.get("type") == "fileChange":
                for change in item.get("changes", []):
                    if isinstance(change, dict) and change.get("path"):
                        changed_paths.append(str(change["path"]))
            if method == "thread/tokenUsage/updated" and isinstance(event_payload, dict):
                usage = event_payload.get("token_usage") or event_payload.get("tokenUsage") or event_payload
            if method == "turn/completed" and isinstance(event_payload, dict):
                turn_payload = event_payload.get("turn", {})
                turn_status = turn_payload.get("status")
                if turn_status:
                    status = str(turn_status)
                error_value = turn_payload.get("error")
                if error_value:
                    error = (
                        str(error_value.get("message") or error_value)
                        if isinstance(error_value, dict) else str(error_value)
                    )
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        await client.close()

    return {
        "ok": status == "completed" and not error,
        "status": status,
        "error": error,
        "thread_id": thread.id,
        "final_response": final_response,
        "changed_paths": sorted(set(changed_paths)),
        "usage": usage,
        "events": events,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-file", required=True)
    args = parser.parse_args()
    try:
        request = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
        result = asyncio.run(_run(request))
    except Exception as exc:
        result = {"ok": False, "status": "failed", "error": str(exc), "events": []}
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
