#!/bin/bash
# Pulls the current SANDBOX_IMAGE (see docker-compose.yml's `sandbox`
# service) into the `sandbox` service's local microsandbox image cache,
# then removes every currently-existing microVM so a fresh one is created
# from that image - see sandbox_service/msb_sandbox.py's
# MicrosandboxTerminal._connect_or_create(): `image=` is only applied the
# first time a given sandbox_id is created, so an existing (running or
# stopped-but-resumable) VM never picks up a newly-pushed image on its own.
#
# Removing a VM wipes its filesystem (installed packages, any in-progress
# files not yet synced to /outputs) - only run this right after pushing a
# new interpreter_kernel/ build that every active session should pick up.
#
# `msb remove` is used directly (inside the `sandbox` container) rather
# than sandbox_service's own /destroy endpoint, since that endpoint only
# acts on sandbox_ids still present in its in-memory terminal cache (empty
# after any sandbox_service restart) - see terminal_registry.destroy_terminal.
#
# Usage: ./interpreter_kernel/refresh_sandboxes.sh [sandbox-service-name]
#   (sandbox-service-name defaults to "sandbox" - the docker-compose.yml service)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

SANDBOX_SERVICE="${1:-sandbox}"

CONTAINER="$(docker compose ps -q "${SANDBOX_SERVICE}")"
if [ -z "${CONTAINER}" ]; then
  echo "Error: '${SANDBOX_SERVICE}' service is not running (docker compose ps -q returned nothing)." >&2
  exit 1
fi

SANDBOX_IMAGE="$(docker exec "${CONTAINER}" printenv SANDBOX_IMAGE)"
echo "==> Pulling latest '${SANDBOX_IMAGE}' into msb's image cache..."
docker exec "${CONTAINER}" msb pull -f "${SANDBOX_IMAGE}"

echo "==> Listing existing sandboxes..."
mapfile -t NAMES < <(docker exec "${CONTAINER}" msb list 2>/dev/null | tail -n +2 | awk '{print $1}')

if [ "${#NAMES[@]}" -eq 0 ]; then
  echo "No existing sandboxes - nothing to recreate. New sessions will already boot from '${SANDBOX_IMAGE}'."
  exit 0
fi

echo "==> Removing ${#NAMES[@]} existing sandbox(es) so each is recreated fresh from '${SANDBOX_IMAGE}':"
printf '    - %s\n' "${NAMES[@]}"
docker exec "${CONTAINER}" msb remove -f "${NAMES[@]}"

# Proactively trigger recreation now (via a no-op command through
# sandbox_service's own API, so it uses the app's normal
# cpus/memory/idle_timeout config - see msb_sandbox.py's DEFAULT_* env
# vars) instead of leaving each session to hit the ~first-boot latency of
# a fresh microVM + registry pull on its next real request.
INTERNAL_TOKEN="$(docker exec "${CONTAINER}" printenv INTERNAL_SERVICE_TOKEN 2>/dev/null || true)"

echo "==> Recreating ${#NAMES[@]} sandbox(es) now..."
for name in "${NAMES[@]}"; do
  status="$(docker exec "${CONTAINER}" curl -sS -m 120 -o /dev/null -w '%{http_code}' \
    -X POST "http://localhost:8020/sandboxes/${name}/exec" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${INTERNAL_TOKEN}" \
    -d '{"command": "true"}')"
  if [ "${status}" = "200" ]; then
    echo "    - ${name}: recreated (HTTP 200)"
  else
    echo "    - ${name}: FAILED (HTTP ${status})" >&2
  fi
done

echo "==> Done. All listed sandboxes are now running '${SANDBOX_IMAGE}'."
