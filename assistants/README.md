# IDEA Assistants

This directory contains IDEA's three deployment-managed Open WebUI
Assistants. Each Assistant wraps the visible `idea-terminal-agent` base model
with a domain-specific system prompt and user-facing metadata.

The three files under `prompts/` are derived from the `content=` strings
seeded by legacy IDEA's `utils/prompt_manager.py` on the
`server-side-compaction` branch. SEA and Mars contain only the minor storage
changes needed for IDEA-next's read-only shared `/app/data` mount and private
per-user `/workspace`. Tests pin their lengths and SHA-256 hashes to prevent
other accidental modernization or formatting changes.

The manifest uses the readable Pipe sub-model ID `idea-terminal-agent`.
Open WebUI currently qualifies that model in its live catalog as
`idea_terminal_agent.idea-terminal-agent`; the deployment script resolves and
uses the qualified ID automatically.

Run from the repository root:

```bash
./assistants/deploy_assistants_openwebui.py
```

The default seed mode creates missing Assistants but leaves existing
Assistants unchanged, preserving edits made through the Admin UI. To restore
the repository versions:

```bash
./assistants/deploy_assistants_openwebui.py --reconcile
```

Use `--dry-run` to validate files and preview actions. `--only sea` limits an
operation to one manifest Assistant. The script loads the repository `.env`
and uses the same Open WebUI administrator authentication flow as
`openwebui/configure_openwebui.py`.

The script also:

- keeps the IDEA Terminal Agent visible in both chat selection and the
  Assistant editor's base-model picker;
- assigns the IDEA logo to the Terminal Agent's workspace metadata so its
  profile image endpoint does not fall back to the Open WebUI favicon;
- enables private Assistant creation for verified non-admin users;
- keeps user-to-user and public Assistant sharing disabled; and
- selects Welcome Assistant as the default when no default has already been
  chosen (or unconditionally with `--reconcile`).

User-created Assistants are never modified or deleted by this script.

`assets/uhslc.svg` preserves the source UHSLC mark on a black square
background; `assets/uhslc.png` is its 512×512 deployment rendering.
