#!/usr/bin/env python3
"""Update only NEXTAUTH_URL; preserve all service secrets and bootstrap settings."""
import sys
from pathlib import Path
from urllib.parse import urlsplit


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: set_langfuse_url.py https://langfuse.example.org")
    url = sys.argv[1]
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
        or any(char.isspace() for char in url)
    ):
        raise SystemExit("Expected an absolute HTTP(S) origin without a path")
    path = Path(__file__).resolve().parent.parent / "langfuse/.env"
    original = path.read_text()
    lines = original.splitlines()
    replacement = f"NEXTAUTH_URL={url.rstrip('/')}"
    found = any(line.startswith("NEXTAUTH_URL=") for line in lines)
    lines = [replacement if line.startswith("NEXTAUTH_URL=") else line for line in lines]
    if not found:
        lines.append(replacement)
    updated = "\n".join(lines) + "\n"
    if updated != original:
        # Preserve permissions and the inode; never generate credentials.
        path.write_text(updated)


if __name__ == "__main__":
    main()
