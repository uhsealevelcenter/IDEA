#!/bin/bash
# Registers (or updates) openwebui/functions/idea_pipe.py as a Pipe function
# in a running Open WebUI instance via its admin API, so you don't have to
# manually copy/paste the file into Admin Panel > Functions every time it
# changes.
#
# Open WebUI Functions live in its own database (not on disk), so this file
# being present in this folder does NOT register it automatically - either
# this script or the manual UI steps in ./README.md must be run at least
# once per Open WebUI instance/database.
#
# Requires:
#   - Open WebUI already running and reachable (see docker-compose.yml)
#   - An ADMIN account's static API key (Settings > Account > API Keys,
#     after logging in as an admin) - the same key you'd set as
#     OPENWEBUI_API_KEY in .env works fine here too, as long as that
#     account has the admin role.
#
# Usage:
#   OPENWEBUI_API_KEY=sk-... ./openwebui/register_idea_pipe.sh
#   OPENWEBUI_BASE_URL=http://localhost:3001 OPENWEBUI_API_KEY=sk-... ./openwebui/register_idea_pipe.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PIPE_FILE="${SCRIPT_DIR}/functions/idea_pipe.py"

# Falls back to .env in the repo root if OPENWEBUI_API_KEY isn't already
# exported in the shell environment.
if [ -z "${OPENWEBUI_API_KEY:-}" ] && [ -f "${REPO_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.env"
  set +a
fi

OPENWEBUI_BASE_URL="${OPENWEBUI_BASE_URL:-http://localhost:3001}"
# Preserve an existing deployment's Pipe ID (chat model IDs include it).
FUNCTION_ID="${IDEA_PIPE_FUNCTION_ID:-idea_terminal_agent}"
FUNCTION_NAME="IDEA Agent"

: "${OPENWEBUI_API_KEY:?OPENWEBUI_API_KEY not set - export it or set it in .env (use the admin API key from Settings > Account > API Keys)}"

if [ ! -f "${PIPE_FILE}" ]; then
  echo "Error: ${PIPE_FILE} not found." >&2
  exit 1
fi

command -v python3 >/dev/null 2>&1 || { echo "Error: python3 is required (used to JSON-encode the function source)." >&2; exit 1; }

AUTH_HEADER="Authorization: Bearer ${OPENWEBUI_API_KEY}"
API_BASE="${OPENWEBUI_BASE_URL%/}/api/v1/functions"

# Build the JSON request body. python3 -c handles proper escaping of the
# Python source (quotes, newlines) - much safer than hand-rolled sed/jq here.
PAYLOAD="$(python3 - "$FUNCTION_ID" "$FUNCTION_NAME" "$PIPE_FILE" <<'PY'
import json
import sys

function_id, function_name, pipe_file = sys.argv[1:4]
with open(pipe_file, "r") as f:
    content = f.read()

print(json.dumps({
    "id": function_id,
    "name": function_name,
    "content": content,
    "meta": {
        "description": "Bridges Open WebUI chat to IDEA's langgraph agent + sandbox_service.",
        "manifest": {},
    },
}))
PY
)"

echo "==> Attempting to create function '${FUNCTION_ID}' at ${OPENWEBUI_BASE_URL}..."
CREATE_STATUS="$(curl -s -o /tmp/idea_pipe_create_response.json -w '%{http_code}' \
  -X POST "${API_BASE}/create" \
  -H "${AUTH_HEADER}" \
  -H "Content-Type: application/json" \
  -d "${PAYLOAD}")"

if [ "${CREATE_STATUS}" = "200" ]; then
  RESPONSE_FILE=/tmp/idea_pipe_create_response.json
  echo "==> Created '${FUNCTION_ID}'."
else
  echo "==> Create failed (HTTP ${CREATE_STATUS}, likely already exists) - updating instead..."
  UPDATE_STATUS="$(curl -s -o /tmp/idea_pipe_update_response.json -w '%{http_code}' \
    -X POST "${API_BASE}/id/${FUNCTION_ID}/update" \
    -H "${AUTH_HEADER}" \
    -H "Content-Type: application/json" \
    -d "${PAYLOAD}")"
  if [ "${UPDATE_STATUS}" != "200" ]; then
    echo "Error: update also failed (HTTP ${UPDATE_STATUS}):" >&2
    cat /tmp/idea_pipe_update_response.json >&2
    exit 1
  fi
  RESPONSE_FILE=/tmp/idea_pipe_update_response.json
  echo "==> Updated '${FUNCTION_ID}'."
fi

IS_ACTIVE="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('is_active', False))" "${RESPONSE_FILE}")"
if [ "${IS_ACTIVE}" != "True" ]; then
  echo "==> Enabling '${FUNCTION_ID}'..."
  curl -s -o /dev/null -w '%{http_code}\n' \
    -X POST "${API_BASE}/id/${FUNCTION_ID}/toggle" \
    -H "${AUTH_HEADER}"
fi

rm -f /tmp/idea_pipe_create_response.json /tmp/idea_pipe_update_response.json

echo "==> Done. '${FUNCTION_NAME}' should now appear in the model dropdown for a new chat."
echo "    If INTERNAL_SERVICE_TOKEN is set in .env, also paste the same value into"
echo "    this function's INTERNAL_SERVICE_TOKEN Valve (Admin Panel > Functions > ${FUNCTION_NAME} > Valves)."
