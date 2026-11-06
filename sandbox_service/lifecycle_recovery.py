"""Disk-preserving compatibility guard for Microsandbox 0.7.2 on Linux.

The upstream start guard mistakes any live PID for the previous runtime:
https://github.com/microsandbox/microsandbox/blob/v0.7.2/sdk/rust/lib/backend/local/sandbox/mod.rs
Keep this adapter version-scoped: its SQLite layout and lock paths are private
upstream interfaces. Never remove disks, signal PIDs, or alter active runs.
"""

import contextlib
import hashlib
import importlib.metadata
import logging
import os
from pathlib import Path
import sqlite3
import sys

logger = logging.getLogger(__name__)


def microsandbox_home() -> Path:
    return Path(os.environ.get("MSB_HOME") or Path.home() / ".microsandbox")


def _recorded_runtime_alive(pid: int, name: str, sandbox_id: int, *, proc_root=Path("/proc")) -> bool:
    """Treat unreadable/ambiguous identities as live, never as permission to repair."""
    process = proc_root / str(pid)
    try:
        status = dict(line.split(":", 1) for line in (process / "status").read_text().splitlines())
        # A worker TID can satisfy kill(pid, 0), but cannot be the VM leader.
        if int(status["Tgid"]) != pid:
            return False
        args = (process / "cmdline").read_bytes().split(b"\0")
        if not args or not args[0]:
            return status["State"].strip().startswith("Z") is False
        if Path(os.fsdecode(args[0])).name != "msb" or args[1:2] != [b"machine"]:
            return False
        # Preserve legacy/ambiguous msb command lines. Only explicit ownership
        # of a different sandbox proves that a runtime PID was reused.
        for flag, expected in ((b"--name", name.encode()), (b"--sandbox-id", str(sandbox_id).encode())):
            if flag in args:
                index = args.index(flag)
                if index + 1 < len(args) and args[index + 1] != expected:
                    return False
        return True
    except FileNotFoundError:
        return False
    except (OSError, ValueError, KeyError):
        return True


def clear_stale_runtime_pid(name: str) -> bool:
    """Clear only a terminated run's provably unrelated PID, under native locks.

    Both lock files use upstream's SHA256(name)[:16] hex convention. Lock order
    matches start: name transition, runtime ownership, then SQLite transaction.
    Locks are nonblocking; a live owner or competing start always wins. The SDK
    acquires them again when it starts, so it still arbitrates any later race.
    """
    home = microsandbox_home()
    database = home / "db" / "msb.db"
    if not database.is_file():
        return False
    if sys.platform != "linux" or importlib.metadata.version("microsandbox") != "0.7.2":
        return False
    import fcntl
    key = hashlib.sha256(name.encode()).hexdigest()[:32]
    with contextlib.ExitStack() as stack:
        for directory in ("creation-locks", "locks"):
            path = home / "run" / directory / (key + ".lock")
            path.parent.mkdir(parents=True, exist_ok=True)
            lock = stack.enter_context(path.open("a+b"))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
        connection = stack.enter_context(contextlib.closing(sqlite3.connect(database, timeout=5)))
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                'SELECT s.id, s.status, r.id, r.pid, r.status FROM sandbox s '
                'JOIN run r ON r.sandbox_id=s.id WHERE s.name=? '
                'ORDER BY r.id DESC LIMIT 1', (name,),
            ).fetchone()
            if row is None:
                return False
            sid, state, rid, pid, run_state = row
            if state not in ("Stopped", "Crashed") or run_state != "Terminated" or not pid:
                return False
            if _recorded_runtime_alive(pid, name, sid):
                return False
            connection.execute(
                'UPDATE run SET pid=NULL WHERE id=? AND sandbox_id=? AND pid=? AND status=?',
                (rid, sid, pid, "Terminated"),
            )
        logger.warning("Cleared stale Microsandbox PID: sandbox=%s run=%s old_pid=%s", name, rid, pid)
        return True
