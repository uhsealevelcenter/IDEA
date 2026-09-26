#!/usr/bin/env python3
"""Exports environment variables defined in deployment/config.yaml for a given environment.

Zero external dependencies - parses basic YAML key/value dictionaries with standard library only.
Usage:
    eval "$(python3 deployment/load_env.py <dev|staging|next-dev|prod>)"
"""

from __future__ import annotations

import sys
from pathlib import Path


def parse_simple_yaml(text: str) -> dict[str, dict[str, str]]:
    """Parse top-level section -> key/value pairs from simple YAML."""
    sections: dict[str, dict[str, str]] = {}
    current_section: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.strip().startswith("#"):
            continue

        # Top-level section header (no leading whitespace, ends with ':')
        if not line.startswith(" ") and not line.startswith("\t") and line.endswith(":"):
            current_section = line[:-1].strip()
            sections[current_section] = {}
            continue

        # Indented key-value entry
        if current_section is not None and (line.startswith("  ") or line.startswith("\t")):
            stripped = line.strip()
            if ":" in stripped:
                key, val = stripped.split(":", 1)
                key = key.strip()
                val = val.strip()
                # Strip single/double quotes if present
                if len(val) >= 2 and val[0] == val[-1] and val[0] in {"'", '"'}:
                    val = val[1:-1]
                sections[current_section][key] = val

    return sections


def main() -> int:
    if len(sys.argv) < 2:
        print("echo 'Error: environment argument required (dev|staging|next-dev|prod)' >&2; false")
        return 1

    target_env = sys.argv[1].strip()
    config_path = Path(__file__).resolve().parent / "config.yaml"

    if not config_path.exists():
        print(f"echo 'Error: config file {config_path} not found' >&2; false")
        return 1

    content = config_path.read_text(encoding="utf-8")
    data = parse_simple_yaml(content)

    defaults = data.get("default", {})
    env_vars = dict(defaults)

    if target_env in data:
        env_vars.update(data[target_env])
    elif target_env != "default":
        print(f"echo 'Error: Environment {target_env!r} not found in {config_path}' >&2; false")
        return 1

    # Output bash export statements
    for k, v in env_vars.items():
        # Escape double quotes and backslashes for safe eval
        escaped_val = str(v).replace("\\", "\\\\").replace('"', '\\"')
        print(f'export {k}="{escaped_val}"')

    return 0


if __name__ == "__main__":
    sys.exit(main())
