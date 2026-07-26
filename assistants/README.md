# IDEA Assistants

This directory contains IDEA's three deployment-managed Open WebUI
Assistants. Each Assistant wraps the visible `idea-terminal-agent` base model
with a domain-specific system prompt and user-facing metadata.

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
- enables private Assistant creation for verified non-admin users;
- keeps user-to-user and public Assistant sharing disabled; and
- selects Welcome Assistant as the default when no default has already been
  chosen (or unconditionally with `--reconcile`).

User-created Assistants are never modified or deleted by this script.
