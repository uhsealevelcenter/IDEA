"""Synchronize authorized Open WebUI documents into PaperQA libraries."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
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
PQA_MAX_DOCUMENT_BYTES = int(
    os.getenv(
        "PQA_MAX_DOCUMENT_BYTES",
        os.getenv("PQA_MAX_PDF_BYTES", str(1024 * 1024 * 1024)),
    )
)
PQA_MAX_CONVERTED_PDF_BYTES = int(
    os.getenv(
        "PQA_MAX_CONVERTED_PDF_BYTES",
        str(PQA_MAX_DOCUMENT_BYTES),
    )
)
PQA_CONVERSION_TIMEOUT_SECONDS = int(
    os.getenv("PQA_CONVERSION_TIMEOUT_SECONDS", "120")
)
PQA_SYNC_CHUNK_BYTES = 1024 * 1024
PQA_EMBEDDING_MODEL = os.getenv(
    "PQA_EMBEDDING_MODEL", "text-embedding-3-small"
)
PQA_INDEX_SCHEMA_VERSION = "2"
PQA_DOCUMENT_REPRESENTATION_VERSION = "normalized-pdf-v2"
LIBREOFFICE_BINARY = os.getenv("PQA_LIBREOFFICE_BINARY", "soffice")

_FORMAT_MIME_TYPES = {
    "pdf": frozenset({"application/pdf"}),
    "docx": frozenset({
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }),
    "doc": frozenset({"application/msword"}),
    "odt": frozenset({"application/vnd.oasis.opendocument.text"}),
    "rtf": frozenset({
        "application/rtf",
        "application/x-rtf",
        "text/rtf",
    }),
}
_MIME_FORMATS = {
    mime_type: document_format
    for document_format, mime_types in _FORMAT_MIME_TYPES.items()
    for mime_type in mime_types
}
_GENERIC_MIME_TYPES = frozenset({
    "",
    "application/octet-stream",
    "binary/octet-stream",
})

logger = logging.getLogger(__name__)
_scope_locks: dict[str, threading.Lock] = {}
_scope_locks_guard = threading.Lock()


@dataclass(frozen=True)
class PaperQALibrary:
    """Trusted PaperQA context selected for one chat turn."""

    scope_id: str
    collection_ids: tuple[str, ...]
    direct_file_ids: tuple[str, ...]
    paper_count: int
    direct_scope_id: str | None = None
    direct_file_names: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


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


def _metadata_content_type(metadata: dict) -> str:
    meta = metadata.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    return (
        str(meta.get("content_type") or "")
        .partition(";")[0]
        .strip()
        .lower()
    )


def _document_format(metadata: dict) -> str | None:
    """Return an allowlisted literature format without trusting a path."""
    filename = _metadata_name(metadata)
    suffix_format = Path(filename).suffix.lower().lstrip(".")
    if suffix_format not in _FORMAT_MIME_TYPES:
        suffix_format = ""

    content_type = _metadata_content_type(metadata)
    mime_format = _MIME_FORMATS.get(content_type)
    if suffix_format:
        if mime_format and mime_format != suffix_format:
            return None
        if content_type in _GENERIC_MIME_TYPES or mime_format:
            return suffix_format
        return None
    return mime_format


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


def _metadata_name(metadata: dict) -> str:
    meta = metadata.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    return str(
        meta.get("name") or metadata.get("filename") or ""
    ).strip()


def _metadata_size(metadata: dict) -> int | None:
    meta = metadata.get("meta") or {}
    if not isinstance(meta, dict):
        return None
    size = meta.get("size")
    return (
        size
        if isinstance(size, int) and not isinstance(size, bool)
        else None
    )


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


def _download_document(
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
                if transferred > PQA_MAX_DOCUMENT_BYTES:
                    raise RuntimeError(
                        "The document exceeds the "
                        f"{PQA_MAX_DOCUMENT_BYTES}-byte PaperQA limit."
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


def _validate_pdf(path: Path) -> int:
    try:
        size = path.stat().st_size
        with path.open("rb") as source:
            signature = source.read(5)
    except OSError as exc:
        raise RuntimeError("The generated PDF could not be read.") from exc
    if not size or signature != b"%PDF-":
        raise RuntimeError("Document processing did not produce a valid PDF.")
    if size > PQA_MAX_CONVERTED_PDF_BYTES:
        raise RuntimeError(
            "The generated PDF exceeds the "
            f"{PQA_MAX_CONVERTED_PDF_BYTES}-byte PaperQA limit."
        )
    return size


def _file_sha256(path: Path, deadline: float) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(PQA_SYNC_CHUNK_BYTES), b""):
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Timed out while synchronizing PaperQA documents."
                )
            digest.update(chunk)
    return digest.hexdigest()


def _convert_to_pdf(source: Path, output_dir: Path, deadline: float) -> Path:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("Timed out while synchronizing PaperQA documents.")
    profile_dir = output_dir / "profile"
    profile_dir.mkdir()
    timeout = min(float(PQA_CONVERSION_TIMEOUT_SECONDS), remaining)
    command = [
        LIBREOFFICE_BINARY,
        "--headless",
        "--nologo",
        "--nodefault",
        "--norestore",
        "--safe-mode",
        f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
        "--convert-to",
        "pdf:writer_pdf_Export",
        "--outdir",
        str(output_dir),
        str(source),
    ]
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Document conversion timed out.") from exc
    except OSError as exc:
        raise RuntimeError("The document converter is unavailable.") from exc
    if completed.returncode:
        logger.warning(
            "LibreOffice conversion failed with exit code %s",
            completed.returncode,
        )
        raise RuntimeError("LibreOffice could not convert the document.")
    return output_dir / f"{source.stem}.pdf"


def _materialize_pdf(
    file_id: str,
    document_format: str,
    destination: Path,
    headers: dict[str, str],
    deadline: float,
) -> tuple[int, str, int, str]:
    """Download one document and atomically install its normalized PDF."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".paperqa-convert-",
        dir=destination.parent,
    ) as temporary_directory:
        temporary_root = Path(temporary_directory)
        source = temporary_root / f"source.{document_format}"
        source_size, source_hash = _download_document(
            file_id, source, headers, deadline
        )
        if document_format == "pdf":
            converted = source
        else:
            converted = _convert_to_pdf(source, temporary_root, deadline)
        output_size = _validate_pdf(converted)
        output_hash = _file_sha256(converted, deadline)
        converted.replace(destination)
    return source_size, source_hash, output_size, output_hash


def _sync_scope(
    scope_id: str,
    file_ids: Iterable[str],
    headers: dict[str, str],
    deadline: float,
    *,
    remove_stale: bool,
) -> tuple[dict[str, dict], tuple[str, ...]]:
    """Mirror authorized documents into one scope and return state/issues."""
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
) -> tuple[dict[str, dict], tuple[str, ...]]:
    papers_dir = PAPERS_ROOT / scope_id
    papers_dir.mkdir(parents=True, exist_ok=True)
    previous = (_read_state(scope_id).get("files") or {})
    desired: dict[str, dict] = {}
    attempted_ids = set(file_ids)
    warnings: list[str] = []

    for file_id in sorted(attempted_ids):
        metadata = _file_metadata(file_id, headers, deadline)
        original_name = _metadata_name(metadata)
        display_name = original_name or "Unnamed document"
        document_format = _document_format(metadata)
        if document_format is None:
            warnings.append(
                f"Skipped {display_name!r}: unsupported or conflicting "
                "document type. Supported types are PDF, DOCX, DOC, ODT, "
                "and RTF."
            )
            continue
        advertised_size = _metadata_size(metadata)
        if (
            advertised_size is not None
            and advertised_size > PQA_MAX_DOCUMENT_BYTES
        ):
            warnings.append(
                f"Skipped {display_name!r}: the document exceeds the "
                f"{PQA_MAX_DOCUMENT_BYTES}-byte PaperQA limit."
            )
            continue
        fingerprint = _metadata_fingerprint(metadata)
        filename = f"{hashlib.sha256(file_id.encode()).hexdigest()}.pdf"
        destination = papers_dir / filename
        old = previous.get(file_id) if isinstance(previous, dict) else None
        if (
            isinstance(old, dict)
            and old.get("fingerprint") == fingerprint
            and old.get("filename") == filename
            and old.get("representation_version")
            == PQA_DOCUMENT_REPRESENTATION_VERSION
            and destination.is_file()
        ):
            desired[file_id] = {
                **old,
                "name": original_name or old.get("name", ""),
            }
            continue

        try:
            source_size, source_hash, output_size, output_hash = (
                _materialize_pdf(
                    file_id,
                    document_format,
                    destination,
                    headers,
                    deadline,
                )
            )
        except Exception as exc:
            if not isinstance(exc, RuntimeError):
                logger.exception("Unexpected PaperQA document processing error")
            reason = (
                str(exc)
                if isinstance(exc, RuntimeError)
                else "Document processing failed."
            )
            warnings.append(
                f"Skipped {display_name!r}: {reason}"
            )
            continue
        desired[file_id] = {
            "filename": filename,
            "fingerprint": fingerprint,
            "representation_version": PQA_DOCUMENT_REPRESENTATION_VERSION,
            "source_format": document_format,
            "content_type": _metadata_content_type(metadata),
            "source_sha256": source_hash,
            "source_size": source_size,
            "sha256": output_hash,
            "size": output_size,
            "name": original_name,
        }

    if isinstance(previous, dict):
        for file_id, record in previous.items():
            if file_id in desired or not isinstance(record, dict):
                continue
            filename = record.get("filename")
            existing = (
                papers_dir / filename
                if isinstance(filename, str)
                else None
            )
            if (
                not remove_stale
                and file_id not in attempted_ids
                and existing is not None
                and existing.is_file()
            ):
                desired[file_id] = record
            elif existing is not None:
                existing.unlink(missing_ok=True)

    _write_state(scope_id, {"files": desired})
    return desired, tuple(warnings)


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
    Direct documents are additive within a chat scope. A combined scope is
    created only for chats with direct documents, so collection-only chats
    reuse the same per-user/per-Assistant/per-collection index.
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
    collection_state, collection_warnings = _sync_scope(
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
        direct_state, direct_warnings = _sync_scope(
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
            direct_scope_id=chat_scope if direct_state else None,
            direct_file_names=tuple(sorted({
                str(record.get("name"))
                for record in direct_state.values()
                if isinstance(record, dict) and record.get("name")
            })),
            warnings=tuple(dict.fromkeys(
                collection_warnings + direct_warnings
            )),
        )

    return PaperQALibrary(
        scope_id=collection_scope,
        collection_ids=tuple(collection_ids),
        direct_file_ids=(),
        paper_count=len(collection_state),
        direct_scope_id=None,
        direct_file_names=(),
        warnings=collection_warnings,
    )
