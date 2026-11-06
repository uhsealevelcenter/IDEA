"""Check native resume, persistent files, and intact catalog journals.

Only the reserved probe is modified. Never open the live catalog with sqlite3.
"""

import asyncio
import json
import os
from pathlib import Path
import uuid

from lifecycle_recovery import require_native_pid_guard
from msb_sandbox import MicrosandboxTerminal
from terminal_registry import _use_microsandbox


def assert_journals_linked():
    # Inspect descriptors, never read/open the SQLite database itself.
    for pid in {1, os.getpid()}:
        for fd in Path(f"/proc/{pid}/fd").glob("*"):
            try:
                target = os.readlink(fd)
            except FileNotFoundError:
                continue
            if "msb.db" in target and target.endswith(" (deleted)"):
                raise RuntimeError(f"Native catalog has an unlinked file: {target}")


def main():
    if not _use_microsandbox():
        print("Skipping microVM lifecycle smoke check: local shell backend selected")
        return
    require_native_pid_guard()
    from microsandbox import Sandbox
    from microsandbox.errors import SandboxNotFoundError
    name = "idea-lifecycle-smoke"
    marker_path = "/workspace/.idea-lifecycle-probe"
    async def exists():
        try:
            await Sandbox.get(name)
            return True
        except SandboxNotFoundError:
            return False
    existed = asyncio.run(exists())
    marker = None
    for iteration in range(3):
        terminal = MicrosandboxTerminal(name, cpus=1, memory=1024, disk_mb=4096)
        try:
            previous = terminal._exec(lambda: terminal._sandbox.fs.exists(marker_path))
            if existed and not previous:
                raise RuntimeError("Existing probe lost its persistent marker")
            if previous:
                old = terminal.read_file(marker_path)
                if not old.startswith(b"idea-lifecycle:") or (marker is not None and old != marker):
                    raise RuntimeError("Persistent probe marker changed on resume")
            marker = ("idea-lifecycle:" + uuid.uuid4().hex).encode()
            terminal._exec(lambda: terminal._sandbox.fs.write(marker_path, marker))
            success, output, _ = terminal.run("pwd")
            if not success or output.strip() != "/workspace":
                raise RuntimeError(f"Probe workspace execution failed: {output}")
            assert_journals_linked()
        finally:
            terminal.close()
        assert_journals_linked()
        existed = True
    print(json.dumps({"sandbox": name, "resume_cycles": 3,
                      "file_preserved": True, "native_journals_linked": True}))


if __name__ == "__main__":
    main()
