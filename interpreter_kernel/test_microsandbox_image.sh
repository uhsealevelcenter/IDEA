#!/bin/bash
# Load a Docker-local image into microsandbox and boot it through IDEA's SDK.
# This script never pushes and never replaces an existing sandbox.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMAGE_TAG="${1:-idea/oi-kernel:research-local}"
SANDBOX_SERVICE="${2:-sandbox}"

if [ -n "${IDEA_DOCKER_CONFIG:-}" ]; then
  export DOCKER_CONFIG="${IDEA_DOCKER_CONFIG}"
fi

cd "${REPO_ROOT}"
docker image inspect "${IMAGE_TAG}" >/dev/null
docker compose ps -q "${SANDBOX_SERVICE}" | grep -q . || {
  echo "The '${SANDBOX_SERVICE}' Compose service is not running." >&2
  exit 1
}

if [ "${SKIP_LOAD:-0}" = "1" ]; then
  echo "==> Reusing ${IMAGE_TAG} from microsandbox's local cache"
else
  echo "==> Loading ${IMAGE_TAG} into microsandbox's local cache (no push)"
  docker save "${IMAGE_TAG}" \
    | docker compose exec -T "${SANDBOX_SERVICE}" \
        msb load --tag "${IMAGE_TAG}"
fi

echo "==> Booting one disposable microVM through the production SDK path"
docker compose exec -T "${SANDBOX_SERVICE}" python /app/test_local_image.py \
  --image "${IMAGE_TAG}" \
  --name "idea-image-smoke-local"
