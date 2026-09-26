#!/usr/bin/env bash
# ==============================================================================
# deployment/check_openwebui.sh
#
# Checks if the Open WebUI service is currently up, healthy, and accepting requests.
#
# Returns:
#   Exit code 0: Open WebUI is healthy and responsive
#   Exit code 1: Open WebUI is down, unreachable, or unhealthy
# ==============================================================================
set -euo pipefail

OPENWEBUI_URL="${1:-${OPENWEBUI_BASE_URL:-http://localhost:3001}}"
TIMEOUT_SECONDS="${2:-10}"

echo "==> Checking Open WebUI service at ${OPENWEBUI_URL}..."

status_code="$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 --max-time "${TIMEOUT_SECONDS}" "${OPENWEBUI_URL%/}/health" 2>/dev/null || echo "000")"

if [[ "${status_code}" == "200" ]]; then
  echo "  [SUCCESS] Open WebUI is healthy and responding (HTTP 200)."
  exit 0
else
  echo "  [FAIL] Open WebUI is not reachable at ${OPENWEBUI_URL} (Status code: ${status_code})."
  echo "         Please ensure 'docker compose up -d' has been run and containers are started."
  exit 1
fi
