"""Require the native PID-identity fix; never open the runtime's SQLite catalog.

Microsandbox owns its catalog and WAL connections. Opening that file through
Python sqlite3 can unlink journals still held by the native SDK. Even integrity
checks must run offline or through the same native implementation.
"""


def require_native_pid_guard() -> None:
    from microsandbox import _microsandbox

    guard = getattr(_microsandbox, "idea_pid_identity_guard", None)
    if guard is None or not guard():
        raise RuntimeError(
            "IDEA requires the patched Microsandbox native PID-identity guard. "
            "Rebuild the sandbox image; do not repair the live catalog with Python SQLite."
        )
