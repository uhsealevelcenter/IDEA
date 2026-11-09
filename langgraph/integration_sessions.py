"""Lifecycle, admission, and capacity reservations for integration sessions.

An integration session (for example, one public n8n chat conversation) owns
one ephemeral microVM. All state lives in Redis so admission decisions stay
atomic across LangGraph worker threads and service replicas: every mutation
for one (owner, integration) pair runs under a single Redis lock.

State machine::

    OPEN -> CLOSING -> EXPORTING -> DELETING -> DELETED

DELETED records remain as tombstones so a message to an ended conversation is
rejected instead of silently recreating its filesystem.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

STATE_OPEN = "OPEN"
STATE_CLOSING = "CLOSING"
STATE_EXPORTING = "EXPORTING"
STATE_DELETING = "DELETING"
STATE_DELETED = "DELETED"
ACTIVE_STATES = {STATE_OPEN, STATE_CLOSING, STATE_EXPORTING, STATE_DELETING}

REASON_EXPLICIT_END = "explicit_end"
REASON_IDLE_EXPIRY = "idle_expiry"
REASON_ABSOLUTE_LIFETIME = "absolute_lifetime"
REASON_QUOTA_EXHAUSTED = "quota_exhausted"
REASON_OPERATOR = "operator_action"
REASON_FAILURE = "failure"

KEY_PREFIX = "idea:isess:"
# sandbox_service reads this key before booting an integration VM; the format
# must match sandbox_service/terminal_registry.py.
VM_RESERVATION_KEY_PREFIX = "idea:isess:vm:"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    value = int(raw)
    if value < 0:
        raise ValueError(f"{name} must be non-negative, got {raw!r}")
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class IntegrationLimits:
    max_open_sessions: int = 4
    max_resident_vms: int = 2
    max_retained_disks: int = 4
    max_reserved_disk_mib: int = 40960
    vm_disk_mib: int = 10240
    new_sessions_per_hour: int = 20
    session_messages_per_minute: int = 6
    messages_per_minute: int = 30
    max_turns_per_session: int = 60
    max_input_bytes: int = 65536
    idle_expiry_seconds: int = 600
    absolute_lifetime_seconds: int = 3600
    max_run_seconds: int = 600
    run_lease_seconds: int = 60
    vm_stop_after_idle_seconds: int = 120
    close_grace_seconds: int = 30
    export_retry_seconds: int = 300
    delete_retry_seconds: int = 3600
    tombstone_ttl_seconds: int = 7 * 86400
    daily_token_budget: int = 0
    session_token_budget: int = 2_000_000
    run_token_allowance: int = 400_000
    capacity_retry_after_seconds: int = 30

    @classmethod
    def from_env(cls) -> "IntegrationLimits":
        defaults = cls()
        values = {}
        for name in cls.__dataclass_fields__:
            values[name] = _env_int(
                f"IDEA_INTEGRATION_{name.upper()}", getattr(defaults, name)
            )
        return cls(**values)


@dataclass(frozen=True)
class IntegrationSettings:
    enabled: bool = False
    integration_ids: frozenset[str] = frozenset()
    limits: IntegrationLimits = field(default_factory=IntegrationLimits)

    @classmethod
    def from_env(cls) -> "IntegrationSettings":
        ids = frozenset(
            item.strip()
            for item in os.getenv("IDEA_INTEGRATION_IDS", "").split(",")
            if item.strip()
        )
        return cls(
            enabled=_env_bool("IDEA_INTEGRATION_SESSIONS_ENABLED"),
            integration_ids=ids,
            limits=IntegrationLimits.from_env(),
        )

    def readiness_errors(self) -> list[str]:
        if not self.enabled:
            return []
        errors = []
        if not self.integration_ids:
            errors.append("IDEA_INTEGRATION_IDS must list at least one integration")
        if self.limits.daily_token_budget <= 0:
            errors.append(
                "IDEA_INTEGRATION_DAILY_TOKEN_BUDGET must be positive before "
                "enabling public integration traffic"
            )
        if self.limits.run_token_allowance <= 0:
            errors.append("IDEA_INTEGRATION_RUN_TOKEN_ALLOWANCE must be positive")
        return errors


class AdmissionError(Exception):
    """A request the integration policy refuses; maps onto an HTTP response."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retry_after = retry_after

    def as_detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.retry_after is not None:
            detail["retry_after"] = self.retry_after
        return detail


@dataclass
class SessionRecord:
    owner_user_id: str
    integration_id: str
    workspace_id: str
    sandbox_id: str
    epoch: str
    created_at: float
    last_activity_at: float
    deadline_at: float
    state: str = STATE_OPEN
    close_reason: str | None = None
    closing_started_at: float | None = None
    active_run_id: str | None = None
    active_run_started_at: float | None = None
    active_run_lease_until: float | None = None
    active_run_token_reservation: int = 0
    vm_resident: bool = False
    vm_resident_since: float | None = None
    vm_disk_reserved: bool = False
    turns: int = 0
    tokens_used: int = 0
    export_status: str | None = None
    export_deadline_at: float | None = None
    export_errors: list[str] = field(default_factory=list)
    delete_attempts: int = 0
    delete_started_at: float | None = None
    last_error: str | None = None
    deleted_at: float | None = None
    schema_version: int = 1

    @classmethod
    def from_json(cls, raw: str) -> "SessionRecord":
        data = json.loads(raw)
        known = {key: data[key] for key in cls.__dataclass_fields__ if key in data}
        return cls(**known)

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    def run_lease_valid(self, now: float) -> bool:
        return bool(
            self.active_run_id
            and self.active_run_lease_until
            and self.active_run_lease_until > now
        )

    def expiry_reason(self, now: float, limits: IntegrationLimits) -> str | None:
        if now >= self.deadline_at:
            return REASON_ABSOLUTE_LIFETIME
        if (
            not self.run_lease_valid(now)
            and now - self.last_activity_at >= limits.idle_expiry_seconds
        ):
            return REASON_IDLE_EXPIRY
        return None

    def public_status(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "state": self.state,
            "close_reason": self.close_reason,
            "created_at": _iso(self.created_at),
            "last_activity_at": _iso(self.last_activity_at),
            "deadline_at": _iso(self.deadline_at),
            "active_run": bool(self.active_run_id),
            "turns": self.turns,
            "export_status": self.export_status,
        }


@dataclass(frozen=True)
class Admission:
    workspace_id: str
    sandbox_id: str
    epoch: str
    run_id: str
    created: bool
    deadline_at: float
    run_deadline_at: float
    token_reservation: int


def _iso(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _scope_digest(owner_user_id: str, integration_id: str) -> str:
    material = json.dumps([owner_user_id, integration_id], separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


class IntegrationSessionStore:
    """Redis-backed session records, counters, and reservations."""

    def __init__(
        self,
        redis_client: Any,
        settings: IntegrationSettings,
        *,
        clock: Callable[[], float] = time.time,
        lock_timeout_seconds: float = 10.0,
    ) -> None:
        self.redis = redis_client
        self.settings = settings
        self.limits = settings.limits
        self.clock = clock
        self.lock_timeout_seconds = lock_timeout_seconds

    # Keys -----------------------------------------------------------------

    def _record_key(self, workspace_id: str) -> str:
        return f"{KEY_PREFIX}rec:{workspace_id}"

    def _scope_key(self, kind: str, owner: str, integration: str) -> str:
        return f"{KEY_PREFIX}{kind}:{_scope_digest(owner, integration)}"

    def _session_rate_key(self, workspace_id: str) -> str:
        return f"{KEY_PREFIX}smsgs:{workspace_id}"

    def _daily_tokens_key(self, owner: str, integration: str, now: float) -> str:
        day = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y%m%d")
        return f"{self._scope_key('tokens', owner, integration)}:{day}"

    @property
    def _index_key(self) -> str:
        return f"{KEY_PREFIX}index"

    @property
    def _sandbox_map_key(self) -> str:
        return f"{KEY_PREFIX}sandboxes"

    def _vm_reservation_key(self, sandbox_id: str) -> str:
        return f"{VM_RESERVATION_KEY_PREFIX}{sandbox_id}"

    def _lock(self, owner: str, integration: str):
        return self.redis.lock(
            self._scope_key("lock", owner, integration),
            timeout=self.lock_timeout_seconds,
            blocking_timeout=self.lock_timeout_seconds,
        )

    # Record access ----------------------------------------------------------

    def get(self, workspace_id: str) -> SessionRecord | None:
        raw = self.redis.get(self._record_key(workspace_id))
        if not raw:
            return None
        return SessionRecord.from_json(raw)

    def _save(self, record: SessionRecord) -> None:
        key = self._record_key(record.workspace_id)
        if record.state == STATE_DELETED:
            self.redis.set(key, record.to_json(), ex=self.limits.tombstone_ttl_seconds)
        else:
            self.redis.set(key, record.to_json())

    def _with_lock(self, owner: str, integration: str, func: Callable[[], Any]) -> Any:
        lock = self._lock(owner, integration)
        if not lock.acquire():
            raise AdmissionError(
                503, "lifecycle_busy",
                "The integration lifecycle store is busy; retry shortly.",
                retry_after=2,
            )
        try:
            return func()
        finally:
            try:
                lock.release()
            except Exception:
                # An expired lock was already released by Redis; the
                # mutations above completed before its timeout.
                pass

    def _rolling_count(self, key: str, now: float, window: int) -> int:
        self.redis.zremrangebyscore(key, 0, now - window)
        return int(self.redis.zcard(key))

    def _record_event(self, key: str, now: float, window: int) -> None:
        self.redis.zadd(key, {f"{now:.6f}:{uuid.uuid4().hex}": now})
        self.redis.expire(key, window + 60)

    # VM reservations --------------------------------------------------------

    def _reserve_resident(self, record: SessionRecord, now: float) -> None:
        resident_key = self._scope_key("resident", record.owner_user_id, record.integration_id)
        self.redis.sadd(resident_key, record.workspace_id)
        record.vm_resident = True
        record.vm_resident_since = now
        self.redis.set(
            self._vm_reservation_key(record.sandbox_id),
            json.dumps({
                "workspace_id": record.workspace_id,
                "epoch": record.epoch,
                "reserved_at": now,
            }),
            ex=max(self.limits.absolute_lifetime_seconds, 60) + 3600,
        )

    def _release_resident(self, record: SessionRecord) -> None:
        resident_key = self._scope_key("resident", record.owner_user_id, record.integration_id)
        self.redis.srem(resident_key, record.workspace_id)
        self.redis.delete(self._vm_reservation_key(record.sandbox_id))
        record.vm_resident = False
        record.vm_resident_since = None

    def _release_disk(self, record: SessionRecord) -> None:
        disks_key = self._scope_key("disks", record.owner_user_id, record.integration_id)
        self.redis.srem(disks_key, record.workspace_id)
        record.vm_disk_reserved = False

    def _check_vm_capacity(self, record_or_none: SessionRecord | None, owner: str, integration: str) -> None:
        limits = self.limits
        needs_resident = record_or_none is None or not record_or_none.vm_resident
        needs_disk = record_or_none is None or not record_or_none.vm_disk_reserved
        if needs_resident:
            resident = int(self.redis.scard(self._scope_key("resident", owner, integration)))
            if resident >= limits.max_resident_vms:
                raise AdmissionError(
                    503, "vm_capacity",
                    "All public sandbox slots are busy; retry shortly.",
                    retry_after=limits.capacity_retry_after_seconds,
                )
        if needs_disk:
            disks = int(self.redis.scard(self._scope_key("disks", owner, integration)))
            if (
                disks >= limits.max_retained_disks
                or (disks + 1) * limits.vm_disk_mib > limits.max_reserved_disk_mib
            ):
                raise AdmissionError(
                    503, "disk_capacity",
                    "Public sandbox storage is at capacity; retry shortly.",
                    retry_after=limits.capacity_retry_after_seconds,
                )

    # Admission --------------------------------------------------------------

    def admit_turn(
        self,
        *,
        owner_user_id: str,
        integration_id: str,
        workspace_id: str,
        sandbox_id: str,
        run_id: str,
        input_bytes: int,
    ) -> Admission:
        if not self.settings.enabled:
            raise AdmissionError(
                503, "integration_disabled",
                "Integration sessions are not enabled on this deployment.",
            )
        if integration_id not in self.settings.integration_ids:
            raise AdmissionError(403, "integration_not_allowed", "Unknown integration.")
        if input_bytes > self.limits.max_input_bytes:
            raise AdmissionError(
                413, "input_too_large",
                f"Request input exceeds {self.limits.max_input_bytes} bytes.",
            )
        return self._with_lock(
            owner_user_id,
            integration_id,
            lambda: self._admit_locked(
                owner_user_id, integration_id, workspace_id, sandbox_id, run_id,
            ),
        )

    def _admit_locked(
        self,
        owner: str,
        integration: str,
        workspace_id: str,
        sandbox_id: str,
        run_id: str,
    ) -> Admission:
        limits = self.limits
        now = self.clock()
        record = self.get(workspace_id)

        if record is not None:
            if record.owner_user_id != owner or record.integration_id != integration:
                raise AdmissionError(403, "session_owner_mismatch", "Session not found.")
            if record.state != STATE_OPEN:
                raise AdmissionError(
                    410, "session_closed",
                    "This conversation has ended. Start a new conversation.",
                )
            reason = record.expiry_reason(now, limits)
            if reason:
                self._begin_close_locked(record, reason, now)
                raise AdmissionError(
                    410, "session_closed",
                    "This conversation has expired. Start a new conversation.",
                )
            if record.run_lease_valid(now):
                raise AdmissionError(
                    409, "turn_in_progress",
                    "A response is still being generated for this conversation.",
                    retry_after=5,
                )
            if record.active_run_id:
                # The worker that held this lease stopped renewing it; charge
                # its full reservation rather than refunding unknown usage.
                record.tokens_used += record.active_run_token_reservation
                record.active_run_id = None
                record.active_run_token_reservation = 0
            if record.sandbox_id != sandbox_id:
                raise AdmissionError(409, "sandbox_mismatch", "Session routing changed.")
            if record.turns >= limits.max_turns_per_session:
                raise AdmissionError(
                    429, "session_turn_limit",
                    "This conversation reached its message limit. Start a new conversation.",
                )
            if self._rolling_count(self._session_rate_key(workspace_id), now, 60) >= limits.session_messages_per_minute:
                raise AdmissionError(
                    429, "session_rate_limited",
                    "Too many messages in this conversation; slow down.",
                    retry_after=60,
                )
            if record.tokens_used + limits.run_token_allowance > limits.session_token_budget:
                raise AdmissionError(
                    429, "session_budget_exhausted",
                    "This conversation reached its usage limit. Start a new conversation.",
                )
        else:
            open_count = int(self.redis.scard(self._scope_key("open", owner, integration)))
            if open_count >= limits.max_open_sessions:
                raise AdmissionError(
                    429, "too_many_open_sessions",
                    "Too many conversations are open; retry later.",
                    retry_after=limits.capacity_retry_after_seconds,
                )
            created_key = self._scope_key("created", owner, integration)
            if self._rolling_count(created_key, now, 3600) >= limits.new_sessions_per_hour:
                raise AdmissionError(
                    429, "session_creation_rate_limited",
                    "Too many new conversations; retry later.",
                    retry_after=300,
                )

        scope_rate_key = self._scope_key("msgs", owner, integration)
        if self._rolling_count(scope_rate_key, now, 60) >= limits.messages_per_minute:
            raise AdmissionError(
                429, "rate_limited", "Too many messages; retry shortly.", retry_after=30,
            )
        daily_key = self._daily_tokens_key(owner, integration, now)
        used_today = int(self.redis.get(daily_key) or 0)
        if limits.daily_token_budget <= 0 or used_today + limits.run_token_allowance > limits.daily_token_budget:
            raise AdmissionError(
                429, "daily_budget_exhausted",
                "The service has reached its daily usage limit.",
                retry_after=int(86400 - (now % 86400)),
            )
        self._check_vm_capacity(record, owner, integration)

        created = record is None
        if record is None:
            record = SessionRecord(
                owner_user_id=owner,
                integration_id=integration,
                workspace_id=workspace_id,
                sandbox_id=sandbox_id,
                epoch=secrets.token_hex(8),
                created_at=now,
                last_activity_at=now,
                deadline_at=now + limits.absolute_lifetime_seconds,
            )
            self.redis.sadd(self._scope_key("open", owner, integration), workspace_id)
            self._record_event(self._scope_key("created", owner, integration), now, 3600)
            self.redis.sadd(self._index_key, workspace_id)
            self.redis.hset(self._sandbox_map_key, sandbox_id, workspace_id)
        if not record.vm_disk_reserved:
            self.redis.sadd(self._scope_key("disks", owner, integration), workspace_id)
            record.vm_disk_reserved = True
        if not record.vm_resident:
            self._reserve_resident(record, now)

        self._record_event(self._session_rate_key(workspace_id), now, 60)
        self._record_event(scope_rate_key, now, 60)
        self.redis.incrby(daily_key, limits.run_token_allowance)
        self.redis.expire(daily_key, 2 * 86400)

        record.turns += 1
        record.last_activity_at = now
        record.active_run_id = run_id
        record.active_run_started_at = now
        record.active_run_lease_until = now + limits.run_lease_seconds
        record.active_run_token_reservation = limits.run_token_allowance
        self._save(record)
        return Admission(
            workspace_id=workspace_id,
            sandbox_id=sandbox_id,
            epoch=record.epoch,
            run_id=run_id,
            created=created,
            deadline_at=record.deadline_at,
            run_deadline_at=min(record.deadline_at, now + limits.max_run_seconds),
            token_reservation=limits.run_token_allowance,
        )

    # Active-run leases -------------------------------------------------------

    def renew_lease(self, workspace_id: str, run_id: str, epoch: str) -> str | None:
        """Extend the run lease; returns a cancellation reason when it must stop."""
        record = self.get(workspace_id)
        if record is None or record.epoch != epoch:
            return REASON_FAILURE

        def renew() -> str | None:
            now = self.clock()
            current = self.get(workspace_id)
            if current is None or current.epoch != epoch:
                return REASON_FAILURE
            if current.state != STATE_OPEN:
                return current.close_reason or REASON_OPERATOR
            if current.active_run_id != run_id:
                return REASON_FAILURE
            if now >= current.deadline_at:
                self._begin_close_locked(current, REASON_ABSOLUTE_LIFETIME, now)
                return REASON_ABSOLUTE_LIFETIME
            started = current.active_run_started_at or now
            if now - started >= self.limits.max_run_seconds:
                return "run_time_limit"
            current.active_run_lease_until = now + self.limits.run_lease_seconds
            self._save(current)
            return None

        return self._with_lock(record.owner_user_id, record.integration_id, renew)

    def finish_turn(self, workspace_id: str, run_id: str, tokens_used: int | None) -> None:
        record = self.get(workspace_id)
        if record is None:
            return

        def finish() -> None:
            now = self.clock()
            current = self.get(workspace_id)
            if current is None or current.active_run_id != run_id:
                return
            reserved = current.active_run_token_reservation
            used = reserved if tokens_used is None else max(int(tokens_used), 0)
            daily_key = self._daily_tokens_key(
                current.owner_user_id, current.integration_id, now
            )
            if used != reserved:
                self.redis.incrby(daily_key, used - reserved)
            current.tokens_used += used
            current.active_run_id = None
            current.active_run_started_at = None
            current.active_run_lease_until = None
            current.active_run_token_reservation = 0
            if current.state == STATE_OPEN:
                current.last_activity_at = now
            self._save(current)

        self._with_lock(record.owner_user_id, record.integration_id, finish)

    # Closing -----------------------------------------------------------------

    def _begin_close_locked(self, record: SessionRecord, reason: str, now: float) -> None:
        if record.state != STATE_OPEN:
            return
        record.state = STATE_CLOSING
        record.close_reason = reason
        record.closing_started_at = now
        self.redis.srem(
            self._scope_key("open", record.owner_user_id, record.integration_id),
            record.workspace_id,
        )
        self._save(record)

    def request_close(
        self,
        *,
        owner_user_id: str,
        integration_id: str,
        workspace_id: str,
        sandbox_id: str,
        reason: str = REASON_EXPLICIT_END,
    ) -> SessionRecord:
        """Idempotently close a session; an unknown session becomes a tombstone."""

        def close() -> SessionRecord:
            now = self.clock()
            record = self.get(workspace_id)
            if record is None:
                record = SessionRecord(
                    owner_user_id=owner_user_id,
                    integration_id=integration_id,
                    workspace_id=workspace_id,
                    sandbox_id=sandbox_id,
                    epoch=secrets.token_hex(8),
                    created_at=now,
                    last_activity_at=now,
                    deadline_at=now,
                    state=STATE_DELETED,
                    close_reason=reason,
                    deleted_at=now,
                    export_status="not_started",
                )
                self._save(record)
                return record
            if record.owner_user_id != owner_user_id or record.integration_id != integration_id:
                raise AdmissionError(403, "session_owner_mismatch", "Session not found.")
            self._begin_close_locked(record, reason, now)
            return record

        return self._with_lock(owner_user_id, integration_id, close)

    # Sweeper transitions -------------------------------------------------------

    def indexed_workspace_ids(self) -> list[str]:
        return sorted(self.redis.smembers(self._index_key))

    def workspace_for_sandbox(self, sandbox_id: str) -> str | None:
        return self.redis.hget(self._sandbox_map_key, sandbox_id)

    def mutate(self, workspace_id: str, func: Callable[[SessionRecord, float], Any]) -> Any:
        record = self.get(workspace_id)
        if record is None:
            self.redis.srem(self._index_key, workspace_id)
            return None

        def apply() -> Any:
            current = self.get(workspace_id)
            if current is None:
                return None
            result = func(current, self.clock())
            self._save(current)
            return result

        return self._with_lock(record.owner_user_id, record.integration_id, apply)

    def begin_close(self, record: SessionRecord, reason: str, now: float) -> None:
        self._begin_close_locked(record, reason, now)

    def release_resident(self, record: SessionRecord) -> None:
        self._release_resident(record)

    def mark_deleted(self, record: SessionRecord, now: float) -> None:
        self._release_resident(record)
        self._release_disk(record)
        self.redis.srem(
            self._scope_key("open", record.owner_user_id, record.integration_id),
            record.workspace_id,
        )
        self.redis.srem(self._index_key, record.workspace_id)
        self.redis.hdel(self._sandbox_map_key, record.sandbox_id)
        record.state = STATE_DELETED
        record.deleted_at = now
        record.active_run_id = None
        record.active_run_lease_until = None


@dataclass
class SweepActions:
    """Side effects the sweeper performs; injected so tests can fake them."""

    stop_vm: Callable[[str], bool]
    delete_vm: Callable[[str], bool]
    interrupt_run: Callable[[str, str], Any]
    cancel_run: Callable[[str, str], None]
    # Returns an export status; only "failed" is retried before the deadline.
    export_session: Callable[[SessionRecord, bool], str]
    list_vms: Callable[[], Iterable[str] | None]


class IntegrationSessionSweeper:
    """Durable expiry, export, deletion, and inventory reconciliation."""

    def __init__(
        self,
        store: IntegrationSessionStore,
        actions: SweepActions,
        *,
        leader_ttl_seconds: int = 60,
    ) -> None:
        self.store = store
        self.actions = actions
        self.leader_ttl_seconds = leader_ttl_seconds
        self.leader_token = uuid.uuid4().hex

    def acquire_leadership(self) -> bool:
        key = f"{KEY_PREFIX}sweeper_leader"
        redis_client = self.store.redis
        if redis_client.set(key, self.leader_token, nx=True, ex=self.leader_ttl_seconds):
            return True
        if redis_client.get(key) == self.leader_token:
            redis_client.expire(key, self.leader_ttl_seconds)
            return True
        return False

    def run_once(self) -> dict[str, int]:
        counts = {"closed": 0, "stopped": 0, "exported": 0, "deleted": 0, "orphans": 0}
        for workspace_id in self.store.indexed_workspace_ids():
            try:
                self._advance(workspace_id, counts)
            except AdmissionError:
                continue
            except Exception as exc:
                print(f"Integration sweeper failed for {workspace_id}: {exc}")
        counts["orphans"] = self._reconcile_inventory()
        return counts

    def _advance(self, workspace_id: str, counts: dict[str, int]) -> None:
        store = self.store
        limits = store.limits
        record = store.get(workspace_id)
        if record is None:
            store.mutate(workspace_id, lambda *_: None)
            return

        if record.state == STATE_OPEN:
            def check_open(current: SessionRecord, now: float) -> str | None:
                if current.state != STATE_OPEN:
                    return None
                reason = current.expiry_reason(now, limits)
                if reason:
                    store.begin_close(current, reason, now)
                    return "closed"
                if current.active_run_id and not current.run_lease_valid(now):
                    current.tokens_used += current.active_run_token_reservation
                    current.active_run_id = None
                    current.active_run_lease_until = None
                    current.active_run_token_reservation = 0
                if (
                    current.vm_resident
                    and not current.active_run_id
                    and now - current.last_activity_at >= limits.vm_stop_after_idle_seconds
                ):
                    return "stop"
                return None

            outcome = store.mutate(workspace_id, check_open)
            if outcome == "closed":
                counts["closed"] += 1
                record = store.get(workspace_id)
            elif outcome == "stop":
                if self.actions.stop_vm(record.sandbox_id):
                    def released(current: SessionRecord, now: float) -> None:
                        if current.state == STATE_OPEN and not current.active_run_id:
                            store.release_resident(current)
                    store.mutate(workspace_id, released)
                    counts["stopped"] += 1
                return
            else:
                return

        if record is None:
            return

        if record.state == STATE_CLOSING:
            now = store.clock()
            if record.run_lease_valid(now) and record.active_run_id:
                self.actions.cancel_run(record.active_run_id, record.close_reason or "closed")
                self.actions.interrupt_run(record.sandbox_id, record.active_run_id)
                if now - (record.closing_started_at or now) < limits.close_grace_seconds:
                    return

            def to_exporting(current: SessionRecord, now: float) -> None:
                if current.state == STATE_CLOSING:
                    current.state = STATE_EXPORTING
                    current.export_deadline_at = now + limits.export_retry_seconds
                    current.export_status = "pending"
            store.mutate(workspace_id, to_exporting)
            record = store.get(workspace_id)

        if record is not None and record.state == STATE_EXPORTING:
            now = store.clock()
            final_attempt = now >= (record.export_deadline_at or now)
            try:
                status = self.actions.export_session(record, final_attempt)
            except Exception as exc:
                status = "failed"
                print(f"Integration export failed for {workspace_id}: {exc}")
            if status != "failed" or final_attempt:
                def to_deleting(current: SessionRecord, now: float) -> None:
                    if current.state == STATE_EXPORTING:
                        current.state = STATE_DELETING
                        current.export_status = status
                        current.delete_started_at = now
                store.mutate(workspace_id, to_deleting)
                counts["exported"] += 1
                record = store.get(workspace_id)
            else:
                return

        if record is not None and record.state == STATE_DELETING:
            try:
                deleted = self.actions.delete_vm(record.sandbox_id)
                error = None if deleted else "sandbox deletion was not confirmed"
            except Exception as exc:
                deleted = False
                error = str(exc)

            def finish_delete(current: SessionRecord, now: float) -> None:
                if current.state != STATE_DELETING:
                    return
                if deleted:
                    store.mark_deleted(current, now)
                else:
                    current.delete_attempts += 1
                    current.last_error = error
            store.mutate(workspace_id, finish_delete)
            if deleted:
                counts["deleted"] += 1

    def _reconcile_inventory(self) -> int:
        try:
            inventory = self.actions.list_vms()
        except Exception as exc:
            print(f"Integration VM inventory failed: {exc}")
            return 0
        if inventory is None:
            return 0
        removed = 0
        for sandbox_id in inventory:
            workspace_id = self.store.workspace_for_sandbox(sandbox_id)
            record = self.store.get(workspace_id) if workspace_id else None
            if record is not None and record.state in ACTIVE_STATES:
                continue
            try:
                if self.actions.delete_vm(sandbox_id):
                    removed += 1
            except Exception as exc:
                print(f"Orphaned integration VM {sandbox_id} deletion failed: {exc}")
        return removed
