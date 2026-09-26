#!/usr/bin/env bash
# ==============================================================================
# deployment/setup_env.sh
#
# Generates self-contained per-service .env files directly from their templates.
# Does NOT read or rely on any root-directory .env file.
#
# Automated Generation:
#   - Generates random secure passwords for Postgres, Langfuse, and OpenWebUI
#   - Generates random cryptographic keys for AES encryption and session secrets
#   - Generates random service-to-service internal authentication tokens
#   - Auto-detects Linux /dev/kvm virtualization support
#
# Interactive Prompts (only for external credentials that cannot be auto-generated):
#   - OpenAI / Azure AI endpoint & API key
#   - Optional GHCR credentials (if using private sandbox images)
#
# Usage:
#   ./deployment/setup_env.sh          # Setup / regenerate missing .env files
#   ./deployment/setup_env.sh --force  # Overwrite and regenerate all .env files
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

FORCE=false
if [[ "${1:-}" == "--force" ]]; then
  FORCE=true
fi

rand_hex() {
  local bytes="${1:-32}"
  openssl rand -hex "${bytes}"
}

rand_pass() {
  openssl rand -base64 16 | tr -dc 'a-zA-Z0-9' | head -c 16
}

echo "=========================================================================="
echo " IDEA Service Environment Setup"
echo "=========================================================================="

# ------------------------------------------------------------------------------
# 1. External Credentials (Cannot be automated)
# ------------------------------------------------------------------------------
# Check existing langgraph/.env if present so we don't re-prompt unnecessarily
EXISTING_OA_KEY="$(grep -E '^OPENAI_API_KEY=' langgraph/.env 2>/dev/null | cut -d= -f2- || true)"
EXISTING_OA_BASE="$(grep -E '^OPENAI_BASE_URL=' langgraph/.env 2>/dev/null | cut -d= -f2- || true)"

OA_KEY="${OPENAI_API_KEY:-${EXISTING_OA_KEY}}"
OA_BASE="${OPENAI_BASE_URL:-${EXISTING_OA_BASE}}"

if [[ -z "${OA_KEY}" || "${OA_KEY}" == *"your_"* ]]; then
  if [[ -t 0 ]]; then
    echo "OpenAI / Azure OpenAI Credentials Required:"
    read -r -p "Enter OPENAI_API_KEY: " OA_KEY
    read -r -p "Enter OPENAI_BASE_URL (press Enter for default https://api.openai.com/v1): " OA_BASE
    OA_BASE="${OA_BASE:-https://api.openai.com/v1}"
    echo ""
  else
    echo "Warning: OPENAI_API_KEY is not set. You will need to edit langgraph/.env and litellm/.env manually."
    OA_KEY="your_openai_or_azure_api_key_here"
    OA_BASE="https://api.openai.com/v1"
  fi
fi

# GHCR credentials check
EXISTING_GHCR_USER="$(grep -E '^GHCR_USERNAME=' sandbox_service/.env 2>/dev/null | cut -d= -f2- || true)"
EXISTING_GHCR_PAT="$(grep -E '^GHCR_PAT=' sandbox_service/.env 2>/dev/null | cut -d= -f2- || true)"

GHCR_USER="${GHCR_USERNAME:-${EXISTING_GHCR_USER}}"
GHCR_TOKEN="${GHCR_PAT:-${EXISTING_GHCR_PAT}}"

if [[ -z "${GHCR_USER}" && -t 0 ]]; then
  echo "--------------------------------------------------------------------------"
  read -r -p "Are you setting this up to use a private sandbox image? (if you don't know what this is, say no) [y/N]: " use_private_sandbox
  if [[ "${use_private_sandbox:-n}" =~ ^[Yy]$ ]]; then
    read -r -p "Enter GHCR Username: " GHCR_USER
    read -r -s -p "Enter GHCR Personal Access Token (classic, read:packages): " GHCR_TOKEN
    echo ""
  fi
  echo "--------------------------------------------------------------------------"
fi

if [[ -n "${GHCR_USER}" && -n "${GHCR_TOKEN}" ]]; then
  SANDBOX_IMG="ghcr.io/uhsealevelcenter/idea-oi-kernel:slim"
else
  SANDBOX_IMG="python"
fi

# ------------------------------------------------------------------------------
# 2. Automated Secret Generation
# ------------------------------------------------------------------------------
PG_USER="idea_user"
PG_DB="idea_db"
PG_PASS="$(rand_pass)"

LANGGRAPH_DB_PASS="$(rand_hex 16)"
LITELLM_DB_PASS="$(rand_hex 16)"
LANGFUSE_DB_PASS="$(rand_hex 16)"

LANGGRAPH_AES="$(rand_hex 16)"
IDENTITY_SEC="$(rand_hex 32)"
SHARED_TOKEN="$(rand_hex 32)"

LITELLM_MASTER="sk-$(rand_hex 32)"
LITELLM_VIRT=""

LF_PUB="pk-lf-$(rand_hex 16)"
LF_SEC="sk-lf-$(rand_hex 16)"
LF_NEXTAUTH="$(rand_hex 32)"
LF_SALT="$(rand_hex 32)"
LF_ENC="$(rand_hex 32)"
LF_USER_PASS="$(rand_pass)"

WEBUI_SEC="$(rand_hex 32)"
WEBUI_PASS="$(rand_pass)"
WEBUI_EMAIL="admin@idea.com"

# Auto-detect KVM device
KVM_DEV="/dev/null"
if [[ -c "/dev/kvm" && -w "/dev/kvm" ]]; then
  KVM_DEV="/dev/kvm"
fi

echo "==> Generating service-specific .env files..."

# ------------------------------------------------------------------------------
# 1. db/.env
# ------------------------------------------------------------------------------
if [[ ! -f "db/.env" || "${FORCE}" == true ]]; then
  cat <<EOF > db/.env
PGDATA=/var/lib/postgresql/data/pgdata
POSTGRES_USER=${PG_USER}
POSTGRES_PASSWORD=${PG_PASS}
POSTGRES_DB=${PG_DB}
POSTGRES_SERVER=db
POSTGRES_PORT=5432
EOF
  echo "  -> Created db/.env"
fi

# ------------------------------------------------------------------------------
# 2. redis/.env
# ------------------------------------------------------------------------------
if [[ ! -f "redis/.env" || "${FORCE}" == true ]]; then
  cat <<EOF > redis/.env
REDIS_HOST_PORT=127.0.0.1:6380:6379
EOF
  echo "  -> Created redis/.env"
fi

# ------------------------------------------------------------------------------
# 3. nginx/.env
# ------------------------------------------------------------------------------
if [[ ! -f "nginx/.env" || "${FORCE}" == true ]]; then
  cat <<EOF > nginx/.env
NGINX_PORT_HTTP=80
NGINX_PORT_HTTPS=
NGINX_HTTPS_CONF=/dev/null
CERTBOT_CONF_DIR=./certbot/conf
CERTBOT_WWW_DIR=./certbot/www
EOF
  echo "  -> Created nginx/.env"
fi

# ------------------------------------------------------------------------------
# 4. langgraph/.env
# ------------------------------------------------------------------------------
if [[ ! -f "langgraph/.env" || "${FORCE}" == true ]]; then
  cat <<EOF > langgraph/.env
PYTHONUNBUFFERED=1
REDIS_HOST=redis
REDIS_PORT=6379
SANDBOX_SERVICE_URL=http://sandbox:8020
IDEA_AGENT_RUNTIME=langgraph
IDEA_KERNEL_SCOPE=chat_assistant
IDEA_MAX_STATE_BYTES=524288
IDEA_MAX_RECENT_ACTIONS=50
IDEA_MAX_RECENT_EXECUTIONS=20
IDEA_MAX_CODE_INLINE_BYTES=100000
IDEA_MAX_EXECUTION_MEMORY_BYTES=48000
IDEA_MAX_MODEL_TOOL_OBSERVATION_BYTES=6000
IDEA_MAX_MODEL_HISTORY_MESSAGE_BYTES=16000
IDEA_MAX_IDENTICAL_TOOL_CALLS=3
IDEA_CHECKPOINT_MAP_TTL_SECONDS=31536000
INPUT_SYNC_TIMEOUT_SECONDS=120
INPUT_SYNC_MAX_FILE_BYTES=1073741824
VISION_MAX_IMAGE_BYTES=20971520
VISION_MAX_IMAGES_PER_TURN=8
OUTPUT_SYNC_TIMEOUT_SECONDS=30
OUTPUT_SYNC_MAX_WORKERS=4
PQA_CONVERSION_TIMEOUT_SECONDS=120
PQA_MAX_DOCUMENT_BYTES=1073741824
PQA_MAX_CONVERTED_PDF_BYTES=1073741824
MAX_SKILL_BYTES=100000

IDEA_AGENT_MODEL=gpt-6-sol
IDEA_AGENT_REASONING_EFFORT=medium
IDEA_TOOL_MODEL=gpt-6-luna
IDEA_ADVANCED_AGENT_MODEL=gpt-6-sol-priority
IDEA_ADVANCED_REASONING_EFFORT=medium
IDEA_MODEL_REQUEST_TIMEOUT_SECONDS=180
IDEA_MODEL_MAX_RETRIES=1

IDEA_CODEX_ENABLED=true
IDEA_CODEX_MODEL=gpt-6-sol
IDEA_CODEX_REASONING_EFFORT=medium
IDEA_CODEX_BASE_URL=
IDEA_CODEX_API_KEY=
IDEA_CODEX_MAX_EVENTS=100

LANGGRAPH_DB_PASSWORD=${LANGGRAPH_DB_PASS}
LANGGRAPH_AES_KEY=${LANGGRAPH_AES}
IDEA_IDENTITY_SECRET=${IDENTITY_SEC}

POSTGRES_SERVER=db
POSTGRES_PORT=5432
POSTGRES_DB=${PG_DB}
LANGGRAPH_DATABASE_URL=postgresql://idea_langgraph:${LANGGRAPH_DB_PASS}@db:5432/${PG_DB}?options=-csearch_path%3Didea_langgraph%2Cpublic

OPENAI_API_KEY=${OA_KEY}
OPENAI_BASE_URL=${OA_BASE}
INTERNAL_SERVICE_TOKEN=${SHARED_TOKEN}

OPENWEBUI_BASE_URL=http://openwebui:8080
OPENWEBUI_API_KEY=

LITELLM_VIRTUAL_KEY=${LITELLM_VIRT}
PQA_LLM_MODEL=gpt-6-luna
PQA_EMBEDDING_MODEL=text-embedding-3-small
PQA_LITELLM_BASE_URL=http://litellm:8080/v1
PQA_SYNC_TIMEOUT_SECONDS=300
PQA_MAX_PDF_BYTES=1073741824
SEMANTIC_SCHOLAR_API_KEY=
EOF
  echo "  -> Created langgraph/.env"
fi

# ------------------------------------------------------------------------------
# 5. sandbox_service/.env
# ------------------------------------------------------------------------------
if [[ ! -f "sandbox_service/.env" || "${FORCE}" == true ]]; then
  cat <<EOF > sandbox_service/.env
PYTHONUNBUFFERED=1
SHARED_DATA_HOST_PATH=/srv/idea_shared_data
INPUT_SYNC_MAX_FILE_BYTES=1073741824
SANDBOX_MAX_DURATION_SECONDS=
SANDBOX_PYTHON_INTERRUPT_GRACE_SECONDS=7
SANDBOX_PYTHON_KERNEL_RECOVERY_GRACE_SECONDS=5
SANDBOX_PYTHON_EXECUTION_TIMEOUT_SECONDS=1800
SANDBOX_PYTHON_RUN_STATUS_TTL_SECONDS=3600
KVM_DEVICE_PATH=${KVM_DEV}
SANDBOX_BACKEND=auto
SANDBOX_CPUS=1
SANDBOX_MEMORY_MB=1024
SANDBOX_DISK_MB=4096
SANDBOX_IMAGE=${SANDBOX_IMG}
GHCR_USERNAME=${GHCR_USER}
GHCR_PAT=${GHCR_TOKEN}
INTERNAL_SERVICE_TOKEN=${SHARED_TOKEN}
SANDBOX_IDLE_TIMEOUT_SECONDS=1800
EOF
  echo "  -> Created sandbox_service/.env"
fi

# ------------------------------------------------------------------------------
# 6. litellm/.env
# ------------------------------------------------------------------------------
if [[ ! -f "litellm/.env" || "${FORCE}" == true ]]; then
  cat <<EOF > litellm/.env
REDIS_HOST=redis
REDIS_PORT=6379
LANGFUSE_HOST=http://langfuse:3000
LITELLM_DB_PASSWORD=${LITELLM_DB_PASS}
LITELLM_DATABASE_URL=postgresql://litellm:${LITELLM_DB_PASS}@db:5432/${PG_DB}?schema=litellm
POSTGRES_SERVER=db
POSTGRES_PORT=5432
POSTGRES_DB=${PG_DB}
LITELLM_MASTER_KEY=${LITELLM_MASTER}
LANGFUSE_PUBLIC_KEY=${LF_PUB}
LANGFUSE_SECRET_KEY=${LF_SEC}
OPENAI_API_KEY=${OA_KEY}
OPENAI_BASE_URL=${OA_BASE}
EOF
  echo "  -> Created litellm/.env"
fi

# ------------------------------------------------------------------------------
# 7. langfuse/.env
# ------------------------------------------------------------------------------
if [[ ! -f "langfuse/.env" || "${FORCE}" == true ]]; then
  cat <<EOF > langfuse/.env
LANGFUSE_DB_PASSWORD=${LANGFUSE_DB_PASS}
DATABASE_URL=postgresql://langfuse:${LANGFUSE_DB_PASS}@db:5432/${PG_DB}?schema=langfuse
POSTGRES_SERVER=db
POSTGRES_PORT=5432
POSTGRES_DB=${PG_DB}
NEXTAUTH_SECRET=${LF_NEXTAUTH}
SALT=${LF_SALT}
ENCRYPTION_KEY=${LF_ENC}
NEXTAUTH_URL=http://localhost:3050
AUTH_DISABLE_SIGNUP=true
TELEMETRY_ENABLED=false

LANGFUSE_INIT_ORG_ID=IDEA_admin
LANGFUSE_INIT_ORG_NAME=IDEA_admin
LANGFUSE_INIT_PROJECT_ID=Idea
LANGFUSE_INIT_PROJECT_NAME=Idea_proj
LANGFUSE_INIT_USER_EMAIL=admin@idea.local
LANGFUSE_INIT_USER_NAME="Idea Admin"
LANGFUSE_INIT_USER_PASSWORD=${LF_USER_PASS}
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=${LF_PUB}
LANGFUSE_INIT_PROJECT_SECRET_KEY=${LF_SEC}
EOF
  echo "  -> Created langfuse/.env"
fi

# ------------------------------------------------------------------------------
# 8. openwebui/.env
# ------------------------------------------------------------------------------
if [[ ! -f "openwebui/.env" || "${FORCE}" == true ]]; then
  cat <<EOF > openwebui/.env
WEBUI_NAME=Open WebUI
ENABLE_OLLAMA_API=false
ENABLE_API_KEYS=true
LANGGRAPH_SERVICE_URL=http://langgraph:8010
THREAD_POOL_SIZE=2000
DATABASE_USER_ACTIVE_STATUS_UPDATE_INTERVAL=60
WEBUI_SECRET_KEY=${WEBUI_SEC}
ENABLE_SIGNUP=true
DEFAULT_USER_ROLE=pending
TASK_MODEL_EXTERNAL=gpt-6-luna
ENABLE_CONTEXT_COMPACTION=true
CONTEXT_COMPACTION_TOKEN_THRESHOLD=136000
OPENWEBUI_LITELLM_BASE_URL=http://litellm:8080/v1
WEBUI_ADMIN_EMAIL=admin@idea.com
WEBUI_ADMIN_PASSWORD=${WEBUI_PASS}
OPENWEBUI_API_KEY=
EOF
  echo "  -> Created openwebui/.env"
fi

echo ""
echo "=========================================================================="
echo "==> All service .env files generated successfully!"
echo "=========================================================================="
echo "Generated credentials are stored in the service .env files."
