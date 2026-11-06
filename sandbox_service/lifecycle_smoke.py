"""Deployment check: preserve a dedicated probe VM's files across PID reuse.

Only the reserved idea-lifecycle-smoke sandbox is modified. Its marker also
survives container replacements, so later deployments check disk persistence.
"""

import json
import importlib.metadata
import os
import sqlite3
import uuid

from lifecycle_recovery import microsandbox_home
from msb_sandbox import MicrosandboxTerminal
from terminal_registry import _use_microsandbox


def main():
    if not _use_microsandbox():
        print("Skipping microVM lifecycle smoke check: local shell backend selected")
        return
    name = "idea-lifecycle-smoke"
    marker_path = "/workspace/.idea-lifecycle-probe"
    assert importlib.metadata.version("microsandbox") == "0.7.2", "Revalidate the lifecycle probe for this SDK"
    database = microsandbox_home() / "db" / "msb.db"
    existed = False
    if database.is_file():
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
            existed = connection.execute('SELECT 1 FROM sandbox WHERE name=?', (name,)).fetchone() is not None
    terminal = MicrosandboxTerminal(name, cpus=1, memory=1024, disk_mb=4096)
    try:
        # An existing probe must retain the marker from the prior deployment.
        previous = terminal._exec(lambda: terminal._sandbox.fs.exists(marker_path))
        assert previous or not existed, "Existing probe lost its persistent marker"
        if previous:
            old = terminal.read_file(marker_path)
            assert old.startswith(b"idea-lifecycle:"), "Persistent probe marker is corrupt"
        marker = ("idea-lifecycle:" + uuid.uuid4().hex).encode()
        terminal._exec(lambda: terminal._sandbox.fs.write(marker_path, marker))
    finally:
        terminal.close()

    # Reproduce the production failure with a definitely live, unrelated PID.
    # This reserved probe is stopped; no real user's runtime record is touched.
    with sqlite3.connect(microsandbox_home() / "db" / "msb.db") as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            'SELECT s.status, r.id, r.status FROM sandbox s JOIN run r ON r.sandbox_id=s.id '
            'WHERE s.name=? ORDER BY r.id DESC LIMIT 1', (name,),
        ).fetchone()
        assert row and row[0] == "Stopped" and row[2] == "Terminated", row
        connection.execute('UPDATE run SET pid=? WHERE id=?', (os.getpid(), row[1]))
    terminal = MicrosandboxTerminal(name)
    try:
        assert terminal.read_file(marker_path) == marker, "Probe file lost on resume"
        success, output, _ = terminal.run("pwd")
        assert success and output.strip() == "/workspace", output
        print(json.dumps({"sandbox": name, "pid_collision_recovered": True,
                          "file_preserved": True, "previous_deployment_marker": bool(previous)}))
    finally:
        terminal.close()


if __name__ == "__main__":
    main()
