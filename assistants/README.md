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

- keeps the IDEA Agent visible in both chat selection and the
  Assistant editor's base-model picker;
- assigns the IDEA logo to the IDEA Agent's workspace metadata so its
  profile image endpoint does not fall back to the Open WebUI favicon;
- enables private Assistant creation for verified non-admin users;
- keeps user-to-user and public Assistant sharing disabled; and
- selects Welcome Assistant as the default when no default has already been
  chosen (or unconditionally with `--reconcile`).

User-created Assistants are never modified or deleted by this script.

Reconciliation also applies one shared Workspace feature policy to Welcome,
SEA, and Mars. Vision, file upload, citations, status updates, and the
Built-in Tools section are available. All Default Features remain off so
Open WebUI does not run web search, image generation, or code interpretation
ahead of IDEA's Pipe. Time & Calculation is the only enabled Open WebUI
built-in; Knowledge Base, Files, Web Search, Code Interpreter, Image
Generation, Memory, Chat History, Tasks, Sub-agents, Notes, Channels,
Notifications, Automations, and Calendar remain visible but off to avoid
duplicating IDEA/PaperQA or introducing side effects.

An Assistant manifest entry may set `"paperqa_enabled": true`. Reconciliation
then disables native Open WebUI file-context RAG for that Assistant and uses
legacy function handling so its attached Knowledge collection descriptors
are handled exclusively by IDEA's trusted PaperQA2 integration. The Pipe's
`PAPERQA_ASSISTANT_IDS` Valve must contain the same Assistant IDs; it defaults
to `welcome-assistant,sea,mars-assistant`.

Each deployment-managed Assistant receives exactly six Workspace suggested
prompts through its own `meta.suggestion_prompts`; this does not modify Open
WebUI's global/default prompt suggestions. SEA and Mars define domain-specific
sets in `manifest.json`. Welcome uses `default_suggestion_prompts`, and any new
manifest Assistant that omits `suggestion_prompts` inherits that same Welcome
set automatically. Run with `--reconcile` to apply prompt changes to existing
official Assistants; seed mode continues to preserve existing UI edits.

`welcome_suggestion_assistant_ids` is a narrow opt-in for existing Assistants
that should receive Welcome's suggestions without becoming fully
deployment-managed. CIndRA is currently listed. For these entries, deployment
updates only `meta.suggestion_prompts` and preserves the Assistant's ownership,
instructions, capabilities, access grants, active state, and all other fields.

`assets/uhslc.svg` preserves the source UHSLC mark on a black square
background; `assets/uhslc.png` is its 512×512 deployment rendering.
