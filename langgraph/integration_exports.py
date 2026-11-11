"""Session-bound export of selected integration results before VM deletion.

Exported copies live outside the guest so they survive VM teardown. Every
object is listed in its session's manifest; downloads resolve only through
that manifest, never through arbitrary paths or Open WebUI file IDs.

Selection rule: regular files under /outputs, excluding hidden path
components other than the displayed-image directories, newest first, until
the per-session byte or file cap is reached.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import posixpath
import re
import secrets
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

OUTPUTS_ROOT = "/outputs"
# Where the runtime saves images it shows in the reply (plots from Python and
# view_image). They are hidden from the file listing but are part of the
# answer, so the reply needs a public URL for them.
DISPLAYED_IMAGE_DIRS = (".idea/kernel-images/", ".idea/display-images/")
EXPORT_KEY_PREFIX = "idea:isess:exports:"
EXPORT_LOCATOR_PREFIX = "idea:isess:export_locator:"
EXPORT_TOTAL_BYTES_KEY = "idea:isess:exports_total_bytes"
EXPORT_EXPIRY_INDEX_KEY = "idea:isess:exports_expiry"
# The object id is the download capability. It is unguessable, and neither a
# session id nor a workspace id is accepted in its place.
_PUBLIC_OBJECT_ID = re.compile(r"^[A-Za-z0-9_-]{22,64}$")
_PUBLIC_WORKSPACE_ID = re.compile(r"^ws_[A-Za-z0-9_-]{1,80}$")

STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class ExportLimits:
    max_session_bytes: int = 100 * 1024 * 1024
    max_session_files: int = 50
    max_total_bytes: int = 5 * 1024 * 1024 * 1024
    retention_seconds: int = 86400

    @classmethod
    def from_env(cls) -> "ExportLimits":
        return cls(
            max_session_bytes=_env_int(
                "IDEA_INTEGRATION_EXPORT_MAX_SESSION_BYTES", cls.max_session_bytes
            ),
            max_session_files=_env_int(
                "IDEA_INTEGRATION_EXPORT_MAX_SESSION_FILES", cls.max_session_files
            ),
            max_total_bytes=_env_int(
                "IDEA_INTEGRATION_EXPORT_MAX_TOTAL_BYTES", cls.max_total_bytes
            ),
            retention_seconds=_env_int(
                "IDEA_INTEGRATION_EXPORT_RETENTION_SECONDS", cls.retention_seconds
            ),
        )


@dataclass
class ExportedObject:
    object_id: str
    path: str
    size: int
    sha256: str
    content_type: str
    signature: str
    exported_at: float
    expires_at: float


@dataclass(frozen=True)
class ExportOutcome:
    status: str
    exported: list[str]
    skipped: list[str]
    errors: list[str]


def _signature_size(signature: str) -> int:
    try:
        return int(str(signature).split(":", 1)[0])
    except (TypeError, ValueError):
        return -1


def _signature_mtime(signature: str) -> float:
    try:
        return float(str(signature).split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        return 0.0


def selectable_output_path(path: str) -> bool:
    normalized = posixpath.normpath(path)
    if normalized != path or not normalized.startswith(OUTPUTS_ROOT + "/"):
        return False
    relative = normalized[len(OUTPUTS_ROOT) + 1:]
    for prefix in DISPLAYED_IMAGE_DIRS:
        if relative.startswith(prefix):
            relative = relative[len(prefix):]
            break
    return bool(relative) and not any(part.startswith(".") for part in relative.split("/"))


class IntegrationExportStore:
    def __init__(
        self,
        redis_client: Any,
        root_dir: str | os.PathLike[str],
        limits: ExportLimits | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.redis = redis_client
        self.root = Path(root_dir)
        self.limits = limits or ExportLimits()
        self.clock = clock

    def _manifest_key(self, workspace_id: str) -> str:
        return f"{EXPORT_KEY_PREFIX}{workspace_id}"

    def _object_path(self, workspace_id: str, object_id: str) -> Path:
        return self.root / workspace_id / object_id

    def manifest(self, workspace_id: str) -> list[ExportedObject]:
        raw = self.redis.hgetall(self._manifest_key(workspace_id)) or {}
        now = self.clock()
        objects = []
        for value in raw.values():
            try:
                item = ExportedObject(**json.loads(value))
            except Exception:
                continue
            if item.expires_at > now:
                objects.append(item)
        return sorted(objects, key=lambda item: item.path)

    def resolve_public(self, object_id: str) -> tuple[ExportedObject, Path] | None:
        """Resolve a download by its unguessable object id alone.

        A session id, workspace id, or Open WebUI file id is not a credential
        here, and the object id is never treated as a filesystem path.
        """
        if not isinstance(object_id, str) or not _PUBLIC_OBJECT_ID.fullmatch(object_id):
            return None
        workspace_id = self.redis.get(f"{EXPORT_LOCATOR_PREFIX}{object_id}")
        if (
            not isinstance(workspace_id, str)
            or not _PUBLIC_WORKSPACE_ID.fullmatch(workspace_id)
        ):
            return None
        found = self.get_object(workspace_id, object_id)
        if found is None:
            return None
        item, path = found
        root = self.root.resolve()
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            return None
        return item, resolved

    def get_object(self, workspace_id: str, object_id: str) -> tuple[ExportedObject, Path] | None:
        for item in self.manifest(workspace_id):
            if len(item.object_id) != len(object_id):
                continue
            if secrets.compare_digest(item.object_id, object_id):
                path = self._object_path(workspace_id, item.object_id)
                if path.is_file():
                    return item, path
        return None

    def export_outputs(
        self,
        *,
        workspace_id: str,
        list_metadata: Callable[[str], dict[str, str] | None],
        read_bytes: Callable[[str], bytes],
        retention_floor: float | None = None,
    ) -> ExportOutcome:
        """Copy new or changed selected outputs; unchanged ones are kept."""
        snapshot = list_metadata(OUTPUTS_ROOT)
        if snapshot is None:
            return ExportOutcome(STATUS_FAILED, [], [], ["could not list /outputs"])
        now = self.clock()
        expires_at = max(now, retention_floor or now) + self.limits.retention_seconds
        manifest_key = self._manifest_key(workspace_id)
        existing = {item.path: item for item in self.manifest(workspace_id)}

        candidates = sorted(
            (
                (path, signature)
                for path, signature in snapshot.items()
                if selectable_output_path(path)
            ),
            key=lambda item: (-_signature_mtime(item[1]), item[0]),
        )
        exported: list[str] = []
        skipped: list[str] = []
        errors: list[str] = []
        session_bytes = 0
        selected = 0

        for path, signature in candidates:
            size = _signature_size(signature)
            if size < 0:
                skipped.append(path)
                continue
            if (
                selected >= self.limits.max_session_files
                or session_bytes + size > self.limits.max_session_bytes
            ):
                skipped.append(path)
                continue
            previous = existing.get(path)
            if previous and previous.signature == signature:
                previous.expires_at = max(previous.expires_at, expires_at)
                self.redis.hset(manifest_key, previous.object_id, json.dumps(asdict(previous)))
                self._remember_locator(workspace_id, previous)
                selected += 1
                session_bytes += previous.size
                continue
            total = int(self.redis.get(EXPORT_TOTAL_BYTES_KEY) or 0)
            if total + size > self.limits.max_total_bytes:
                skipped.append(path)
                errors.append(f"{path}: export storage quota reached")
                continue
            try:
                data = read_bytes(path)
            except Exception as exc:
                errors.append(f"{path}: {exc}")
                continue
            if len(data) != size or session_bytes + len(data) > self.limits.max_session_bytes:
                errors.append(f"{path}: changed while exporting")
                continue
            item = self._write_object(workspace_id, path, signature, data, now, expires_at)
            if previous:
                self._delete_object(workspace_id, previous)
            self.redis.hset(manifest_key, item.object_id, json.dumps(asdict(item)))
            self._remember_locator(workspace_id, item)
            self.redis.incrby(EXPORT_TOTAL_BYTES_KEY, item.size)
            exported.append(path)
            selected += 1
            session_bytes += item.size

        self.redis.zadd(EXPORT_EXPIRY_INDEX_KEY, {workspace_id: expires_at})
        if errors:
            status = STATUS_PARTIAL if (exported or existing) else STATUS_FAILED
        elif skipped:
            status = STATUS_PARTIAL
        else:
            status = STATUS_COMPLETE
        return ExportOutcome(status, exported, skipped, errors)

    def _write_object(
        self,
        workspace_id: str,
        path: str,
        signature: str,
        data: bytes,
        now: float,
        expires_at: float,
    ) -> ExportedObject:
        object_id = secrets.token_urlsafe(24)
        directory = self.root / workspace_id
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
            handle.write(data)
            temporary = Path(handle.name)
        temporary.replace(directory / object_id)
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        item = ExportedObject(
            object_id=object_id,
            path=path,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            content_type=content_type,
            signature=signature,
            exported_at=now,
            expires_at=expires_at,
        )
        return item

    def _remember_locator(self, workspace_id: str, item: ExportedObject) -> None:
        if not _PUBLIC_OBJECT_ID.fullmatch(item.object_id):
            return
        if not _PUBLIC_WORKSPACE_ID.fullmatch(workspace_id):
            return
        ttl = max(1, int(item.expires_at - self.clock()))
        self.redis.set(
            f"{EXPORT_LOCATOR_PREFIX}{item.object_id}", workspace_id, ex=ttl,
        )

    def _delete_object(self, workspace_id: str, item: ExportedObject) -> None:
        self.redis.hdel(self._manifest_key(workspace_id), item.object_id)
        self.redis.delete(f"{EXPORT_LOCATOR_PREFIX}{item.object_id}")
        try:
            self._object_path(workspace_id, item.object_id).unlink()
        except FileNotFoundError:
            pass
        self.redis.decrby(EXPORT_TOTAL_BYTES_KEY, item.size)

    def purge_expired(self) -> int:
        """Delete exports past retention; returns the number of objects removed."""
        now = self.clock()
        removed = 0
        for workspace_id in self.redis.zrangebyscore(EXPORT_EXPIRY_INDEX_KEY, 0, now):
            raw = self.redis.hgetall(self._manifest_key(workspace_id)) or {}
            for value in raw.values():
                try:
                    item = ExportedObject(**json.loads(value))
                except Exception:
                    continue
                if item.expires_at <= now:
                    self._delete_object(workspace_id, item)
                    removed += 1
            if not self.redis.hlen(self._manifest_key(workspace_id)):
                self.redis.zrem(EXPORT_EXPIRY_INDEX_KEY, workspace_id)
                shutil.rmtree(self.root / workspace_id, ignore_errors=True)
        return removed
