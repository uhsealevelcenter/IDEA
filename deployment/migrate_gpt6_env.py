#!/usr/bin/env python3
"""Move existing service env files from IDEA's prior model defaults to GPT-6.

Only known former defaults are replaced. Unknown operator-selected model values
remain untouched; non-model settings and credentials are never rewritten.
"""

from pathlib import Path


MIGRATIONS = {
    "langgraph/.env": {
        "IDEA_AGENT_MODEL": ("gpt-6-sol", {"gpt-5.5", "gpt-5.6-terra", "gpt-5.6-sol"}),
        "IDEA_AGENT_REASONING_EFFORT": ("medium", {"medium"}),
        "IDEA_TOOL_MODEL": ("gpt-6-luna", {"gpt-5.5", "gpt-5.6-terra"}),
        "IDEA_ADVANCED_AGENT_MODEL": ("gpt-6-sol-priority", {"gpt-5.5", "gpt-5.6-sol"}),
        "IDEA_ADVANCED_REASONING_EFFORT": ("medium", {"medium"}),
        "IDEA_CODEX_MODEL": ("gpt-6-sol", {"gpt-5.5", "gpt-5.6-terra"}),
        "IDEA_CODEX_REASONING_EFFORT": ("medium", {"medium"}),
        "PQA_LLM_MODEL": ("gpt-6-luna", {"gpt-5.6-terra", "gpt-5.6-luna"}),
    },
    "openwebui/.env": {
        "TASK_MODEL_EXTERNAL": ("gpt-6-luna", {"gpt-5.6-luna"}),
    },
}


def migrate(path: Path, settings: dict[str, tuple[str, set[str]]]) -> list[str]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    changed: list[str] = []
    for index, line in enumerate(lines):
        key, separator, value = line.partition("=")
        if not separator or key not in settings:
            continue
        seen.add(key)
        target, previous = settings[key]
        if value.strip() in previous or not value.strip():
            if value != target:
                lines[index] = f"{key}={target}"
                changed.append(key)
    for key, (target, _) in settings.items():
        if key not in seen:
            lines.append(f"{key}={target}")
            changed.append(key)
    if changed:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    for relative, settings in MIGRATIONS.items():
        changed = migrate(root / relative, settings)
        if changed:
            print(f"Updated {relative}: {', '.join(changed)}")


if __name__ == "__main__":
    main()
