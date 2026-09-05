#!/usr/bin/env python3
"""Reconcile IDEA-owned Open WebUI settings after deployment.

This intentionally leaves Open WebUI's persistent configuration enabled.
Only the LiteLLM task-model connection, hidden task-model metadata, external
task model, native code-execution settings, context-compaction settings, and
title-generation prompt are managed here; other Admin Panel changes remain
database-backed and editable.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


DEFAULT_OPENWEBUI_URL = "http://localhost:3001"
DEFAULT_LITELLM_URL = "http://litellm:8080/v1"
DEFAULT_TASK_MODEL = "gpt-5.6-luna"
DEFAULT_CONTEXT_COMPACTION_TOKEN_THRESHOLD = 136_000
LEGACY_LITELLM_URLS = {"http://litellm:4000/v1"}
TITLE_GENERATION_PROMPT = """### Task:
Generate a concise 3–5 word title summarizing the chat history.

### Guidelines:
- Do not include emoji, symbols, quotation marks, or special formatting.
- Clearly represent the main subject of the conversation.
- Write in the chat's primary language.
- Return only a raw JSON object.

### Output:
{ "title": "your concise title here" }

### Chat History:
<chat_history>
{{MESSAGES:END:2}}
</chat_history>"""
PUBLIC_READ_GRANT = {
    "principal_type": "user",
    "principal_id": "*",
    "permission": "read",
}


class ApiError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, detail: str = ""):
        message = f"{method} {path} failed with HTTP {status}"
        if detail:
            message += f": {detail}"
        super().__init__(message)
        self.status = status


def load_env_file(path: Path) -> None:
    """Load a simple Docker-style env file without overriding exported values."""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(
        f"{name} must be one of true/false, yes/no, on/off, or 1/0"
    )


class OpenWebUIClient:
    def __init__(self, base_url: str, token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")

        request = Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read()
        except HTTPError as exc:
            # Open WebUI errors normally contain {"detail": "..."}. Do not
            # echo arbitrary response bodies because config endpoints contain
            # credentials.
            detail = ""
            try:
                parsed = json.loads(exc.read().decode("utf-8"))
                if isinstance(parsed, dict) and isinstance(parsed.get("detail"), str):
                    detail = parsed["detail"]
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            raise ApiError(method, path, exc.code, detail) from exc
        except URLError as exc:
            raise RuntimeError(f"Unable to reach Open WebUI at {self.base_url}: {exc.reason}") from exc

        if not body:
            return None
        return json.loads(body.decode("utf-8"))

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return self.request("POST", path, payload)


def wait_for_openwebui(client: OpenWebUIClient, wait_seconds: int) -> None:
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            client.get("/health")
            return
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(2)


def authenticate(client: OpenWebUIClient) -> None:
    api_key = os.getenv("OPENWEBUI_API_KEY", "")
    api_key_error: ApiError | None = None
    if api_key:
        client.token = api_key
        try:
            # This endpoint requires an administrator, as do the connection
            # and hidden-model updates performed later.
            client.get("/openai/config")
            return
        except ApiError as exc:
            if exc.status not in {401, 403}:
                raise
            api_key_error = exc

    email = os.getenv("WEBUI_ADMIN_EMAIL", "")
    password = os.getenv("WEBUI_ADMIN_PASSWORD", "")
    if sys.stdin.isatty():
        if api_key_error:
            print(
                "OPENWEBUI_API_KEY was rejected for admin access; "
                "sign in to obtain a temporary deployment token."
            )
        if not email:
            email = input("OpenWebUI admin email: ").strip()
        if not password:
            password = getpass.getpass("OpenWebUI admin password: ")

    if not email or not password:
        reason = (
            f"OPENWEBUI_API_KEY was rejected ({api_key_error})"
            if api_key_error
            else "OPENWEBUI_API_KEY is not set"
        )
        raise RuntimeError(
            f"{reason}; set WEBUI_ADMIN_EMAIL/WEBUI_ADMIN_PASSWORD or run "
            "the script interactively to enter them securely"
        )

    client.token = ""
    response = client.post(
        "/api/v1/auths/signin",
        {"email": email, "password": password},
    )
    token = response.get("token") if isinstance(response, dict) else None
    if not token:
        raise RuntimeError("Open WebUI sign-in response did not include a token")
    client.token = token
    client.get("/openai/config")


def configure_litellm_connection(
    client: OpenWebUIClient,
    litellm_url: str,
    litellm_key: str,
    task_model: str,
) -> None:
    config = client.get("/openai/config")
    urls = list(config.get("OPENAI_API_BASE_URLS") or [])
    keys = list(config.get("OPENAI_API_KEYS") or [])
    api_configs = dict(config.get("OPENAI_API_CONFIGS") or {})

    normalized_url = litellm_url.rstrip("/")
    normalized_urls = [str(url).rstrip("/") for url in urls]
    if normalized_url in normalized_urls:
        index = normalized_urls.index(normalized_url)
        urls[index] = normalized_url
    else:
        legacy_indexes = [
            index
            for index, url in enumerate(normalized_urls)
            if url in LEGACY_LITELLM_URLS
        ]
        if legacy_indexes:
            # Migrate the incorrect port used by the initial configurator.
            # Reusing the same index also preserves Open WebUI's urlIdx
            # association and avoids leaving a dead connection in the UI.
            index = legacy_indexes[0]
            urls[index] = normalized_url
        else:
            index = len(urls)
            urls.append(normalized_url)

    while len(keys) < len(urls):
        keys.append("")
    keys[index] = litellm_key

    connection_config = dict(api_configs.get(str(index)) or {})
    connection_config.update(
        {
            "enable": True,
            # Only advertise Luna from this connection. Sol remains an
            # implementation detail of the IDEA Pipe/LangGraph path.
            "model_ids": [task_model],
        }
    )
    api_configs[str(index)] = connection_config

    client.post(
        "/openai/config/update",
        {
            "ENABLE_OPENAI_API": True,
            "OPENAI_API_BASE_URLS": urls,
            "OPENAI_API_KEYS": keys,
            "OPENAI_API_CONFIGS": api_configs,
        },
    )


def wait_for_model(
    client: OpenWebUIClient,
    task_model: str,
    wait_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + wait_seconds
    while True:
        response = client.get("/api/models")
        models = response.get("data", []) if isinstance(response, dict) else []
        match = next(
            (model for model in models if isinstance(model, dict) and model.get("id") == task_model),
            None,
        )
        if match:
            return match
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"{task_model!r} did not appear in Open WebUI's model catalog; "
                "verify the LiteLLM deployment and Azure model name"
            )
        time.sleep(2)


def public_read_grants(existing: list[Any] | None) -> list[dict[str, str]]:
    grants: list[dict[str, str]] = []
    for grant in existing or []:
        if not isinstance(grant, dict):
            continue
        normalized = {
            "principal_type": grant.get("principal_type"),
            "principal_id": grant.get("principal_id"),
            "permission": grant.get("permission"),
        }
        if all(isinstance(value, str) for value in normalized.values()):
            grants.append(normalized)  # type: ignore[arg-type]
    if PUBLIC_READ_GRANT not in grants:
        grants.append(dict(PUBLIC_READ_GRANT))
    return grants


def hide_task_model(
    client: OpenWebUIClient,
    task_model: str,
    catalog_model: dict[str, Any],
) -> None:
    path = f"/api/v1/models/model?id={quote(task_model, safe='')}"
    try:
        existing = client.get(path)
    except ApiError as exc:
        # Open WebUI versions differ here: the pinned fork historically
        # returned 401, while newer builds correctly return 404 when the
        # upstream model has no workspace metadata record yet.
        if exc.status not in {401, 404}:
            raise
        existing = None

    if existing:
        payload = {
            "id": task_model,
            "base_model_id": existing.get("base_model_id"),
            "name": existing.get("name") or catalog_model.get("name") or task_model,
            "meta": {**(existing.get("meta") or {}), "hidden": True},
            "params": existing.get("params") or {},
            "access_grants": public_read_grants(existing.get("access_grants")),
            "is_active": True,
        }
        client.post("/api/v1/models/model/update", payload)
    else:
        payload = {
            "id": task_model,
            "base_model_id": None,
            "name": catalog_model.get("name") or task_model,
            "meta": {"hidden": True},
            "params": {},
            # Hidden is a presentation setting. Public read keeps the model
            # available in each user's backend catalog for background tasks.
            "access_grants": [dict(PUBLIC_READ_GRANT)],
            "is_active": True,
        }
        client.post("/api/v1/models/create", payload)


def configure_task_settings(
    client: OpenWebUIClient,
    task_model: str,
    title_prompt: str,
) -> None:
    config = client.get("/api/v1/tasks/config")
    config["TASK_MODEL_EXTERNAL"] = task_model
    config["TITLE_GENERATION_PROMPT_TEMPLATE"] = title_prompt
    client.post("/api/v1/tasks/config/update", config)

    verified = client.get("/api/v1/tasks/config")
    actual_model = verified.get("TASK_MODEL_EXTERNAL")
    if actual_model != task_model:
        raise RuntimeError(
            f"External Task Model verification failed: expected {task_model!r}, "
            f"received {actual_model!r}"
        )
    actual_prompt = verified.get("TITLE_GENERATION_PROMPT_TEMPLATE")
    if actual_prompt != title_prompt:
        raise RuntimeError("Title Generation Prompt verification failed")


def configure_context_compaction(
    client: OpenWebUIClient,
    enabled: bool,
    token_threshold: int,
) -> None:
    if token_threshold < 1:
        raise RuntimeError("CONTEXT_COMPACTION_TOKEN_THRESHOLD must be positive")

    config = client.get("/api/v1/chats/config")
    # Older/currently-pinned Open WebUI releases (e.g. 0.11.0-idea.0.8) have
    # no Token Cap field at all in their /api/v1/chats/config schema - only
    # newer releases added it. Detect support instead of assuming it exists,
    # so this script keeps working against a pinned image that predates it.
    supports_token_cap = "CONTEXT_COMPACTION_TOKEN_CAP" in config
    config["ENABLE_CONTEXT_COMPACTION"] = enabled
    config["CONTEXT_COMPACTION_TOKEN_THRESHOLD"] = token_threshold
    token_cap = None
    if supports_token_cap:
        try:
            existing_token_cap = int(
                config.get("CONTEXT_COMPACTION_TOKEN_CAP") or 0
            )
        except (TypeError, ValueError):
            existing_token_cap = 0
        # Open WebUI uses the lower of the threshold and Token Cap as the
        # effective compaction threshold. Never leave a stale, lower cap
        # behind when IDEA raises its managed threshold, but preserve an
        # administrator's intentionally higher cap.
        token_cap = max(token_threshold, existing_token_cap)
        config["CONTEXT_COMPACTION_TOKEN_CAP"] = token_cap
    # Preserve the independently editable compaction prompt template.
    client.post("/api/v1/chats/config", config)

    verified = client.get("/api/v1/chats/config")
    if verified.get("ENABLE_CONTEXT_COMPACTION") is not enabled:
        raise RuntimeError("Context Compaction enabled-state verification failed")
    if verified.get("CONTEXT_COMPACTION_TOKEN_THRESHOLD") != token_threshold:
        raise RuntimeError(
            "Context Compaction threshold verification failed: expected "
            f"{token_threshold!r}, received "
            f"{verified.get('CONTEXT_COMPACTION_TOKEN_THRESHOLD')!r}"
        )
    if supports_token_cap and verified.get("CONTEXT_COMPACTION_TOKEN_CAP") != token_cap:
        raise RuntimeError(
            "Context Compaction token-cap verification failed: expected "
            f"{token_cap!r}, received "
            f"{verified.get('CONTEXT_COMPACTION_TOKEN_CAP')!r}"
        )


def configure_native_code_execution(
    client: OpenWebUIClient,
    code_execution_enabled: bool,
    code_interpreter_enabled: bool,
) -> None:
    """Reconcile Open WebUI runtimes that are separate from IDEA's kernel."""
    config = client.get("/api/v1/configs/code_execution")
    config["ENABLE_CODE_EXECUTION"] = code_execution_enabled
    config["ENABLE_CODE_INTERPRETER"] = code_interpreter_enabled
    # The endpoint expects the complete form, so preserve engine, Jupyter,
    # authentication, timeout, and prompt settings while changing only the
    # two managed enabled states.
    client.post("/api/v1/configs/code_execution", config)

    verified = client.get("/api/v1/configs/code_execution")
    if verified.get("ENABLE_CODE_EXECUTION") is not code_execution_enabled:
        raise RuntimeError("Code Execution enabled-state verification failed")
    if verified.get("ENABLE_CODE_INTERPRETER") is not code_interpreter_enabled:
        raise RuntimeError("Code Interpreter enabled-state verification failed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(__file__).resolve().parent.parent / ".env",
        help="Docker-style environment file (default: repository .env)",
    )
    parser.add_argument("--base-url", help="Host-reachable Open WebUI URL")
    parser.add_argument("--litellm-url", help="Open WebUI-reachable LiteLLM /v1 URL")
    parser.add_argument("--task-model", help="External task model ID")
    parser.add_argument(
        "--compaction-threshold",
        type=int,
        help=(
            "Context-compaction token threshold "
            f"(default: {DEFAULT_CONTEXT_COMPACTION_TOKEN_THRESHOLD})"
        ),
    )
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=120,
        help="Maximum wait for Open WebUI and the model catalog",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_file(args.env_file)

    base_url = args.base_url or os.getenv("OPENWEBUI_BASE_URL") or DEFAULT_OPENWEBUI_URL
    litellm_url = (
        args.litellm_url
        or os.getenv("OPENWEBUI_LITELLM_BASE_URL")
        or DEFAULT_LITELLM_URL
    )
    task_model = (
        args.task_model
        or os.getenv("TASK_MODEL_EXTERNAL")
        or DEFAULT_TASK_MODEL
    )
    compaction_enabled = env_bool("ENABLE_CONTEXT_COMPACTION", True)
    compaction_threshold = (
        args.compaction_threshold
        if args.compaction_threshold is not None
        else int(
            os.getenv(
                "CONTEXT_COMPACTION_TOKEN_THRESHOLD",
                str(DEFAULT_CONTEXT_COMPACTION_TOKEN_THRESHOLD),
            )
        )
    )
    code_execution_enabled = env_bool("ENABLE_CODE_EXECUTION", False)
    code_interpreter_enabled = env_bool("ENABLE_CODE_INTERPRETER", False)
    litellm_key = os.getenv("LITELLM_MASTER_KEY", "")
    if not litellm_key:
        raise RuntimeError("LITELLM_MASTER_KEY is required")

    client = OpenWebUIClient(base_url)
    wait_for_openwebui(client, args.wait_seconds)
    authenticate(client)

    print(f"Configuring Open WebUI external task model {task_model!r}...")
    configure_litellm_connection(client, litellm_url, litellm_key, task_model)
    catalog_model = wait_for_model(client, task_model, args.wait_seconds)
    hide_task_model(client, task_model, catalog_model)
    configure_task_settings(client, task_model, TITLE_GENERATION_PROMPT)
    configure_native_code_execution(
        client,
        code_execution_enabled,
        code_interpreter_enabled,
    )
    configure_context_compaction(
        client,
        compaction_enabled,
        compaction_threshold,
    )
    print(
        f"Done: {task_model!r} is the hidden External Task Model; "
        f"context compaction is {'enabled' if compaction_enabled else 'disabled'} "
        f"at {compaction_threshold} tokens; the title prompt is configured. "
        "Native code execution and Code Interpreter settings are reconciled. "
        "Other Admin Panel settings remain persistent and editable."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ApiError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
