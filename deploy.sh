#!/usr/bin/env bash
# 1. Set up the LiteLLM and LangGraph databases 
# 2. Rebuild and restart everything, including services built from
#    source on this host (e.g. langgraph) rather than pulled as an
#    image 
# 3. Reconnect Open WebUI to the IDEA chat integration and reseed its
#    IDEA-managed settings/assistants
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

if [ ! -f .env ]; then
  echo "Error: .env not found in ${SCRIPT_DIR}." >&2
  exit 1
fi

echo "Setting up the LiteLLM database..."
./litellm/setup_litellm_db.sh

echo "Setting up the LangGraph database..."
./langgraph/db/setup_langgraph_db.sh

echo "Rebuilding and restarting all services..."
docker compose up -d --build --remove-orphans

OPENWEBUI_BASE_URL="${OPENWEBUI_BASE_URL:-http://localhost:3001}"
echo "Waiting for Open WebUI at ${OPENWEBUI_BASE_URL}..."
tries=0
until curl -fsS -o /dev/null "${OPENWEBUI_BASE_URL}/health" 2>/dev/null; do
  tries=$((tries + 1))
  if [ "${tries}" -ge 120 ]; then
    echo "Error: Open WebUI did not become reachable after 120s." >&2
    exit 1
  fi
  sleep 1
done

echo "Re-registering the IDEA Pipe function..."
OPENWEBUI_BASE_URL="${OPENWEBUI_BASE_URL}" ./openwebui/register_idea_pipe.sh

echo "Reconciling IDEA-managed Open WebUI settings..."
python3 ./openwebui/configure_openwebui.py --base-url "${OPENWEBUI_BASE_URL}"

echo "Seeding/reconciling IDEA Assistants..."
python3 ./assistants/deploy_assistants_openwebui.py --base-url "${OPENWEBUI_BASE_URL}"

echo "Deploy complete."