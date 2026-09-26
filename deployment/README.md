# IDEA Deployment & Setup Guide

This directory houses all infrastructure, provisioning, database initialization, and deployment configuration for IDEA across all environments (`dev`, `staging`, `next-dev`, `prod`).

---

## Directory Overview

```
deployment/
├── config.yaml                     # Central environment configuration (ports, sandbox limits, SSL)
├── load_env.py                     # Zero-dependency environment exporter (eval "$(python3 deployment/load_env.py <env>)")
├── deploy.sh                       # All-in-one deployment & startup runner (used by CI/CD and local development)
├── setup_env.sh                    # Interactive per-service .env generator & password creator
├── update_openwebui_key.sh         # Helper to generate/sync Open WebUI admin API key into .env files
├── check_openwebui.sh              # Health check utility for Open WebUI
├── renew-production-cert.sh        # Production Certbot TLS renewal cron script
│
├── db/                             # Database provisioning & schema migrations
│   ├── setup_all_databases.sh      # Master runner for initializing all 3 service databases
│   ├── setup_litellm_db.sh & init_litellm_db.sql
│   ├── setup_langgraph_db.sh & init_langgraph_db.sql
│   └── setup_langfuse_db.sh & init_langfuse_db.sql
│
└── post_deploy/                    # Post-deploy Open WebUI API reconciliation
    ├── register_idea_pipe.sh       # Registers/updates openwebui/functions/idea_pipe.py via API
    ├── configure_openwebui.py      # Reconciles LiteLLM connection, task model, and prompt settings
    └── deploy_assistants_openwebui.py # Seeds official assistants (Welcome, SEA, Mars) from manifest
```

---

## 1. Environment Variables Configuration

Environment variables are managed **per-service** in each service's respective directory.

### Automated Setup (Recommended)
You can run the interactive setup helper, which generates `.env` files for all services from their templates and automatically populates strong random passwords, AES keys, and session secrets:

```bash
./deployment/setup_env.sh
```

*(Pass `--force` if you want to overwrite and regenerate existing `.env` files).*

After running the script, simply fill in your `OPENAI_API_KEY` and `OPENAI_BASE_URL` in `langgraph/.env` and `litellm/.env`.

---

### Manual Setup
Alternatively, copy each `.env.example` file to `.env`:

```bash
cp db/.env.example db/.env
cp redis/.env.example redis/.env
cp nginx/.env.example nginx/.env
cp langgraph/.env.example langgraph/.env
cp sandbox_service/.env.example sandbox_service/.env
cp litellm/.env.example litellm/.env
cp langfuse/.env.example langfuse/.env
cp openwebui/.env.example openwebui/.env
```

*(Note: For backward compatibility, values in a root `.env` file are also read if present).*

### Variable Descriptions by Service

#### 1. `db/.env` (PostgreSQL with pgvector)
- `POSTGRES_USER`: The administrative superuser username for PostgreSQL.
- `POSTGRES_PASSWORD`: The password for the PostgreSQL superuser.
- `POSTGRES_DB`: The default database name created on startup (`idea_db`).
- `POSTGRES_SERVER`: Hostname of the database container on the docker network (`db`).
- `POSTGRES_PORT`: Port exposed inside the docker network (`5432`).

#### 2. `redis/.env` (Redis Cache & Queue)
- `REDIS_HOST_PORT`: Host port mapping for local dev inspection (e.g. `127.0.0.1:6380:6379`).

#### 3. `nginx/.env` (Reverse Proxy)
- `NGINX_PORT_HTTP`: HTTP host port (default: `80`).
- `NGINX_PORT_HTTPS`: HTTPS host port (set to `443:443` in production; leave empty for HTTP-only).
- `NGINX_HTTPS_CONF`: Path to HTTPS config file (set to `./nginx/nginx-https-prod.conf` on prod; `/dev/null` on dev/staging).
- `CERTBOT_CONF_DIR`: Mount path for Let's Encrypt certificates (`./certbot/conf`).
- `CERTBOT_WWW_DIR`: Mount path for Certbot HTTP-01 renewal challenges (`./certbot/www`).

#### 4. `langgraph/.env` (LangGraph Agent Runtime)
- `IDEA_AGENT_MODEL`: Primary conversational model identifier (e.g. `gpt-5.6-terra`).
- `IDEA_AGENT_REASONING_EFFORT`: Reasoning depth/effort for the agent model (`low`, `medium`, `high`).
- `IDEA_TOOL_MODEL`: Auxiliary model used for sub-agent tool evaluation.
- `IDEA_ADVANCED_AGENT_MODEL`: Model used for advanced agent tasks using the Responses API (e.g. `gpt-5.6-sol`).
- `IDEA_ADVANCED_REASONING_EFFORT`: Reasoning depth for the advanced agent model.
- `IDEA_MODEL_REQUEST_TIMEOUT_SECONDS`: Maximum request timeout for model completions.
- `IDEA_MODEL_MAX_RETRIES`: Number of retry attempts for failed model requests.
- `IDEA_CODEX_ENABLED`: Toggle for delegating code-generation tasks to Codex (`true`/`false`).
- `IDEA_CODEX_MODEL`: Model used for Codex generation (`gpt-5.6-terra`).
- `IDEA_CODEX_BASE_URL` / `IDEA_CODEX_API_KEY`: Dedicated upstream credentials for Codex (optional).
- `LANGGRAPH_DB_PASSWORD`: Dedicated PostgreSQL password for the `idea_langgraph` role.
- `LANGGRAPH_AES_KEY`: 16, 24, or 32-character AES secret key used to encrypt agent session checkpoints at rest.
- `IDEA_IDENTITY_SECRET`: Cryptographic secret for hashing and managing user identity scopes.
- `OPENAI_API_KEY`: Upstream Azure OpenAI or OpenAI API key.
- `OPENAI_BASE_URL`: Base URL for the upstream API endpoint (e.g. `https://<resource>.services.ai.azure.com/openai/v1`).
- `INTERNAL_SERVICE_TOKEN`: Shared secret Bearer token guarding internal HTTP communication between LangGraph, Sandbox, and OpenWebUI.
- `OPENWEBUI_BASE_URL`: Base URL used by LangGraph to push artifacts to Open WebUI (`http://openwebui:8080`).
- `OPENWEBUI_API_KEY`: Admin API key used to authenticate artifact uploads into Open WebUI.
- `PQA_LLM_MODEL`: LLM model used by PaperQA for answering and summaries (`gpt-5.6-luna`).
- `PQA_EMBEDDING_MODEL`: Text embedding model for PaperQA document indexing (`text-embedding-3-small`).
- `PQA_LITELLM_BASE_URL`: LiteLLM endpoint used by PaperQA (`http://litellm:8080/v1`).
- `PQA_SYNC_TIMEOUT_SECONDS`: Timeout for downloading and indexing attached research documents.
- `PQA_MAX_PDF_BYTES`: Maximum document size limit for PaperQA (default: 1 GB).
- `SEMANTIC_SCHOLAR_API_KEY`: API key for PaperQA academic search (optional).

#### 5. `sandbox_service/.env` (MicroVM Execution Sandbox)
- `KVM_DEVICE_PATH`: Host virtualization device. **Notice: Microsandbox microVMs require hardware `/dev/kvm` support on Linux (e.g. Jetstream2). On macOS or Windows, leave this as `/dev/null`, and the service will automatically fall back to the safe local terminal backend.**
- `SANDBOX_BACKEND`: Sandbox mode (`auto`, `microsandbox`, or `local`).
- `SANDBOX_CPUS` / `SANDBOX_MEMORY_MB` / `SANDBOX_DISK_MB`: Hardware resource limits allocated per microVM (overridden per environment via `deployment/config.yaml`).
- `SANDBOX_IMAGE`: OCI container image booted for user sessions (`ghcr.io/uhsealevelcenter/idea-oi-kernel:slim`).
- `GHCR_USERNAME` / `GHCR_PAT`: GitHub Container Registry credentials if pulling private sandbox images.
- `INTERNAL_SERVICE_TOKEN`: Must match the `INTERNAL_SERVICE_TOKEN` set in `langgraph/.env`.
- `SANDBOX_IDLE_TIMEOUT_SECONDS`: Seconds of inactivity before an idle microVM is suspended.

#### 6. `litellm/.env` (LiteLLM Proxy)
- `LITELLM_DB_PASSWORD`: Dedicated PostgreSQL password for the `litellm` role and schema.
- `LITELLM_MASTER_KEY`: Administrative master key for configuring routes and virtual keys in LiteLLM.
- `LITELLM_VIRTUAL_KEY`: Shared virtual key with assigned budget limits for user traffic.
- `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`: API keys from Langfuse for tracing proxy requests.
- `OPENAI_API_KEY` / `OPENAI_BASE_URL`: Upstream inference credentials forwarded by the proxy.

#### 7. `langfuse/.env` (Observability & Tracing)
- `LANGFUSE_DB_PASSWORD`: Dedicated PostgreSQL password for the `langfuse` role and schema.
- `LANGFUSE_NEXTAUTH_SECRET`: Secret key used by NextAuth to sign session cookies.
- `LANGFUSE_SALT`: Salt string used for password hashing.
- `LANGFUSE_ENCRYPTION_KEY`: 32-character AES key for encrypting stored credentials at rest.
- `NEXTAUTH_URL`: Canonical public URL of the Langfuse service (`http://localhost:3050`).
- `AUTH_DISABLE_SIGNUP`: Disables open account registration on the Langfuse UI (`true`).
- `LANGFUSE_INIT_*`: Optional bootstrap variables to pre-create the organization, project, and administrator user.

#### 8. `openwebui/.env` (Chat Interface)
- `WEBUI_SECRET_KEY`: Encryption secret for Open WebUI sessions and cookies.
- `ENABLE_SIGNUP`: Enables user self-registration (`true`).
- `DEFAULT_USER_ROLE`: Initial role assigned to new signups (`pending` puts them in the admin queue for approval).
- `TASK_MODEL_EXTERNAL`: Model used for title generation and auxiliary tasks (`gpt-5.6-luna`).
- `ENABLE_CONTEXT_COMPACTION`: Automatically compacts long chat histories to prevent context overflow.
- `CONTEXT_COMPACTION_TOKEN_THRESHOLD`: Token count that triggers chat compaction (e.g. `136000`).
- `OPENWEBUI_LITELLM_BASE_URL`: Internal URL to reach the LiteLLM proxy (`http://litellm:8080/v1`).
- `WEBUI_ADMIN_EMAIL` / `WEBUI_ADMIN_PASSWORD`: Admin user credentials used by automated post-deployment scripts.
- `OPENWEBUI_API_KEY`: Static admin API key for API-driven administration.

---

## 2. Database Initialization

Run the master setup script to provision schemas, users, and tables across `LiteLLM`, `LangGraph`, and `Langfuse`:

```bash
./deployment/db/setup_all_databases.sh
```

Or run individual setup scripts if preferred:
```bash
./deployment/db/setup_litellm_db.sh
./deployment/db/setup_langgraph_db.sh
./deployment/db/setup_langfuse_db.sh
```

---

## 3. Starting Services with deploy.sh (All-in-One Runner)

`deployment/deploy.sh` is the single, fully automated entrypoint for both local development and remote CI/CD deployments. It executes 5 stages:
1. **Stage 1**: Generates missing service `.env` files via `setup_env.sh` and loads `config.yaml` parameters.
2. **Stage 2**: Automatically verifies and initializes PostgreSQL schemas for LiteLLM, LangGraph, and Langfuse.
3. **Stage 3**: Authenticates with GHCR (if credentials are set) and starts all Docker Compose services with quiet, streamlined logging.
4. **Stage 4**: Runs service health checks, polls LiteLLM until ready (up to 3 min), and automatically generates & injects `LITELLM_VIRTUAL_KEY`.
5. **Stage 5**: Automatically generates and syncs `OPENWEBUI_API_KEY`, sets the `INTERNAL_SERVICE_TOKEN` Valve, configures task models, and seeds assistants.

To start any environment:

```bash
# Local development:
./deployment/deploy.sh dev

# Staging:
./deployment/deploy.sh staging

# Next-dev:
./deployment/deploy.sh next-dev

# Production:
./deployment/deploy.sh prod
```

---

## 4. Manual Deployment Workflow (Optional)

If you prefer to start services step-by-step rather than running `deploy.sh`:

1. **Initialize Databases:**
   ```bash
   ./deployment/db/setup_all_databases.sh
   ```
2. **Start Docker Compose:**
   ```bash
   eval "$(python3 deployment/load_env.py dev)"
   docker compose up -d --build
   ```
3. **Sync Open WebUI Key & Reconcile:**
   ```bash
   ./deployment/update_openwebui_key.sh
   ./deployment/post_deploy/register_idea_pipe.sh
   ./deployment/post_deploy/configure_openwebui.py
   ./deployment/post_deploy/deploy_assistants_openwebui.py
   ```

---

## 5. Automated CI/CD Deployments

In GitHub Actions (`.github/workflows/deploy.yml`), deployments execute:
```bash
./deployment/deploy.sh <environment>
```
This handles loading the environment parameters, restarting services, verifying health, and running the post-deployment Open WebUI reconciliation scripts automatically.
