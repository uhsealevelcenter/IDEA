# Intelligent Data Exploring Assistant (IDEA)

IDEA is a tool-using AI assistant for scientific data exploration. It is designed to help researchers go from question to analysis and figures quickly while keeping results transparent and reproducible. IDEA is a framework for building domain-focused assistants that run code, generate plots, save outputs, work directly with uploaded datasets, and pull data from the web via its internet-connected environment.

## IDEA vs. SEA

- **IDEA** is the general-purpose framework for creating and working with custom data analysis assistants.
- **SEA (Station Explorer Assistant)** is a special-purpose IDEA configured for sea level data analysis.

**Web access:**
- **SEA (no login required):** https://uhslc.soest.hawaii.edu/research/SEA
- **IDEA (login required):** https://uhslc.soest.hawaii.edu/research/IDEA
- **Account requests:** idea-dev-grp@hawaii.edu

https://github.com/user-attachments/assets/7bea7a70-b72b-484a-a75f-f466cd547e7c

## Why IDEA (vs. a chat-only assistant)

IDEA is action-oriented. It can execute code, inspect data, and produce artifacts you can download. Results are backed by runnable code and intermediate outputs, which supports scientific transparency and reproducibility.

## Core Capabilities

- **Data ingestion:** Load CSV, NetCDF, text, and other common formats; summarize variables, dimensions, ranges, and missingness.
- **Exploratory analysis:** Time series resampling, anomalies, seasonal cycles, trend estimates, and comparisons across stations or regions.
- **Visualization:** Publication-ready plots, quick-look figures, and exportable figure packs.
- **Mapping:** Interactive maps (folium) and static maps (matplotlib/cartopy).
- **Domain workflows:** Sea level and tide-gauge analysis, station lookup, extremes, trends, and climate index context (e.g., El Niño-Southern Oscillation).
- **Reproducible outputs:** Saved plots, tables, and derived datasets with traceable steps.
- **Literature RAG:** Optional literature review using [PaperQA2](https://github.com/Future-House/paper-qa), with locally indexed PDFs for retrieval-augmented answers (via user uploads to their Knowledge base in IDEA or a limited archive of journal articles in SEA).

<p align="center">
  <img src="https://uhslc.soest.hawaii.edu/research/SEAinfo/EngineeringSchematic_details.png" alt="IDEAschematic_details" width="600" />
</p>
Engineering plan of IDEA. Figure 1 from: Widlansky, M. J., & Komar, N. (2025). Building an intelligent data exploring assistant for geoscientists. *JGR: Machine Learning and Computation*, 2, e2025JH000649. https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2025JH000649

## Example Workflow
1) Suggest a topic and ask IDEA to show you how it can help.
2) Propose a research direction, or let IDEA guide you.
3) Check methods and results carefully, and ask for clarification or revision when necessary.
[Sample conversation with IDEA correcting its mistake](https://uhslc.soest.hawaii.edu/idea-api/share/fAIaXflp1JrC_7lttLhdactuoxvxEUBWQHZYlCmAowY)

### Prompting ideas
- “I uploaded a NetCDF—what’s inside?”
- “Plot monthly mean sea level for Honolulu and compare to an El Niño index.”
- “Analyze trends and extremes in the time series.”
- “Generate a self-contained web page showing the methods and results of this analysis.”

## Build Your Own IDEA

IDEA is built to be customized. You can tailor behavior by adding domain instructions, preferred methods, and datasets. The **Instructions** panel enables:

- Custom roles (e.g., “Station Explorer Assistant (SEA)” for analyzing tide gauge data)
- Standardized lab workflows and QA/QC rules
- Consistent output styles across a team
- Reuse of local knowledge and reference datasets via online sources or upload

## How It Works (Conceptual)

IDEA combines:

- A conversational interface with a multimodal large language model (`gpt-5.6-terra` by default, configurable through `IDEA_AGENT_MODEL`)
- Information and data context (provide custom "Instruction" manuals, "Knowledge" documents, and Data files)
- Tool use for real actions (file I/O, code execution, plotting, and reporting)
- Human-driven and reproducible science workflows (code reviews and "Conversation" sharing)

Internally, chat requests flow through [Open WebUI](https://github.com/open-webui/open-webui) (`openwebui/`) into a checkpointed LangGraph agent service (`langgraph/`). It delegates code execution to per-user isolated microVMs (`sandbox_service/`, built on [microsandbox](https://microsandbox.dev/)) running a persistent Python kernel derived from Open Interpreter's execution engine (`interpreter_kernel/`, using [Open Terminal](https://github.com/open-webui/open-terminal) for shell/grep/glob). For substantial coding work, LangGraph can also delegate to Codex inside that same private workspace; Codex is a subordinate coding runtime, not a second conversation agent. See [`docs/Codex-Integration.md`](docs/Codex-Integration.md).

## Limitations and Scientific Caution

IDEA is powerful but not infallible. It can:

- Misinterpret ambiguous requests
- Choose suboptimal methods if assumptions are unclear [Example](https://uhslc.soest.hawaii.edu/idea-api/share/fAIaXflp1JrC_7lttLhdactuoxvxEUBWQHZYlCmAowY)
- Produce results that require domain judgment to validate

Always verify critical results, especially for publication or operational decisions. For example, when conducting a sea level analysis, be mindful of datum shifts, QC flags, record length, and local effects (subsidence/uplift). When necessary, prompt IDEA to check its work.

## Getting Started Locally (requires Docker)

### 1. Clone the Repository

```bash
git clone https://github.com/uhsealevelcenter/IDEA.git
cd IDEA
```

### 2. Configure Environment Variables

Copy `example.env` to `.env` in the project root:

```bash
cp example.env .env
```

At minimum, edit `.env` and set your own values for:

```ini
# LLM provider - replace with your own key and endpoint
# (Azure AI Foundry OpenAI-compatible endpoint, or platform.openai.com)
OPENAI_API_KEY=YOUR_API_KEY_HERE
OPENAI_BASE_URL=YOUR_AZURE_OPENAI_ENDPOINT_HERE

# Database
POSTGRES_DB=idea_db
POSTGRES_USER=idea_user
POSTGRES_PASSWORD=change_this
LANGGRAPH_DB_PASSWORD=change_this

# Durable agent memory (use exactly 16, 24, or 32 characters for AES)
LANGGRAPH_AES_KEY=0123456789abcdef0123456789abcdef
IDEA_IDENTITY_SECRET=change_this

# Open WebUI (generate with: openssl rand -hex 32)
WEBUI_SECRET_KEY=change_this

# LiteLLM proxy (DB: openssl rand -hex 20; master: printf 'sk-'; openssl rand -hex 32)
LITELLM_DB_PASSWORD=change_this
LITELLM_MASTER_KEY=change_this

# Langfuse LLM observability (DB: openssl rand -hex 20; the other three:
# openssl rand -hex 32 each) - see "LLM Observability (Langfuse)" below.
LANGFUSE_DB_PASSWORD=change_this
LANGFUSE_NEXTAUTH_SECRET=change_this
LANGFUSE_SALT=change_this
LANGFUSE_ENCRYPTION_KEY=change_this
```

See `example.env` for the full list of variables and inline comments explaining each one. Several are commented out by default (`PQA_HOME`/`PAPER_DIRECTORY`, `EARTHDATA_USERNAME`/`PASSWORD`, `SECRET_KEY`, `FIRST_SUPERUSER`/`PASSWORD`, `SMTP_*`, `GUEST_*`) - these are leftover from the previous `app.py`/`auth.py`-based backend, which no longer exists in this repo; leave them commented out unless something reintroduces a consumer for them.

IDEA has been tested with several LLM inference providers, including OpenAI (https://platform.openai.com/), Anthropic (https://claude.com/platform/api), and Jetstream2 (https://docs.jetstream-cloud.org/inference-service/overview/).

### 3. Set Up the Service Database Roles

LiteLLM, LangGraph, and Langfuse each use a separate, least-privilege Postgres
role and schema on the shared `db` service. Run all three setup scripts once
before starting the full stack; all are idempotent and safe to re-run:

```bash
./litellm/setup_litellm_db.sh
./langgraph/db/setup_langgraph_db.sh
./langfuse/setup_langfuse_db.sh
```

The LangGraph setup builds its service image and initializes the checkpoint
tables. PostgreSQL checkpoints contain exact tool execution memory and are
encrypted when `LANGGRAPH_AES_KEY` is set.

### 4. Seed Shared Scientific Data

IDEA exposes a centrally maintained, read-only `/app/data` tree to every
user's terminal. Import the allowlisted datasets from legacy IDEA before the
first start:

```bash
docker compose run --rm --build \
  --volume "$(realpath ../../IDEA/data):/source:ro" \
  shared-data import /source

docker compose run --rm shared-data status
```

Only metadata, benchmarks, altimetry, and InSight data are imported. Papers,
HCDP, SJW, `.pqa`, prompts, and every other legacy path are excluded. See
[`shared_data/README.md`](shared_data/README.md) for updates and migration
details.

### 5. Start Local Services

```bash
docker compose up -d --build
```

`docker compose` automatically merges `docker-compose.yml` with `docker-compose.override.yml` (dev-only ports for `langgraph`/`sandbox`/`litellm`, live source mounts, and the `nginx` service) since no `-f` flags are given.

### 6. One-Time Open WebUI Setup

Open WebUI Functions live in its own database, not on disk, so the Pipe function that bridges chat to `langgraph` (`openwebui/functions/idea_pipe.py`) has to be registered once per Open WebUI instance:

1. Open http://localhost, sign up (the first account created becomes admin).
2. Go to **Settings > Account > API Keys** and generate a key for this admin account.
https://docs.openwebui.com/features/authentication-access/api-keys/
3. Register the pipe function using that key:
   ```bash
   OPENWEBUI_API_KEY=<the key from step 2> ./openwebui/register_idea_pipe.sh
   ```
   This POSTs `openwebui/functions/idea_pipe.py` to Open WebUI's `/api/v1/functions` admin API (create-or-update + enable), so you don't have to manually copy/paste the file into **Admin Panel > Functions** every time it changes. Re-run it any time `idea_pipe.py` is edited. (You can still do this manually via **Admin Panel > Functions > "+"** if you prefer.)
4. Configure Open WebUI's hidden external task model:
   ```bash
   ./openwebui/configure_openwebui.py
   ```
   This idempotent deployment step adds the internal LiteLLM connection,
   keeps `gpt-5.6-luna` hidden from the chat model selector, and persists it
   as Open WebUI's **External Task Model**. It also enables context
   compaction at 136,000 tokens (matching legacy IDEA's 50%-of-272,000
   policy), raises Open WebUI's Token Cap to at least the same value while
   preserving an existing higher cap, and installs IDEA's title-generation
   prompt. It reads
   `OPENWEBUI_API_KEY`, `LITELLM_MASTER_KEY`, and
   `TASK_MODEL_EXTERNAL`, `ENABLE_CONTEXT_COMPACTION`, and
   `CONTEXT_COMPACTION_TOKEN_THRESHOLD` from `.env`. It does **not** disable
   Open WebUI's persistent configuration, so unrelated changes made in the
   Admin Panel remain editable and survive restarts. Run it again after
   deployments to reconcile the IDEA-owned settings. If
   `OPENWEBUI_API_KEY` is unavailable or rejected, an interactive run
   securely prompts for the Open WebUI admin login and uses a temporary JWT;
   non-interactive deployments can provide `WEBUI_ADMIN_EMAIL` and
   `WEBUI_ADMIN_PASSWORD` through their secret store.
5. Deploy the official Assistants:
   ```bash
   ./assistants/deploy_assistants_openwebui.py
   ```
   This seeds Welcome Assistant, SEA, and Mars Assistant on top of the visible
   `idea-terminal-agent` base model. It enables private Assistant creation for
   verified users without enabling user-to-user or public sharing. Normal seed
   mode preserves subsequent Admin UI edits; use `--reconcile` to restore the
   repository definitions. User-created Assistants are never modified.
6. Start a new chat. **Welcome Assistant** is selected by default on a new
   installation; SEA and Mars Assistant are available from the Assistant
   selector.

#### Optional: `OPENWEBUI_API_KEY` and `INTERNAL_SERVICE_TOKEN`

Two more `.env` variables matter once the above is working, and both require a follow-up step **inside the Open WebUI UI** - setting them in `.env` alone is not enough:

- **`OPENWEBUI_API_KEY`** - the same admin API key from step 2 above, saved into `.env`. Deployment/configuration scripts use it to reconcile Open WebUI settings. Output syncing does not use this shared key; the Pipe forwards the current user's authenticated session so generated files are owned and downloadable by that user.
- **`INTERNAL_SERVICE_TOKEN`** - a shared secret guarding the internal `langgraph`<->`sandbox` and Pipe-function->`langgraph` HTTP calls (generate with `openssl rand -hex 32`). Setting it in `.env` only secures the `langgraph`/`sandbox` side; you must **also** paste the same value into `idea_pipe.py`'s `INTERNAL_SERVICE_TOKEN` Valve in **Admin Panel > Functions > IDEA Agent > Valves**, since Open WebUI Valves are configured through that UI, not read from `.env`. Leave blank only for local dev (both sides fail open when unset).

See `openwebui/README.md` for full details on both.

### 6. Access the App

- Main app: http://localhost

## LLM Observability (Langfuse)

Every call `litellm` makes - from LangGraph's `TerminalAgent`, PaperQA, and Open
WebUI's background-task model - is traced to a self-hosted
[Langfuse](https://langfuse.com/) instance (`langfuse` service), grouped by
conversation (`session_id`) and end user (`trace_user_id`), matching what
`LITELLM_END_USER_HEADER` already does for spend tracking. This uses Langfuse
**v2** (Postgres-only self-host), not the current v3, which additionally
requires ClickHouse, S3/MinIO, and a dedicated Redis; v2 only receives
security patches (no new features) per Langfuse's own docs, so revisit this
choice if that becomes a blocker. See
`docker-compose.yml`'s `langfuse` service and
`litellm/litellm_config.yaml`'s `success_callback`/`failure_callback` for the
wiring.

1. Generate the four Langfuse secrets shown in step 2 above and run
   `./langfuse/setup_langfuse_db.sh` (step 3 above) before first start.
2. Start (or restart) the stack, then open the Langfuse UI - `:3050` in dev
   (`docker-compose.override.yml`), otherwise wherever you route it in
   production (see the Quick Deploy doc) - and create an org/project through
   the normal sign-up flow. Alternatively, set the `LANGFUSE_INIT_*`
   variables in `.env` (see `example.env`) to auto-create the org, project,
   and admin user on first boot without touching the UI - useful for
   scripted/CI environments.
3. Under **Project Settings > API Keys**, generate a public/secret key pair
   and save them into `.env` as `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`
   (or, if you used `LANGFUSE_INIT_*`, they already match the project's
   actual keys - no extra step needed).
4. Restart `litellm` so it picks up the keys:
   ```bash
   docker compose up -d litellm
   ```

A failure to reach Langfuse only logs a warning inside `litellm` - it never
blocks or fails the underlying LLM call.

## Deploying to Production (requires Docker)

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Explicit `-f` flags skip `docker-compose.override.yml`, so dev-only host ports (`langgraph`, `sandbox`, `litellm`) and the extra `nginx` service are never published. `docker-compose.prod.yml` is currently an empty overlay kept for this `-f` combo's sake (its one-time purpose - the `sandbox` `/dev/kvm` passthrough - now lives directly in `docker-compose.yml`, gated behind the `KVM_DEVICE_PATH` env var).

Repeat all three database setup scripts (step 3 above) and the one-time Open
WebUI Function setup (step 5 above) on first deploy. The `langfuse` service
itself is not published to the host by the base `docker-compose.yml` (same as
`langgraph`/`sandbox`/`litellm`) - route it through your reverse proxy only if
you want production UI access to traces; `litellm` reaching it over the
internal compose network is all that's required for tracing to work.

In CI, this is what the `deploy` job in `.github/workflows/deploy.yml` runs remotely over SSH via each environment's `DEPLOY_CMD` GitHub Actions variable.

### Deployment Checklist (Field Notes)

This is a condensed, step-by-step checklist distilled from real deployments,
in the order things actually need to happen. It complements - rather than
replaces - the detailed steps above and in
[`docs/Quick-Deploy.md`](docs/Quick-Deploy.md); use those for full detail on
any individual step.

1. **Set up the databases for LangGraph and LiteLLM** (and Langfuse, if used)
   by running their setup scripts (see "Set Up the Service Database Roles"
   above). This depends on having already generated the several secrets each
   service needs (Postgres passwords, `LANGGRAPH_AES_KEY`,
   `LITELLM_MASTER_KEY`, etc. - see "Configure Environment Variables" above)
   and put them in `.env`.
2. **Build the images.** Building/starting the `openwebui` service requires
   pull access to the custom, IDEA-maintained
   `ghcr.io/uhsealevelcenter/idea-open-webui` base image referenced in
   `openwebui/Dockerfile` - this is a private GHCR package, so request access
   if the pull fails with an authentication error.
3. **Set up Open WebUI's own keys and secrets through its UI** - some values
   (like the admin API key) can only be generated from inside Open WebUI's
   **Settings** panel, not by editing `.env` alone.
4. **Create the admin account(s).** In Open WebUI, the first account signed
   up becomes the admin account (see "One-Time Open WebUI Setup" above).
5. **Run the one-time Open WebUI setup scripts**, which depend on the admin
   API key from step 3 being saved as `OPENWEBUI_API_KEY` in `.env`:
   - `./openwebui/register_idea_pipe.sh`
   - `./openwebui/configure_openwebui.py`
   - `./assistants/deploy_assistants_openwebui.py`
6. **Create a LiteLLM virtual key** with access to all models and a budget
   set to $300 (or whatever your deployment needs; see the `curl` command in
   the `LITELLM_VIRTUAL_KEY` comment in `example.env`), then set it as
   `LITELLM_VIRTUAL_KEY` in `.env`. This depends on `LITELLM_MASTER_KEY`
   already being set in `.env` and the `litellm` service already running.
7. **Migrate `/data`** from the stage or dev environment into the shared data
   volume (see "Seed Shared Scientific Data" above).
8. **After changing keys in `.env`, recreate the affected containers** so
   they receive the updated runtime environment. No image rebuild is needed
   for environment-only changes. On production, run:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate langgraph litellm
   ```

#### Additional Notes / Gotchas

- In `.env`, `OPENWEBUI_API_KEY` must be an Open WebUI **API Key** (generated
  under **Settings > Account > API Keys**), not a JWT/session token - a JWT
  will not work here even though the two can look similar.
- If Open WebUI's Functions data is ever lost or the instance is redeployed
  from scratch, the IDEA Pipe needs to be re-registered by rerunning
  `./openwebui/register_idea_pipe.sh` - Functions live in Open WebUI's own
  database, not in this repo, so they don't come back automatically.
- In `.env`, pin `SANDBOX_IMAGE` to a specific tag, e.g.
  `ghcr.io/uhsealevelcenter/idea-oi-kernel:research-2026.08.17-2` (check `example.env` for the current
  recommended tag).
- Existing microVMs do **not** pick up a changed `SANDBOX_IMAGE`
  automatically - after changing it, recreate the `sandbox` service and
  refresh existing sandboxes with
  `./interpreter_kernel/refresh_sandboxes.sh`. Note this is destructive to
  sandbox filesystem state (see "Update or Roll Back" in
  `docs/Quick-Deploy.md`), so only do this deliberately, not as a routine
  step.

### Security and Deployment Notes

- **Code execution:** IDEA allows an AI model to generate and execute code, isolated per-user in a microsandbox microVM (`sandbox_service/`) rather than in the host environment directly.
- **Local development:** The dev compose override bind-mounts `langgraph/` and `sandbox_service/` source for live reload.
- **Isolation:** `langgraph` and `sandbox` are not published to the host by the base `docker-compose.yml` and are only reachable from other containers on the compose network, plus (in dev) the `INTERNAL_SERVICE_TOKEN` shared secret gating their HTTP APIs.

Docker provides isolation, but it is not a complete security solution for sensitive environments. Treat the `sandbox` service as an execution environment and design your deployment accordingly.

#### Running without microsandbox (e.g. a local Mac)

Microsandbox needs real KVM (Linux) or WHP (Windows) on the **host** to boot isolated microVMs. If you're running `docker compose up` on a typical local machine - most notably **Apple Silicon/Intel Macs, which have no KVM device to pass through** - `sandbox_service` cannot use microsandbox at all, regardless of `SANDBOX_BACKEND`/`KVM_DEVICE_PATH` settings.

`SANDBOX_BACKEND=auto` (the default) detects this automatically: `sandbox_service/terminal_registry.py`'s `_use_microsandbox()` calls `microsandbox_available()` (`sandbox_service/msb_sandbox.py`), which checks that the `microsandbox` package imports **and** that `/dev/kvm` is a real, functional device (a `KVM_GET_API_VERSION` ioctl check - `docker-compose.yml` always binds *something* to `/dev/kvm`, defaulting to the harmless `/dev/null` via `KVM_DEVICE_PATH`, so a plain existence check isn't enough). If either check fails, it silently falls back to a **plain local shell** per `sandbox_id` (`PersistentTerminal`, a `pexpect`-driven bash process inside the `sandbox` container itself, one process per user session rather than one microVM per user).

What this means in practice for local dev:

- **`run_terminal_tool` / `write_file_tool` still work** - they go through the same `PersistentTerminal`/shell interface either way, just without per-user microVM-level isolation (all sessions run as separate shell processes inside the single `sandbox` container, sharing its filesystem rather than an isolated one each).
- **`run_python_tool` (the persistent Jupyter kernel) and `grep_search_tool`/`glob_search_tool` do not work** on the local-shell fallback - they require the microsandbox backend booting the `interpreter_kernel/` guest image, which only exists inside a microVM. `run_python_tool` returns a clear error chunk telling the agent to fall back to `run_terminal_tool` (e.g. `python3 -c "..."`) instead of failing the turn; `grep_search_tool`/`glob_search_tool` raise outright on this backend.
- Set `KVM_DEVICE_PATH=/dev/kvm` in `.env` only on hosts that actually have a working KVM device (e.g. a Jetstream2 VM or other Linux host with virtualization enabled) to get the real per-user microVM isolation and the full tool surface.

## Project Structure

```
.
├── docker-compose.yml             # Base service definitions (db, redis, langgraph, sandbox, litellm, openwebui)
├── docker-compose.override.yml    # Local dev overrides (nginx, dev ports, live-reload mounts) - auto-merged
├── docker-compose.prod.yml        # Explicit production nginx overlay; KVM configuration stays in the base file
├── example.env                    # Template for the .env file (copy and fill in)
├── nginx.conf                     # Dev reverse proxy in front of Open WebUI
├── langgraph/                     # Checkpointed LangGraph runtime and TerminalAgent tools
├── sandbox_service/               # Per-user microsandbox microVM execution service
├── interpreter_kernel/            # OCI image booted per microVM: Open Terminal + persistent Python kernel
├── litellm/                       # LiteLLM proxy config, Dockerfile, and DB setup script
├── langfuse/                      # Langfuse (LLM observability) DB setup script
├── assistants/                    # Official Assistant prompts, logos, manifest, and deployment script
└── openwebui/                     # Open WebUI Pipe function (idea_pipe.py) wiring the chat frontend to langgraph
    ├── functions/idea_pipe.py     # The Pipe function itself
    └── register_idea_pipe.sh      # Registers/updates idea_pipe.py in a running Open WebUI via its admin API
```

## Citation

Widlansky, M. J., & Komar, N. (2025). Building an intelligent data exploring assistant for geoscientists. *JGR: Machine Learning and Computation*, 2, e2025JH000649. https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2025JH000649

## Contributing

Contributions, issue reports, and feature requests are welcome! Please open an issue or a pull request with your changes. General feedback or questions can be emailed to idea-dev-grp@hawaii.edu

## Release

![image](https://github.com/user-attachments/assets/4fe5d3e7-5c1a-4fcd-9274-998e841fb860)

Prototype (v0.1.0) https://doi.org/10.5281/zenodo.15605301

## License

This project is licensed under the MIT License. See `LICENSE`.
