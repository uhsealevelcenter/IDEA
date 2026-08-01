#!/usr/bin/env python3
"""Seed or reconcile IDEA Assistants in Open WebUI.

The default ``seed`` mode creates missing official Assistants while preserving
later Admin UI edits. ``--reconcile`` intentionally restores all
repository-managed fields. User-created Assistants are never changed.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote


SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPOSITORY_ROOT))

from openwebui.configure_openwebui import (  # noqa: E402
    ApiError,
    DEFAULT_OPENWEBUI_URL,
    OpenWebUIClient,
    authenticate,
    load_env_file,
    public_read_grants,
    wait_for_openwebui,
)


DEFAULT_MANIFEST = SCRIPT_DIR / "manifest.json"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Keep the official Assistants' Workspace controls consistent while allowing
# only capabilities that IDEA's Pipe owns or safely interoperates with.
OFFICIAL_ASSISTANT_CAPABILITIES = {
    "vision": True,
    "file_upload": True,
    "web_search": False,
    "image_generation": False,
    "code_interpreter": False,
    "citations": True,
    "status_updates": True,
    "memory": False,
    "builtin_tools": True,
    "terminal": False,
    "usage": False,
}
OFFICIAL_ASSISTANT_DEFAULT_FEATURE_IDS: list[str] = []
OFFICIAL_ASSISTANT_BUILTIN_TOOLS = {
    "time": True,
    "memory": False,
    "chats": False,
    "notes": False,
    "knowledge": False,
    "files": False,
    "channels": False,
    "notifications": False,
    "web_search": False,
    "image_generation": False,
    "code_interpreter": False,
    "tasks": False,
    "automations": False,
    "calendar": False,
    "subagents": False,
}


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    base_model_id = manifest.get("base_model_id")
    base_model_name = manifest.get("base_model_name")
    base_model_logo = manifest.get("base_model_logo")
    assistants = manifest.get("assistants")
    if not isinstance(base_model_id, str) or not base_model_id:
        raise RuntimeError("Assistant manifest requires a non-empty base_model_id")
    if not isinstance(base_model_name, str) or not base_model_name:
        raise RuntimeError("Assistant manifest requires a non-empty base_model_name")
    if not isinstance(base_model_logo, str) or not base_model_logo:
        raise RuntimeError("Assistant manifest requires a non-empty base_model_logo")
    if not isinstance(assistants, list) or not assistants:
        raise RuntimeError("Assistant manifest requires a non-empty assistants list")

    seen: set[str] = set()
    for assistant in assistants:
        if not isinstance(assistant, dict):
            raise RuntimeError("Every Assistant manifest entry must be an object")
        for field in ("id", "name", "description", "prompt", "logo"):
            if not isinstance(assistant.get(field), str) or not assistant[field]:
                raise RuntimeError(
                    f"Assistant manifest entry requires a non-empty {field!r}"
                )
        assistant_id = assistant["id"]
        if "paperqa_enabled" in assistant and not isinstance(
            assistant["paperqa_enabled"], bool
        ):
            raise RuntimeError(
                "Assistant paperqa_enabled must be true or false"
            )
        if assistant_id in seen:
            raise RuntimeError(f"Duplicate Assistant ID {assistant_id!r}")
        seen.add(assistant_id)

    default_id = manifest.get("default_assistant_id")
    if default_id not in seen:
        raise RuntimeError("default_assistant_id must identify a manifest Assistant")
    return manifest


def read_relative_text(
    manifest_path: Path,
    relative_path: str,
    ends_with_newline: bool = False,
) -> str:
    path = (manifest_path.parent / relative_path).resolve()
    try:
        path.relative_to(manifest_path.parent.resolve())
    except ValueError as exc:
        raise RuntimeError(f"Assistant path escapes manifest directory: {relative_path}") from exc
    text = path.read_text(encoding="utf-8")
    if not text:
        raise RuntimeError(f"Assistant prompt is empty: {path}")
    # Text files conventionally carry one final newline. The legacy SEA
    # string intentionally includes it; Welcome and Mars do not.
    if not ends_with_newline and text.endswith("\n"):
        text = text[:-1]
    return text


def png_data_uri(manifest_path: Path, relative_path: str) -> str:
    path = (manifest_path.parent / relative_path).resolve()
    try:
        path.relative_to(manifest_path.parent.resolve())
    except ValueError as exc:
        raise RuntimeError(f"Assistant path escapes manifest directory: {relative_path}") from exc
    image = path.read_bytes()
    if not image.startswith(PNG_SIGNATURE):
        raise RuntimeError(f"Assistant logo must be a PNG: {path}")
    return "data:image/png;base64," + base64.b64encode(image).decode("ascii")


def get_workspace_model(
    client: OpenWebUIClient,
    model_id: str,
) -> dict[str, Any] | None:
    path = f"/api/v1/models/model?id={quote(model_id, safe='')}"
    try:
        response = client.get(path)
    except ApiError as exc:
        if exc.status not in {401, 404}:
            raise
        return None
    return response if isinstance(response, dict) else None


def wait_for_base_model(
    client: OpenWebUIClient,
    configured_id: str,
    wait_seconds: int,
) -> dict[str, Any]:
    """Resolve a Pipe sub-model's optionally function-qualified catalog ID."""
    deadline = time.monotonic() + wait_seconds
    while True:
        response = client.get("/api/models")
        models = response.get("data", []) if isinstance(response, dict) else []
        candidates = [
            model
            for model in models
            if isinstance(model, dict)
            and isinstance(model.get("id"), str)
            and (
                model["id"] == configured_id
                or model["id"].endswith(f".{configured_id}")
            )
        ]
        exact = next(
            (model for model in candidates if model["id"] == configured_id),
            None,
        )
        if exact:
            return exact
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            ids = ", ".join(sorted(model["id"] for model in candidates))
            raise RuntimeError(
                f"Assistant base model {configured_id!r} is ambiguous in "
                f"Open WebUI's catalog: {ids}"
            )
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Assistant base model {configured_id!r} did not appear in "
                "Open WebUI's model catalog. Register and enable "
                "openwebui/functions/idea_pipe.py first."
            )
        time.sleep(2)


def official_assistant_payload(
    manifest_path: Path,
    base_model_id: str,
    definition: dict[str, Any],
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = dict((existing or {}).get("meta") or {})
    meta.update(
        {
            "description": definition["description"],
            "profile_image_url": png_data_uri(manifest_path, definition["logo"]),
            "tags": [{"name": name} for name in definition.get("tags", [])],
            "official_assistant": True,
            "paperqa_enabled": bool(definition.get("paperqa_enabled", False)),
        }
    )
    capabilities = dict(meta.get("capabilities") or {})
    capabilities.update(OFFICIAL_ASSISTANT_CAPABILITIES)
    if definition.get("paperqa_enabled"):
        # The collection descriptors still reach the Pipe in legacy mode, but
        # Open WebUI must not inject its own RAG context for the same PDFs.
        capabilities["file_context"] = False
    meta["capabilities"] = capabilities
    meta["defaultFeatureIds"] = list(
        OFFICIAL_ASSISTANT_DEFAULT_FEATURE_IDS
    )
    meta["builtinTools"] = dict(OFFICIAL_ASSISTANT_BUILTIN_TOOLS)
    params = dict((existing or {}).get("params") or {})
    params["system"] = read_relative_text(
        manifest_path,
        definition["prompt"],
        bool(definition.get("prompt_ends_with_newline")),
    )
    if definition.get("paperqa_enabled"):
        params["function_calling"] = "legacy"
    return {
        "id": definition["id"],
        "base_model_id": base_model_id,
        "name": definition["name"],
        "meta": meta,
        "params": params,
        "access_grants": public_read_grants((existing or {}).get("access_grants")),
        "is_active": True,
    }


def configure_assistant_base_model(
    client: OpenWebUIClient,
    base_model_id: str,
    base_model_name: str,
    profile_image_url: str,
    dry_run: bool,
) -> str:
    existing = get_workspace_model(client, base_model_id)
    meta = dict((existing or {}).get("meta") or {})
    # Keep the implementation model visible. It can therefore be selected
    # directly in chat and in the stock Open WebUI Assistant base-model
    # picker without requiring a custom frontend publication.
    meta["hidden"] = False
    meta["profile_image_url"] = profile_image_url
    meta.pop("assistant_base_model", None)
    payload = {
        "id": base_model_id,
        "base_model_id": (existing or {}).get("base_model_id"),
        "name": base_model_name,
        "meta": meta,
        "params": dict((existing or {}).get("params") or {}),
        "access_grants": public_read_grants((existing or {}).get("access_grants")),
        "is_active": True,
    }
    if not dry_run:
        client.post(
            "/api/v1/models/model/update" if existing else "/api/v1/models/create",
            payload,
        )
    return "updated" if existing else "created"


def deploy_assistants(
    client: OpenWebUIClient,
    manifest_path: Path,
    manifest: dict[str, Any],
    reconcile: bool,
    dry_run: bool,
    only: set[str] | None = None,
) -> dict[str, str]:
    results: dict[str, str] = {}
    for definition in manifest["assistants"]:
        assistant_id = definition["id"]
        if only and assistant_id not in only:
            continue
        existing = get_workspace_model(client, assistant_id)
        if existing and not reconcile:
            results[assistant_id] = "skipped"
            continue
        if (
            existing
            and reconcile
            and not (existing.get("meta") or {}).get("official_assistant")
        ):
            raise RuntimeError(
                f"Refusing to reconcile {assistant_id!r}: the existing record "
                "is not marked as an IDEA-managed official Assistant"
            )

        payload = official_assistant_payload(
            manifest_path,
            manifest["base_model_id"],
            definition,
            existing,
        )
        if not dry_run:
            client.post(
                "/api/v1/models/model/update" if existing else "/api/v1/models/create",
                payload,
            )
        results[assistant_id] = "updated" if existing else "created"
    return results


def configure_user_assistant_permissions(
    client: OpenWebUIClient,
    dry_run: bool,
) -> None:
    permissions = client.get("/api/v1/users/default/permissions")
    workspace = dict(permissions.get("workspace") or {})
    sharing = dict(permissions.get("sharing") or {})
    workspace["models"] = True
    sharing["models"] = False
    sharing["public_models"] = False
    payload = {
        **permissions,
        "workspace": workspace,
        "sharing": sharing,
    }
    if not dry_run:
        client.post("/api/v1/users/default/permissions", payload)


def configure_default_assistant(
    client: OpenWebUIClient,
    assistant_id: str,
    reconcile: bool,
    dry_run: bool,
) -> str:
    config = client.get("/api/v1/configs/models")
    current = str(config.get("DEFAULT_MODELS") or "").strip()
    if current and not reconcile:
        return "skipped"
    config["DEFAULT_MODELS"] = assistant_id
    if not dry_run:
        client.post("/api/v1/configs/models", config)
    return "updated"


def verify_assistants(
    client: OpenWebUIClient,
    manifest: dict[str, Any],
    expected_ids: set[str],
) -> None:
    base = get_workspace_model(client, manifest["base_model_id"])
    if not base or base.get("meta", {}).get("hidden") is not False:
        raise RuntimeError("Assistant base model visibility verification failed")
    if not str(base.get("meta", {}).get("profile_image_url") or "").startswith(
        "data:image/png;base64,"
    ):
        raise RuntimeError("Assistant base model IDEA logo verification failed")

    definitions = {
        item["id"]: item for item in manifest["assistants"] if item["id"] in expected_ids
    }
    for assistant_id, definition in definitions.items():
        model = get_workspace_model(client, assistant_id)
        if not model:
            raise RuntimeError(f"Assistant verification failed: {assistant_id!r} is missing")
        if model.get("base_model_id") != manifest["base_model_id"]:
            raise RuntimeError(f"Assistant base-model verification failed: {assistant_id!r}")
        if model.get("name") != definition["name"]:
            raise RuntimeError(f"Assistant name verification failed: {assistant_id!r}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=REPOSITORY_ROOT / ".env",
        help="Docker-style environment file (default: repository .env)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Assistant manifest JSON",
    )
    parser.add_argument("--base-url", help="Host-reachable Open WebUI URL")
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help="Restore repository-managed fields on existing official Assistants",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report actions without changing Open WebUI",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="ASSISTANT_ID",
        help="Deploy only the named Assistant (repeatable)",
    )
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=120,
        help="Maximum wait for Open WebUI and its model catalog",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_file(args.env_file)
    manifest_path = args.manifest.resolve()
    manifest = load_manifest(manifest_path)
    known_ids = {item["id"] for item in manifest["assistants"]}
    only = set(args.only) or None
    unknown = (only or set()) - known_ids
    if unknown:
        raise RuntimeError(f"Unknown Assistant ID(s): {', '.join(sorted(unknown))}")

    base_url = args.base_url or os.getenv("OPENWEBUI_BASE_URL") or DEFAULT_OPENWEBUI_URL
    client = OpenWebUIClient(base_url)
    wait_for_openwebui(client, args.wait_seconds)
    authenticate(client)

    configured_base_model_id = manifest["base_model_id"]
    base_model = wait_for_base_model(
        client,
        configured_base_model_id,
        args.wait_seconds,
    )
    resolved_base_model_id = base_model["id"]
    # Open WebUI qualifies manifold Pipe sub-models as
    # "<function-id>.<sub-model-id>". Keep the readable sub-model ID in the
    # manifest, but use the live catalog ID in every API payload.
    manifest = {**manifest, "base_model_id": resolved_base_model_id}
    if resolved_base_model_id != configured_base_model_id:
        print(
            f"Resolved Assistant base model {configured_base_model_id!r} "
            f"to {resolved_base_model_id!r}."
        )
    base_action = configure_assistant_base_model(
        client,
        resolved_base_model_id,
        manifest["base_model_name"],
        png_data_uri(manifest_path, manifest["base_model_logo"]),
        args.dry_run,
    )
    configure_user_assistant_permissions(client, args.dry_run)
    results = deploy_assistants(
        client,
        manifest_path,
        manifest,
        args.reconcile,
        args.dry_run,
        only,
    )
    if only and manifest["default_assistant_id"] not in only:
        default_action = "skipped"
    else:
        default_action = configure_default_assistant(
            client,
            manifest["default_assistant_id"],
            args.reconcile,
            args.dry_run,
        )

    if not args.dry_run:
        changed_ids = {
            assistant_id
            for assistant_id, action in results.items()
            if action != "skipped"
        }
        verify_assistants(client, manifest, changed_ids)

    prefix = "Would apply" if args.dry_run else "Applied"
    print(f"{prefix} Assistant base model: {base_action}")
    for assistant_id, action in results.items():
        print(f"{prefix} {assistant_id}: {action}")
    print(f"{prefix} default Assistant: {default_action}")
    print(
        "Verified users may create private Assistants; public and user-to-user "
        "Assistant sharing remain disabled."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ApiError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
