#!/usr/bin/env bash
# ==============================================================================
# deployment/deploy.sh
#
# Single, all-in-one deployment & startup script for IDEA.
# Handles environment setup, database initialization, container launch,
# health checks, and Open WebUI post-deploy reconciliation.
#
# Usage:
#   ./deployment/deploy.sh <dev|staging|next-dev|prod>
# ==============================================================================
set -euo pipefail

TARGET_ENV="${1:-}"

if [ -z "${TARGET_ENV}" ]; then
  echo "Usage: $0 <dev|staging|next-dev|prod>" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Clean indicator helpers
step() {
  echo ""
  echo "==> [STEP] $1"
}

success() {
  echo "    [SUCCESS] $1"
}

fail() {
  echo ""
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  echo "  [FAILURE] $1"
  echo "----------------------------------------------------------"
  echo "  Diagnosis & Suggested Fix:"
  echo "  $2"
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  exit 1
}

echo "=========================================================="
echo "==> Deploying IDEA Stack for environment: [${TARGET_ENV}]"
echo "=========================================================="

# ------------------------------------------------------------------------------
# Stage 1: Environment Setup
# ------------------------------------------------------------------------------
step "[Stage 1/5] Setting up environment configuration..."

if [[ ! -f "db/.env" || ! -f "langgraph/.env" || ! -f "openwebui/.env" ]]; then
  echo "    Running ./deployment/setup_env.sh..."
  "${SCRIPT_DIR}/setup_env.sh" || fail "setup_env.sh failed." "Check file permissions and ensure openssl is installed."
fi

echo "    Exporting parameters from deployment/config.yaml for '${TARGET_ENV}'..."
eval "$(python3 "${SCRIPT_DIR}/load_env.py" "${TARGET_ENV}")" || fail "load_env.py failed." "Check deployment/config.yaml syntax."

if [ -f ./.env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi
success "Environment configuration loaded."

# ------------------------------------------------------------------------------
# Stage 2: Database Initialization (Idempotent)
# ------------------------------------------------------------------------------
step "[Stage 2/5] Verifying and initializing database roles & schemas (~1-2 min on initial build)..."

"${SCRIPT_DIR}/db/setup_all_databases.sh" || fail \
  "Database initialization failed." \
  "Check that Docker is running and PostgreSQL credentials match in db/.env, langgraph/.env, and litellm/.env."
success "Database roles and schemas for LiteLLM, LangGraph, and Langfuse are ready."

# ------------------------------------------------------------------------------
# Stage 3: Start Services
# ------------------------------------------------------------------------------
step "[Stage 3/5] Starting Docker Compose services (~1-2 min)..."

# Authenticate with GHCR if credentials exist (e.g. from root .env, sandbox_service/.env, or environment)
GHCR_USER="${GHCR_USERNAME:-$(grep -E '^GHCR_USERNAME=' sandbox_service/.env 2>/dev/null | cut -d= -f2- || true)}"
GHCR_TOKEN="${GHCR_PAT:-$(grep -E '^GHCR_PAT=' sandbox_service/.env 2>/dev/null | cut -d= -f2- || true)}"

if [[ -n "${GHCR_USER}" && -n "${GHCR_TOKEN}" ]]; then
  echo "    Logging in to ghcr.io using ${GHCR_USER} credentials..."
  echo "${GHCR_TOKEN}" | docker login ghcr.io -u "${GHCR_USER}" --password-stdin >/dev/null 2>&1 || true
fi

echo "    Launching containers..."
if [[ "${TARGET_ENV}" == "dev" ]]; then
  DOCKER_BUILDKIT=1 docker compose up -d --remove-orphans --quiet-pull >/dev/null 2>&1 || docker compose up -d --remove-orphans || fail "docker compose up failed." "Check 'docker compose logs' for container errors."
else
  DOCKER_BUILDKIT=1 docker compose up -d --build --remove-orphans --quiet-pull >/dev/null 2>&1 || docker compose up -d --build --remove-orphans || fail "docker compose up failed." "Check 'docker compose logs' for build or container errors."
fi
success "Docker containers started in background."

# ------------------------------------------------------------------------------
# Stage 4: Health Checks
# ------------------------------------------------------------------------------
step "[Stage 4/5] Running service health checks (~30-60 sec)..."

# 4a. Database
echo "    Checking PostgreSQL container health..."
if ! docker compose exec -T db pg_isready -U "${POSTGRES_USER:-idea_user}" -d "${POSTGRES_DB:-idea_db}" >/dev/null 2>&1; then
  fail "PostgreSQL health check failed." "The 'db' service is not ready. Run 'docker compose logs db' for details."
fi
success "Database is healthy."

# 4b. LangGraph
echo "    Waiting for LangGraph service to become ready..."
lg_ready=false
for attempt in $(seq 1 20); do
  if docker compose exec -T langgraph curl -fsS http://localhost:8010/health >/dev/null 2>&1; then
    lg_ready=true
    break
  fi
  sleep 2
done

if [[ "${lg_ready}" != "true" ]]; then
  fail "LangGraph internal health check failed." \
       "LangGraph is not responding at http://localhost:8010/health. Run 'docker compose logs langgraph' for logs."
fi
success "LangGraph service is healthy."

# 4c. LiteLLM & Virtual Key Generation
echo "    Waiting for LiteLLM proxy to become healthy (up to 3 min)..."
litellm_ready=false
for attempt in $(seq 1 36); do
  if docker compose exec -T langgraph python -c "
import urllib.request
try:
    with urllib.request.urlopen('http://litellm:8080/health/liveliness', timeout=3) as resp:
        exit(0 if resp.status == 200 else 1)
except Exception:
    exit(1)
" 2>/dev/null; then
    litellm_ready=true
    break
  fi
  sleep 5
done

if [[ "${litellm_ready}" != "true" ]]; then
  fail "LiteLLM health check failed." "LiteLLM did not become healthy within 3 minutes. Check 'docker compose logs litellm'."
fi
success "LiteLLM proxy is healthy."

echo "    Checking virtual key authorization..."
litellm_master="$(grep -E '^LITELLM_MASTER_KEY=' litellm/.env 2>/dev/null | cut -d= -f2- || true)"
current_key="$(grep -E '^LITELLM_VIRTUAL_KEY=' langgraph/.env 2>/dev/null | cut -d= -f2- || true)"

virt_key_valid="false"
if [[ -n "${current_key}" ]]; then
  virt_key_valid="$(docker compose exec -T langgraph python -c "
import urllib.request, json, os
req = urllib.request.Request('http://litellm:8080/health/liveliness', headers={'Authorization': 'Bearer ${current_key}'})
try:
    with urllib.request.urlopen(req) as resp:
        exit(0)
except Exception:
    exit(1)
" 2>/dev/null && echo "true" || echo "false")"
fi

if [[ "${virt_key_valid}" != "true" && -n "${litellm_master}" ]]; then
  echo "    LITELLM_VIRTUAL_KEY is missing or invalid in database; generating new shared virtual key..."
  new_virt_key="$(docker compose exec -T langgraph python -c "
import urllib.request, json, time, sys
master = '${litellm_master}'
url = 'http://litellm:8080/key/generate'
headers = {
    'Authorization': f'Bearer {master}',
    'Content-Type': 'application/json'
}
data = json.dumps({
    'max_budget': 100,
    'models': ['gpt-5.6-terra', 'gpt-5.6-sol', 'gpt-5.6-luna', 'gpt-5.5', 'gpt-6-astra', 'text-embedding-3-small'],
    'key_alias': f'idea-langgraph-shared-{int(time.time())}'
}).encode()
req = urllib.request.Request(url, data=data, headers=headers, method='POST')
try:
    with urllib.request.urlopen(req) as resp:
        print(json.loads(resp.read().decode()).get('key', ''))
except Exception as e:
    sys.stderr.write(f'LiteLLM key generation error: {e}\n')
" | grep -E '^sk-' | head -n 1 | tr -d '\r\n' || true)"

  if [[ -n "${new_virt_key}" && "${new_virt_key}" == sk-* ]]; then
    echo "    Generated and registered new virtual key: ${new_virt_key:0:8}..."
    python3 -c "
from pathlib import Path
f = Path('langgraph/.env')
if f.exists():
    text = f.read_text()
    lines = [f'LITELLM_VIRTUAL_KEY=${new_virt_key}' if l.startswith('LITELLM_VIRTUAL_KEY=') else l for l in text.splitlines()]
    if not any(l.startswith('LITELLM_VIRTUAL_KEY=') for l in lines):
        lines.append(f'LITELLM_VIRTUAL_KEY=${new_virt_key}')
    f.write_text('\n'.join(lines) + '\n')
"
    docker compose up -d --force-recreate langgraph >/dev/null 2>&1 || true
    success "Virtual key generated and injected into LangGraph."
  else
    fail "Failed to generate LiteLLM virtual key." "Check LiteLLM master key and verify LiteLLM database connection."
  fi
fi

# 4d. Open WebUI
echo "    Waiting for Open WebUI to respond..."
ow_ready=false
for attempt in $(seq 1 24); do
  if "${SCRIPT_DIR}/check_openwebui.sh" "http://localhost:3001" 3 >/dev/null 2>&1; then
    ow_ready=true
    break
  fi
  sleep 4
done

if [[ "${ow_ready}" != "true" ]]; then
  fail "Open WebUI health check failed." \
       "Open WebUI did not respond on http://localhost:3001/health. Run 'docker compose logs openwebui'."
fi
success "Open WebUI service is healthy."

# 4d. Nginx
echo "    Verifying Nginx reverse proxy..."
if ! curl -fsS -o /dev/null --connect-timeout 5 "http://localhost:80/" 2>/dev/null; then
  echo "    [NOTICE] Nginx on port 80 is not responding directly. Open WebUI is directly accessible on port 3001."
else
  success "Nginx is proxying traffic on port 80."
fi

# ------------------------------------------------------------------------------
# Stage 5: Post-Deployment Open WebUI Reconciliation
# ------------------------------------------------------------------------------
step "[Stage 5/5] Checking Open WebUI configuration & Pipe registration..."

ow_api_key="$(grep -E "^OPENWEBUI_API_KEY=" openwebui/.env 2>/dev/null | cut -d= -f2- || true)"

if [[ -z "${ow_api_key}" || "${ow_api_key}" == *"your_"* ]]; then
  echo "    OPENWEBUI_API_KEY is unset; automatically generating and syncing admin API key..."
  "${SCRIPT_DIR}/update_openwebui_key.sh" >/dev/null 2>&1 || true
  ow_api_key="$(grep -E "^OPENWEBUI_API_KEY=" openwebui/.env 2>/dev/null | cut -d= -f2- || true)"
  export OPENWEBUI_API_KEY="${ow_api_key}"
fi

if [[ -n "${ow_api_key}" && "${ow_api_key}" != *"your_"* ]]; then
  echo "    Reconciling Pipe function, task model, and assistants..."
  OPENWEBUI_API_KEY="${ow_api_key}" OPENWEBUI_BASE_URL="http://localhost:3001" "${SCRIPT_DIR}/post_deploy/register_idea_pipe.sh" || fail "Failed to register IDEA pipe." "Check OPENWEBUI_API_KEY validity or ensure the admin user exists."
  OPENWEBUI_API_KEY="${ow_api_key}" OPENWEBUI_BASE_URL="http://localhost:3001" "${SCRIPT_DIR}/post_deploy/configure_openwebui.py" || fail "Failed to configure Open WebUI settings." "Check LiteLLM connection."
  
  if [[ "${TARGET_ENV}" == "prod" ]]; then
    OPENWEBUI_API_KEY="${ow_api_key}" OPENWEBUI_BASE_URL="http://localhost:3001" "${SCRIPT_DIR}/post_deploy/deploy_assistants_openwebui.py" --reconcile || fail "Failed to seed assistants." "Check assistants/manifest.json."
  else
    OPENWEBUI_API_KEY="${ow_api_key}" OPENWEBUI_BASE_URL="http://localhost:3001" "${SCRIPT_DIR}/post_deploy/deploy_assistants_openwebui.py" || fail "Failed to seed assistants." "Check assistants/manifest.json."
  fi

  echo "    Restarting Nginx to apply routing..."
  docker compose restart nginx >/dev/null 2>&1 || true
  success "Pipe functions and assistants reconciled."
else
  fail "Could not obtain OPENWEBUI_API_KEY." "Ensure Open WebUI created its admin account and is running at http://localhost:3001."
fi

echo ""
echo "=========================================================="
echo "==> Deployment for [${TARGET_ENV}] completed successfully!"
echo "=========================================================="
echo " - Chat Web UI:            http://localhost (or http://localhost:3001)"
echo " - Langfuse Observability: http://localhost:3050"
echo " - LiteLLM Admin / Health: http://localhost:8030/health"
echo " - LangGraph Health:       http://localhost:8010/health"
echo "=========================================================="
