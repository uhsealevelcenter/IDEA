# Quick Deploy

This is the short production-style deployment path for IDEA-next; use the
root [`README.md`](../README.md) and component READMEs when troubleshooting
or changing defaults.

## Prerequisites

- A Linux host with Docker Engine, Docker Compose v2, Git, Python 3, and enough storage for persistent Docker volumes.
- A working `/dev/kvm` device for the production microsandbox backend; without it, IDEA falls back to a limited local-shell backend.
- An OpenAI-compatible API endpoint and key, plus access to the pinned Open WebUI and sandbox images in GHCR when those packages are private.
- An external reverse proxy or load balancer that provides HTTPS and routes the instance hostname to Open WebUI on host port `3001`.

## Deploy

1. Check out the desired branch on the server:

   ```bash
   git clone https://github.com/uhsealevelcenter/IDEA.git
   cd IDEA
   git checkout idea-next/system-prompt-AND-functions
   ```

2. Create the environment file and replace every placeholder secret:

   ```bash
   cp example.env .env
   ```

   At minimum, set `OPENAI_API_KEY`, `OPENAI_BASE_URL`, the Postgres
   credentials, `WEBUI_SECRET_KEY`, `LITELLM_DB_PASSWORD`,
   `LANGGRAPH_DB_PASSWORD`, `LANGGRAPH_AES_KEY`, `IDEA_IDENTITY_SECRET`,
   `LITELLM_MASTER_KEY`, `LITELLM_VIRTUAL_KEY`, and
   `INTERNAL_SERVICE_TOKEN`; set `KVM_DEVICE_PATH=/dev/kvm` on the production
   host and review `ENABLE_SIGNUP`, `CORS_ORIGINS`, registry credentials, and
   the model names.

3. Create the isolated LiteLLM and LangGraph database roles, schemas, and
   checkpoint tables:

   ```bash
   ./litellm/setup_litellm_db.sh
   ./langgraph/db/setup_langgraph_db.sh
   ```

4. Seed the shared scientific-data volume before first use, if a legacy data
   tree is available:

   ```bash
   docker compose run --rm --build \
     --volume "/path/to/legacy/IDEA/data:/source:ro" \
     shared-data import /source
   docker compose run --rm shared-data status
   ```

5. Build and start the production service set:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml \
     up -d --build
   docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
   ```

6. Open the instance, create the first Open WebUI account as the administrator,
   generate an API key under **Settings > Account > API Keys**, and save it as
   `OPENWEBUI_API_KEY` in `.env`.

7. Register the IDEA Pipe and reconcile IDEA-owned Open WebUI settings and
   official Assistants:

   ```bash
   OPENWEBUI_BASE_URL=http://localhost:3001 \
     ./openwebui/register_idea_pipe.sh
   OPENWEBUI_BASE_URL=http://localhost:3001 \
     ./openwebui/configure_openwebui.py
   OPENWEBUI_BASE_URL=http://localhost:3001 \
     ./assistants/deploy_assistants_openwebui.py
   ```

8. In **Admin Panel > Functions > IDEA Agent > Valves**, set
   `INTERNAL_SERVICE_TOKEN` to the same value used in `.env`, then disable
   public signup if required and restart the services after any `.env`
   changes:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml \
     up -d
   ```

## Verify

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
curl -f http://localhost:3001/
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs \
  --tail=100 openwebui langgraph sandbox litellm
```

Log in through the public HTTPS URL and confirm that Welcome Assistant can
answer a prompt, run Python, read an uploaded file and image, use PaperQA on
an attached PDF, and return a downloadable artifact whose link still works
on a later turn.

## Update or Roll Back

Fetch and check out the target ref, rerun the database setup, rebuild the
Compose services, and rerun the three Open WebUI deployment scripts above;
back up the named Docker volumes before migrations or rollback testing.

Do not run `interpreter_kernel/refresh_sandboxes.sh` as a routine deployment
step: it recreates existing microVMs and wipes their writable filesystem
state.

The current GitHub Actions workflow maps only `dev`, `staging`, and `main`,
so a `next-dev` instance needs its branch-to-environment mapping, GitHub
Environment secrets/variables, DNS/TLS route, and smoke-check URL configured
before automated deployment can be used.
