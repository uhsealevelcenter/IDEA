"""Persistent per-user mappings from sandbox output paths to Open WebUI files."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable

import redis


REGISTRY_PREFIX = os.getenv(
    "ARTIFACT_REGISTRY_PREFIX",
    "idea:artifact-registry",
)


@dataclass(frozen=True)
class ArtifactRecord:
    sandbox_path: str
    signature: str
    openwebui_file_id: str
    display_name: str
    uploaded_at: str
    schema_version: int = 1

    @classmethod
    def from_json(cls, value: str) -> "ArtifactRecord":
        payload = json.loads(value)
        return cls(
            sandbox_path=payload["sandbox_path"],
            signature=payload["signature"],
            openwebui_file_id=payload["openwebui_file_id"],
            display_name=payload["display_name"],
            uploaded_at=payload["uploaded_at"],
            schema_version=int(payload.get("schema_version", 1)),
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)


def _signature_modified_at(signature: str) -> float:
    """Return the mtime component from a size:mtime signature."""
    try:
        return float(signature.rsplit(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        return 0.0


class ArtifactRegistry:
    """Redis-backed latest-version index; Open WebUI remains the byte store."""

    def __init__(
        self,
        user_id: str,
        client: redis.Redis | None = None,
    ):
        user_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        self.key = f"{REGISTRY_PREFIX}:{user_hash}"
        self.client = client or redis.Redis(
            host=os.getenv("REDIS_HOST", "redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            decode_responses=True,
        )

    def get_many(self, paths: Iterable[str]) -> dict[str, ArtifactRecord]:
        ordered_paths = list(dict.fromkeys(paths))
        if not ordered_paths:
            return {}
        raw_records = self.client.hmget(self.key, ordered_paths)
        records: dict[str, ArtifactRecord] = {}
        for path, raw_record in zip(ordered_paths, raw_records):
            if not raw_record:
                continue
            try:
                record = ArtifactRecord.from_json(raw_record)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if record.sandbox_path == path and record.openwebui_file_id:
                records[path] = record
        return records

    def upsert(
        self,
        sandbox_path: str,
        signature: str,
        openwebui_file_id: str,
        display_name: str,
    ) -> bool:
        """
        Store the newest known version.

        Optimistic locking plus the signature mtime prevents a slower,
        older concurrent turn from replacing a mapping written by a newer
        turn for the same user/path.
        """
        record = ArtifactRecord(
            sandbox_path=sandbox_path,
            signature=signature,
            openwebui_file_id=openwebui_file_id,
            display_name=display_name,
            uploaded_at=datetime.now(timezone.utc).isoformat(),
        )
        while True:
            try:
                with self.client.pipeline() as pipeline:
                    pipeline.watch(self.key)
                    current_raw = pipeline.hget(self.key, sandbox_path)
                    if current_raw:
                        try:
                            current = ArtifactRecord.from_json(current_raw)
                        except (
                            KeyError,
                            TypeError,
                            ValueError,
                            json.JSONDecodeError,
                        ):
                            current = None
                        if (
                            current
                            and _signature_modified_at(current.signature)
                            > _signature_modified_at(signature)
                        ):
                            pipeline.unwatch()
                            return False
                    pipeline.multi()
                    pipeline.hset(self.key, sandbox_path, record.to_json())
                    pipeline.execute()
                    return True
            except redis.WatchError:
                continue

    def remove_many(self, paths: Iterable[str]) -> None:
        paths = list(dict.fromkeys(paths))
        if paths:
            self.client.hdel(self.key, *paths)
