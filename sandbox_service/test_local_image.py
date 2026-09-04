"""Validate a local image through IDEA's production MicrosandboxTerminal."""

from __future__ import annotations

import argparse
import asyncio
import json
import re

from microsandbox import Sandbox
from microsandbox.errors import SandboxNotFoundError

from msb_sandbox import MicrosandboxTerminal


async def ensure_missing(name: str) -> None:
    try:
        await Sandbox.get(name)
    except SandboxNotFoundError:
        return
    raise RuntimeError(f"refusing to replace existing sandbox {name!r}")


def validate(image: str, name: str, disk_mb: int = 4096) -> None:
    if not re.fullmatch(r"idea-image-smoke-[A-Za-z0-9_-]+", name):
        raise ValueError("test sandbox name must start with 'idea-image-smoke-'")
    asyncio.run(ensure_missing(name))

    terminal = None
    try:
        # This is the same creation and lazy-service path used by requests.
        terminal = MicrosandboxTerminal(
            session_id=name,
            image=image,
            cpus=2,
            memory=4096,
            disk_mb=disk_mb,
            idle_timeout=None,
            max_duration=None,
            shared_data_host_path="",
        )
        success, output, _ = terminal.run(
            "codex --version && guarddog --help >/dev/null"
        )
        if not success or "codex-cli" not in output:
            raise RuntimeError(f"guest CLI check failed: {output}")

        payload = terminal.run_python(
            "value = 6 * 7\nvalue", kernel_id="msb_smoke"
        )
        if "42" not in json.dumps(payload):
            raise RuntimeError(f"persistent kernel did not return 42: {payload}")

        success, root_size, _ = terminal.run(
            "df -BM --output=size / | tail -n 1"
        )
        match = re.search(r"(\d+)M", root_size)
        if not success or match is None:
            raise RuntimeError(f"could not inspect guest root capacity: {root_size}")
        # ext4 metadata and reserved blocks make the reported filesystem
        # slightly smaller than the configured sparse upper-disk capacity.
        if int(match.group(1)) < int(disk_mb * 0.9):
            raise RuntimeError(
                f"guest root capacity is smaller than requested: {root_size} "
                f"for {disk_mb} MiB"
            )

        # Open Terminal is intentionally started lazily by this call because
        # detached microsandbox guests do not execute OCI entrypoints.
        result = terminal.glob_search(
            "codex_runner.py", path="/opt/oi_kernel", max_results=10
        )
        if "codex_runner.py" not in json.dumps(result):
            raise RuntimeError(f"Open Terminal glob did not find runner: {result}")

        print(output)
        print(json.dumps(payload))
        print(f"Microsandbox SDK image validation passed: {image}")
    finally:
        if terminal is not None:
            terminal.destroy()
        else:
            try:
                asyncio.run(Sandbox.remove(name))
            except SandboxNotFoundError:
                pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="idea/oi-kernel:research-local")
    parser.add_argument("--name", default="idea-image-smoke-local")
    parser.add_argument("--disk-mb", type=int, default=4096)
    args = parser.parse_args()
    validate(args.image, args.name, args.disk_mb)


if __name__ == "__main__":
    main()
