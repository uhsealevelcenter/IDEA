"""Synchronize authorized Open WebUI PDFs into isolated PaperQA libraries."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import requests


OPENWEBUI_BASE_URL = os.getenv(
    "OPENWEBUI_BASE_URL", "http://openwebui:8080"
).rstrip("/")
PQA_HOME = Path(os.getenv("PQA_HOME", "/app/data"))
PQA_ROOT = PQA_HOME / ".pqa"
PAPERS_ROOT = Path(
    os.getenv("PAPER_DIRECTORY", str(PQA_HOME / "papers"))
)
LIBRARY_STATE_ROOT = PQA_ROOT / "libraries"
PQA_SYNC_TIMEOUT_SECONDS = int(
    os.getenv("PQA_SYNC_TIMEOUT_SECONDS", "300")
)
PQA_MAX_PDF_BYTES = int(
    os.getenv("PQA_MAX_PDF_BYTES", str(1024 * 1024 * 1024))
)
PQA_SYNC_CHUNK_BYTES = 1024 * 1024
PQA_EMBEDDING_MODEL = os.getenv(
    "PQA_EMBEDDING_MODEL", "text-embedding-3-small"
)
PQA_INDEX_SCHEMA_VERSION = "1"
_scope_locks: dict[str, threading.Lock] = {}
_scope_locks_guard = threading.Lock()


@dataclass(frozen=True)
class PaperQALibrary:
    """Trusted PaperQA context selected for one chat turn."""

    scope_id: str
    collection_ids: tuple[str, ...]
    direct_file_ids: tuple[str, ...]
    paper_count: int


def _opaque_id(prefix: str, *parts: Any) -> str:
    serialized = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"{prefix}-{digest}"


def _scope_lock(scope_id: str) -> threading.Lock:
    with _scope_locks_guard:
        return _scope_locks.setdefault(scope_id, threading.Lock())


def _safe_resource_id(value: Any) -> str | None:
    if (
        isinstance(value, str)
        and value
        and "/" not in value
        and "\\" not in value
        and not value.startswith(("http://", "https://", "data:"))
        and all(ord(char) >= 32 for char in value)
    ):
        return value
    return None


def _resource_ids(resources: Iterable[dict]) -> tuple[list[str], list[str]]:
    collection_ids: list[str] = []
    direct_ids: list[str] = []
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        resource_id = _safe_resource_id(resource.get("id"))
        if not resource_id:
            continue
        if resource.get("type") == "collection":
            if resource_id not in collection_ids:
                collection_ids.append(resource_id)
        elif resource_id not in direct_ids:
            direct_ids.append(resource_id)
    return sorted(collection_ids), sorted(direct_ids)


def _request_json(
    path: str,
    headers: dict[str, str],
    deadline: float,
) -> dict:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("Timed out while synchronizing PaperQA documents.")
    response = requests.get(
        f"{OPENWEBUI_BASE_URL}{path}",
        headers=headers,
        timeout=(min(5, remaining), remaining),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Open WebUI returned invalid JSON.")
    return payload


def _collection_file_ids(
    collection_id: str,
    headers: dict[str, str],
    deadline: float,
) -> list[str]:
    """Return every file in an authorized collection, across API pages."""
    encoded = quote(collection_id, safe="")
    page = 1
    file_ids: list[str] = []
    while True:
        payload = _request_json(
            f"/api/v1/knowledge/{encoded}/files?page={page}",
            headers,
            deadline,
        )
        items = payload.get("items") or []
        if not isinstance(items, list):
            raise RuntimeError(
                f"Open WebUI returned invalid files for collection "
                f"{collection_id!r}."
            )
        for item in items:
            if not isinstance(item, dict):
                continue
            file_id = _safe_resource_id(item.get("id"))
            if file_id and file_id not in file_ids:
                file_ids.append(file_id)
        total = payload.get("total")
        if (
            not items
            or not isinstance(total, int)
            or len(file_ids) >= total
        ):
            return file_ids
        page += 1


def _file_metadata(
    file_id: str,
    headers: dict[str, str],
    deadline: float,
) -> dict:
    return _request_json(
        f"/api/v1/files/{quote(file_id, safe='')}",
        headers,
        deadline,
    )


def _is_pdf(metadata: dict) -> bool:
    meta = metadata.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    content_type = str(meta.get("content_type") or "").lower()
    filename = str(
        meta.get("name") or metadata.get("filename") or ""
    ).lower()
    return content_type == "application/pdf" or filename.endswith(".pdf")


def _metadata_fingerprint(metadata: dict) -> str:
    meta = metadata.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    stable = {
        "hash": metadata.get("hash"),
        "updated_at": metadata.get("updated_at"),
        "size": meta.get("size"),
        "name": meta.get("name") or metadata.get("filename"),
        "content_type": meta.get("content_type"),
    }
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _state_path(scope_id: str) -> Path:
    return LIBRARY_STATE_ROOT / f"{scope_id}.json"


def _read_state(scope_id: str) -> dict:
    path = _state_path(scope_id)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_state(scope_id: str, payload: dict) -> None:
    LIBRARY_STATE_ROOT.mkdir(parents=True, exist_ok=True)
    destination = _state_path(scope_id)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(destination)


def _download_pdf(
    file_id: str,
    destination: Path,
    headers: dict[str, str],
    deadline: float,
) -> tuple[int, str]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("Timed out while synchronizing PaperQA documents.")
    response = requests.get(
        (
            f"{OPENWEBUI_BASE_URL}/api/v1/files/"
            f"{quote(file_id, safe='')}/content"
        ),
        headers=headers,
        stream=True,
        timeout=(min(5, remaining), remaining),
    )
    response.raise_for_status()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".part")
    transferred = 0
    digest = hashlib.sha256()
    try:
        with temporary.open("wb") as output:
            for chunk in response.iter_content(
                chunk_size=PQA_SYNC_CHUNK_BYTES
            ):
                if not chunk:
                    continue
                transferred += len(chunk)
                if transferred > PQA_MAX_PDF_BYTES:
                    raise RuntimeError(
                        f"PDF {file_id!r} exceeds the "
                        f"{PQA_MAX_PDF_BYTES}-byte PaperQA limit."
                    )
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Timed out while synchronizing PaperQA documents."
                    )
                digest.update(chunk)
                output.write(chunk)
        temporary.replace(destination)
    finally:
        response.close()
        temporary.unlink(missing_ok=True)
    return transferred, digest.hexdigest()


def _sync_scope(
    scope_id: str,
    file_ids: Iterable[str],
    headers: dict[str, str],
    deadline: float,
    *,
    remove_stale: bool,
) -> dict[str, dict]:
    """Mirror authorized PDFs into one scope and return its file state."""
    with _scope_lock(scope_id):
        return _sync_scope_unlocked(
            scope_id,
            file_ids,
            headers,
            deadline,
            remove_stale=remove_stale,
        )


def _sync_scope_unlocked(
    scope_id: str,
    file_ids: Iterable[str],
    headers: dict[str, str],
    deadline: float,
    *,
    remove_stale: bool,
) -> dict[str, dict]:
    papers_dir = PAPERS_ROOT / scope_id
    papers_dir.mkdir(parents=True, exist_ok=True)
    previous = (_read_state(scope_id).get("files") or {})
    desired: dict[str, dict] = {}

    for file_id in sorted(set(file_ids)):
        metadata = _file_metadata(file_id, headers, deadline)
        if not _is_pdf(metadata):
            continue
        fingerprint = _metadata_fingerprint(metadata)
        filename = f"{hashlib.sha256(file_id.encode()).hexdigest()}.pdf"
        destination = papers_dir / filename
        old = previous.get(file_id) if isinstance(previous, dict) else None
        if (
            isinstance(old, dict)
            and old.get("fingerprint") == fingerprint
            and old.get("filename") == filename
            and destination.is_file()
        ):
            desired[file_id] = old
            continue

        size, content_hash = _download_pdf(
            file_id, destination, headers, deadline
        )
        desired[file_id] = {
            "filename": filename,
            "fingerprint": fingerprint,
            "sha256": content_hash,
            "size": size,
        }

    if not remove_stale and isinstance(previous, dict):
        for file_id, record in previous.items():
            if file_id in desired or not isinstance(record, dict):
                continue
            filename = record.get("filename")
            if isinstance(filename, str) and (papers_dir / filename).is_file():
                desired[file_id] = record

    if remove_stale and isinstance(previous, dict):
        for file_id, record in previous.items():
            if file_id in desired or not isinstance(record, dict):
                continue
            filename = record.get("filename")
            if isinstance(filename, str):
                (papers_dir / filename).unlink(missing_ok=True)

    _write_state(scope_id, {"files": desired})
    return desired


def _materialize_combined_scope(
    scope_id: str,
    source_scopes: Iterable[str],
) -> dict[str, dict]:
    """Mirror source PDFs into an effective scope using hardlinks when possible."""
    with _scope_lock(scope_id):
        return _materialize_combined_scope_unlocked(
            scope_id, source_scopes
        )


def _materialize_combined_scope_unlocked(
    scope_id: str,
    source_scopes: Iterable[str],
) -> dict[str, dict]:
    destination_dir = PAPERS_ROOT / scope_id
    destination_dir.mkdir(parents=True, exist_ok=True)
    previous = _read_state(scope_id).get("files") or {}
    desired: dict[str, dict] = {}
    desired_names: set[str] = set()

    for source_scope in source_scopes:
        source_dir = PAPERS_ROOT / source_scope
        source_files = _read_state(source_scope).get("files") or {}
        if not isinstance(source_files, dict):
            continue
        for file_id, record in source_files.items():
            if not isinstance(record, dict):
                continue
            source_name = record.get("filename")
            if not isinstance(source_name, str):
                continue
            source = source_dir / source_name
            if not source.is_file():
                continue
            destination_name = (
                f"{hashlib.sha256(file_id.encode()).hexdigest()}.pdf"
            )
            destination = destination_dir / destination_name
            old_record = (
                previous.get(file_id)
                if isinstance(previous, dict)
                else None
            )
            if (
                destination.exists()
                and (
                    not isinstance(old_record, dict)
                    or old_record.get("sha256") != record.get("sha256")
                )
            ):
                destination.unlink()
            if not destination.exists():
                try:
                    os.link(source, destination)
                except OSError:
                    shutil.copy2(source, destination)
            desired_names.add(destination_name)
            desired[file_id] = {**record, "filename": destination_name}

    for existing in destination_dir.glob("*.pdf"):
        if existing.name not in desired_names:
            existing.unlink()
    _write_state(scope_id, {"files": desired})
    return desired


def prepare_paperqa_library(
    *,
    user_id: str,
    assistant_id: str,
    session_id: str,
    resources: Iterable[dict],
    authorization: str,
) -> PaperQALibrary:
    """Resolve, authorize, and synchronize a turn's PaperQA library.

    Collection scopes are persistent and mirrored to current membership.
    Direct PDFs are additive within a chat scope. A combined scope is created
    only for chats with direct PDFs, so collection-only chats reuse the same
    per-user/per-Assistant/per-collection index.
    """
    if not authorization:
        raise RuntimeError(
            "Cannot prepare PaperQA documents because the current Open "
            "WebUI user credential was not forwarded."
        )
    collection_ids, direct_ids = _resource_ids(resources)
    headers = {"Authorization": authorization}
    deadline = time.monotonic() + PQA_SYNC_TIMEOUT_SECONDS

    collection_file_ids: list[str] = []
    for collection_id in collection_ids:
        for file_id in _collection_file_ids(
            collection_id, headers, deadline
        ):
            if file_id not in collection_file_ids:
                collection_file_ids.append(file_id)

    collection_scope = _opaque_id(
        "collection",
        PQA_INDEX_SCHEMA_VERSION,
        PQA_EMBEDDING_MODEL,
        user_id,
        assistant_id,
        collection_ids,
    )
    collection_state = _sync_scope(
        collection_scope,
        collection_file_ids,
        headers,
        deadline,
        remove_stale=True,
    )

    chat_scope = _opaque_id(
        "chat", user_id, assistant_id, session_id
    )
    prior_chat_state = _read_state(chat_scope).get("files") or {}
    if direct_ids or prior_chat_state:
        direct_state = _sync_scope(
            chat_scope,
            direct_ids,
            headers,
            deadline,
            remove_stale=False,
        )
        combined_scope = _opaque_id(
            "combined", collection_scope, chat_scope
        )
        combined = _materialize_combined_scope(
            combined_scope, (collection_scope, chat_scope)
        )
        return PaperQALibrary(
            scope_id=combined_scope,
            collection_ids=tuple(collection_ids),
            direct_file_ids=tuple(sorted(direct_state)),
            paper_count=len(combined),
        )

    return PaperQALibrary(
        scope_id=collection_scope,
        collection_ids=tuple(collection_ids),
        direct_file_ids=(),
        paper_count=len(collection_state),
    )
