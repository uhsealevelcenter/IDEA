#!/usr/bin/env python3
"""Create service env files from IDEA's legacy root ``.env``.

The deployment layout before the service split kept every credential in the
repository root. Existing servers must retain those credentials when they
start using per-service env files, especially the PostgreSQL superuser,
OpenWebUI, internal-service, identity, and Langfuse values.

This migration is one-shot and non-destructive: it only creates missing
service env files, never rewrites an existing one, and leaves the root file in
place for older operational tooling.
"""

from __future__ import annotations

import re
import secrets
import shlex
import sys
from pathlib import Path
from urllib.parse import quote


SERVICES = (
    "db",
    "redis",
    "nginx",
    "langgraph",
    "sandbox_service",
    "litellm",
    "langfuse",
    "openwebui",
)
ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        match = ENV_LINE.match(raw_line)
        if not match:
            continue
        key, value = match.groups()
        value = value.strip()
        if value.startswith(("'", '"')):
            parsed = shlex.split(value, comments=True, posix=True)
            value = parsed[0] if parsed else ""
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        values[key] = value
    return values


def first(values: dict[str, str], *keys: str, default: str = "") -> str:
    for key in keys:
        if values.get(key):
            return values[key]
    return default


def env_value(value: str) -> str:
    return shlex.quote(value) if value else ""


def render_template(template: Path, output: Path, updates: dict[str, str]) -> None:
    lines = template.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    for index, line in enumerate(lines):
        match = ENV_LINE.match(line)
        if not match or match.group(1) not in updates:
            continue
        key = match.group(1)
        lines[index] = f"{key}={env_value(updates[key])}"
        seen.add(key)
    for key, value in updates.items():
        if key not in seen:
            lines.append(f"{key}={env_value(value)}")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(output)
    output.chmod(0o600)


def migration_values(legacy: dict[str, str]) -> dict[str, dict[str, str]]:
    postgres_user = first(legacy, "POSTGRES_USER", default="idea_user")
    postgres_password = first(legacy, "POSTGRES_PASSWORD", default=secrets.token_urlsafe(24))
    postgres_db = first(legacy, "POSTGRES_DB", default="idea_db")
    postgres_server = first(legacy, "POSTGRES_SERVER", default="db")
    postgres_port = first(legacy, "POSTGRES_PORT", default="5432")

    langgraph_password = first(
        legacy, "LANGGRAPH_DB_PASSWORD", default=secrets.token_hex(16)
    )
    litellm_password = first(
        legacy, "LITELLM_DB_PASSWORD", default=secrets.token_hex(16)
    )
    langfuse_password = first(
        legacy, "LANGFUSE_DB_PASSWORD", default=secrets.token_hex(16)
    )
    internal_token = first(
        legacy, "INTERNAL_SERVICE_TOKEN", default=secrets.token_hex(32)
    )
    # Older deployments derived identities from INTERNAL_SERVICE_TOKEN. Keep
    # that derivation stable so existing workspaces and kernels remain reachable.
    identity_secret = first(legacy, "IDEA_IDENTITY_SECRET", default=internal_token)
    langgraph_aes = first(
        legacy, "LANGGRAPH_AES_KEY", default=secrets.token_hex(16)
    )

    langfuse_public = first(
        legacy,
        "LANGFUSE_PUBLIC_KEY",
        "LITELLM_LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_INIT_PUBLIC_KEY",
        default=f"pk-lf-{secrets.token_hex(16)}",
    )
    langfuse_secret = first(
        legacy,
        "LANGFUSE_SECRET_KEY",
        "LITELLM_LANGFUSE_SECRET_KEY",
        "LANGFUSE_INIT_SECRET_KEY",
        default=f"sk-lf-{secrets.token_hex(16)}",
    )

    database = quote(postgres_db, safe="")
    langgraph_url = (
        f"postgresql://idea_langgraph:{quote(langgraph_password, safe='')}"
        f"@db:5432/{database}?options=-csearch_path%3Didea_langgraph%2Cpublic"
    )
    litellm_url = (
        f"postgresql://litellm:{quote(litellm_password, safe='')}"
        f"@db:5432/{database}?schema=litellm"
    )
    langfuse_url = (
        f"postgresql://langfuse:{quote(langfuse_password, safe='')}"
        f"@db:5432/{database}?schema=langfuse"
    )

    common_db = {
        "POSTGRES_SERVER": postgres_server,
        "POSTGRES_PORT": postgres_port,
        "POSTGRES_DB": postgres_db,
    }
    return {
        "db": {
            "POSTGRES_USER": postgres_user,
            "POSTGRES_PASSWORD": postgres_password,
            "POSTGRES_DB": postgres_db,
            "POSTGRES_SERVER": postgres_server,
            "POSTGRES_PORT": postgres_port,
        },
        "redis": {},
        "nginx": {},
        "langgraph": {
            **common_db,
            "LANGGRAPH_DB_PASSWORD": langgraph_password,
            "LANGGRAPH_DATABASE_URL": langgraph_url,
            "LANGGRAPH_AES_KEY": langgraph_aes,
            "IDEA_IDENTITY_SECRET": identity_secret,
            "OPENAI_API_KEY": first(legacy, "OPENAI_API_KEY"),
            "OPENAI_BASE_URL": first(legacy, "OPENAI_BASE_URL"),
            "INTERNAL_SERVICE_TOKEN": internal_token,
            "OPENWEBUI_BASE_URL": "http://openwebui:8080",
            "OPENWEBUI_API_KEY": first(legacy, "OPENWEBUI_API_KEY"),
            "LITELLM_VIRTUAL_KEY": first(legacy, "LITELLM_VIRTUAL_KEY"),
            "SEMANTIC_SCHOLAR_API_KEY": first(legacy, "SEMANTIC_SCHOLAR_API_KEY"),
        },
        "sandbox_service": {
            "KVM_DEVICE_PATH": first(legacy, "KVM_DEVICE_PATH", default="/dev/null"),
            "SANDBOX_BACKEND": first(legacy, "SANDBOX_BACKEND", default="auto"),
            "SANDBOX_IMAGE": first(
                legacy,
                "SANDBOX_IMAGE",
                default="ghcr.io/uhsealevelcenter/idea-oi-kernel:slim",
            ),
            "GHCR_USERNAME": first(legacy, "GHCR_USERNAME"),
            "GHCR_PAT": first(legacy, "GHCR_PAT"),
            "INTERNAL_SERVICE_TOKEN": internal_token,
        },
        "litellm": {
            **common_db,
            "LITELLM_DB_PASSWORD": litellm_password,
            "LITELLM_DATABASE_URL": litellm_url,
            "LITELLM_MASTER_KEY": first(
                legacy, "LITELLM_MASTER_KEY", default=f"sk-{secrets.token_hex(32)}"
            ),
            "LANGFUSE_PUBLIC_KEY": langfuse_public,
            "LANGFUSE_SECRET_KEY": langfuse_secret,
            "OPENAI_API_KEY": first(legacy, "OPENAI_API_KEY"),
            "OPENAI_BASE_URL": first(legacy, "OPENAI_BASE_URL"),
        },
        "langfuse": {
            **common_db,
            "LANGFUSE_DB_PASSWORD": langfuse_password,
            "DATABASE_URL": langfuse_url,
            "NEXTAUTH_SECRET": first(
                legacy,
                "NEXTAUTH_SECRET",
                "LANGFUSE_NEXTAUTH_SECRET",
                default=secrets.token_hex(32),
            ),
            "SALT": first(
                legacy, "SALT", "LANGFUSE_SALT", default=secrets.token_hex(32)
            ),
            "ENCRYPTION_KEY": first(
                legacy,
                "ENCRYPTION_KEY",
                "LANGFUSE_ENCRYPTION_KEY",
                default=secrets.token_hex(32),
            ),
            "NEXTAUTH_URL": first(
                legacy,
                "NEXTAUTH_URL",
                "LANGFUSE_BASE_URL",
                default="http://localhost:3050",
            ),
            "AUTH_DISABLE_SIGNUP": first(legacy, "AUTH_DISABLE_SIGNUP", default="true"),
            "LANGFUSE_INIT_ORG_ID": first(
                legacy, "LANGFUSE_INIT_ORG_ID", default="IDEA_admin"
            ),
            "LANGFUSE_INIT_ORG_NAME": first(
                legacy, "LANGFUSE_INIT_ORG_NAME", default="IDEA_admin"
            ),
            "LANGFUSE_INIT_PROJECT_ID": first(
                legacy, "LANGFUSE_INIT_PROJECT_ID", default="Idea"
            ),
            "LANGFUSE_INIT_PROJECT_NAME": first(
                legacy, "LANGFUSE_INIT_PROJECT_NAME", default="Idea_proj"
            ),
            "LANGFUSE_INIT_USER_EMAIL": first(legacy, "LANGFUSE_INIT_USER_EMAIL"),
            "LANGFUSE_INIT_USER_NAME": first(legacy, "LANGFUSE_INIT_USER_NAME"),
            "LANGFUSE_INIT_USER_PASSWORD": first(
                legacy, "LANGFUSE_INIT_USER_PASSWORD"
            ),
            "LANGFUSE_INIT_PROJECT_PUBLIC_KEY": langfuse_public,
            "LANGFUSE_INIT_PROJECT_SECRET_KEY": langfuse_secret,
        },
        "openwebui": {
            "WEBUI_SECRET_KEY": first(legacy, "WEBUI_SECRET_KEY"),
            "ENABLE_SIGNUP": first(legacy, "ENABLE_SIGNUP", default="true"),
            "DEFAULT_USER_ROLE": first(legacy, "DEFAULT_USER_ROLE", default="pending"),
            "TASK_MODEL_EXTERNAL": first(
                legacy, "TASK_MODEL_EXTERNAL", default="gpt-6-luna"
            ),
            "ENABLE_CONTEXT_COMPACTION": first(
                legacy, "ENABLE_CONTEXT_COMPACTION", default="true"
            ),
            "CONTEXT_COMPACTION_TOKEN_THRESHOLD": first(
                legacy, "CONTEXT_COMPACTION_TOKEN_THRESHOLD", default="136000"
            ),
            "OPENWEBUI_LITELLM_BASE_URL": first(
                legacy,
                "OPENWEBUI_LITELLM_BASE_URL",
                default="http://litellm:8080/v1",
            ),
            # Existing installations authenticate post-deploy operations with
            # OPENWEBUI_API_KEY. Do not reset the administrator login.
            "WEBUI_ADMIN_EMAIL": "",
            "WEBUI_ADMIN_PASSWORD": "",
            "OPENWEBUI_API_KEY": first(legacy, "OPENWEBUI_API_KEY"),
            "CORS_ORIGINS": first(legacy, "CORS_ORIGINS"),
        },
    }


def migrate(root: Path) -> list[Path]:
    legacy_path = root / ".env"
    if not legacy_path.is_file():
        return []
    values = migration_values(parse_env(legacy_path))
    created: list[Path] = []
    for service in SERVICES:
        output = root / service / ".env"
        if output.exists():
            continue
        template = root / service / ".env.example"
        if not template.is_file():
            raise FileNotFoundError(f"Missing service environment template: {template}")
        render_template(template, output, values[service])
        created.append(output)
    return created


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        created = migrate(root)
    except (OSError, ValueError) as exc:
        print(f"Legacy environment migration failed: {exc}", file=sys.stderr)
        return 1
    if created:
        print("Migrated legacy root .env into service files:")
        for path in created:
            print(f"  -> {path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
