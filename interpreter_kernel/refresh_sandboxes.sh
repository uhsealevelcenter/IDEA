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
# KNOWN BUG (msb 0.6.6): `msb pull -f` (force re-download) can fail with
#   error: cache error at .../cache/layers/sha256_<digest>.tar.gz: No such
#   file or directory (os error 2)
# This has been observed specifically on very small (e.g. 32-byte, "empty
# diff") OCI layers - msb's forced-redownload path appears to mishandle
# re-creating/locking that layer's cache entry. A plain `msb pull` (no
# `-f`) against the same reference does NOT hit this bug and correctly
# resolves to the newly-pushed digest (verify with `msb images`), so we
# fall back to it automatically below rather than failing the whole
# refresh.
#
# If both the forced and non-forced pulls fail, or the resolved digest
# still doesn't match what you just pushed, manually clear the corrupt
# layer's cache entry (both files - a stray/incomplete `.lock` can also
# confuse this) inside the sandbox container and retry:
#   docker exec <container> rm -f /root/.microsandbox/cache/layers/sha256_<digest>.tar.gz*
#   docker exec <container> msb pull "${SANDBOX_IMAGE}"
if ! docker exec "${CONTAINER}" msb pull -f "${SANDBOX_IMAGE}"; then
  echo "==> 'msb pull -f' failed (see known cache bug note above) - retrying without -f..." >&2
  docker exec "${CONTAINER}" msb pull "${SANDBOX_IMAGE}"
fi

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
