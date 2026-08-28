#!/bin/bash
# Pulls the current SANDBOX_IMAGE (see docker-compose.yml's `sandbox`
# service) into the `sandbox` service's local microsandbox image cache,
# then removes every currently-existing microVM so a fresh one is created.
# Use this after changing either the guest image or creation-time settings
# such as shared volume mounts - see sandbox_service/msb_sandbox.py's
# MicrosandboxTerminal._connect_or_create(). Existing (running or
# stopped-but-resumable) VMs never pick up either kind of change on their own.
#
# Removing a VM wipes its filesystem (installed packages, any in-progress
# files not yet synced to /outputs). This is acceptable only while every
# workspace belongs to a developer who has agreed to start fresh. Before IDEA
# has non-developer users, replace this workflow with a versioned migration
# that snapshots, validates, and restores each writable workspace.
#
# `msb remove` is used directly (inside the `sandbox` container) rather
# than sandbox_service's own /destroy endpoint, since that endpoint only
# acts on sandbox_ids still present in its in-memory terminal cache (empty
# after any sandbox_service restart) - see terminal_registry.destroy_terminal.
#
# Usage:
#   ./interpreter_kernel/refresh_sandboxes.sh \
#     --allow-destructive-developer-refresh [--skip-pull] [sandbox-service-name]
#   sandbox-service-name defaults to "sandbox". --skip-pull is appropriate
#   for mount-only changes when SANDBOX_IMAGE is already cached. The explicit
#   destructive flag is mandatory; it is not a substitute for user backups.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

PULL_IMAGE=1
ALLOW_DESTRUCTIVE=0
while [[ "${1:-}" == --* ]]; do
  case "$1" in
    --skip-pull)
      PULL_IMAGE=0
      ;;
    --allow-destructive-developer-refresh)
      ALLOW_DESTRUCTIVE=1
      ;;
    *)
      echo "Error: unknown option '$1'." >&2
      exit 2
      ;;
  esac
  shift
done

if [ "${ALLOW_DESTRUCTIVE}" -ne 1 ]; then
  cat >&2 <<'EOF'
Refusing to replace sandbox workspaces without explicit acknowledgement.

This developer-stage operation permanently deletes every sandbox filesystem.
Run again with --allow-destructive-developer-refresh only after confirming all
affected developers can start fresh. Do not use this workflow once IDEA has
non-developer users; implement a snapshot/restore migration first.
EOF
  exit 2
fi

SANDBOX_SERVICE="${1:-sandbox}"
if [ "$#" -gt 1 ]; then
  echo "Error: expected at most one sandbox service name." >&2
  exit 2
fi

CONTAINER="$(docker compose ps -q "${SANDBOX_SERVICE}")"
if [ -z "${CONTAINER}" ]; then
  echo "Error: '${SANDBOX_SERVICE}' service is not running (docker compose ps -q returned nothing)." >&2
  exit 1
fi

SANDBOX_IMAGE="$(docker exec "${CONTAINER}" printenv SANDBOX_IMAGE)"
if [ "${PULL_IMAGE}" -eq 1 ]; then
  echo "==> Pulling latest '${SANDBOX_IMAGE}' into msb's image cache..."
  # msb 0.6.6 can mishandle very small OCI layers during a forced pull.
  # A normal pull still resolves the tag to the newly published digest, so
  # retry without -f before failing the refresh.
  if ! docker exec "${CONTAINER}" msb pull -f "${SANDBOX_IMAGE}"; then
    echo "==> 'msb pull -f' failed; retrying without -f..." >&2
    docker exec "${CONTAINER}" msb pull "${SANDBOX_IMAGE}"
  fi
else
  echo "==> Reusing cached '${SANDBOX_IMAGE}' (--skip-pull)."
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
FAILURES=0
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
    FAILURES=$((FAILURES + 1))
  fi
done

if [ "${FAILURES}" -ne 0 ]; then
  echo "Error: ${FAILURES} sandbox(es) failed to recreate." >&2
  exit 1
fi

echo "==> Done. All listed sandboxes now use the current image and mount configuration."
