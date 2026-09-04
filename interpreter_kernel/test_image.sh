#!/bin/bash
# Build and validate the microsandbox image locally. This script never pushes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMAGE_TAG="${1:-idea/oi-kernel:local}"
MAX_IMAGE_BYTES="${MAX_IMAGE_BYTES:-6442450944}"

# Useful on development hosts whose normal Docker config references a desktop
# credential helper that is unavailable in the current shell/VM.
if [ -n "${IDEA_DOCKER_CONFIG:-}" ]; then
  export DOCKER_CONFIG="${IDEA_DOCKER_CONFIG}"
fi

cd "${REPO_ROOT}"

if [ "${SKIP_BUILD:-0}" = "1" ]; then
  echo "==> Reusing prebuilt local image ${IMAGE_TAG} (no push)"
  docker image inspect "${IMAGE_TAG}" >/dev/null
else
  echo "==> Building ${IMAGE_TAG} for the local architecture (no push)"
  docker build \
    --file interpreter_kernel/Dockerfile \
    --tag "${IMAGE_TAG}" \
    interpreter_kernel
fi

echo "==> Checking the isolated Python environments"
docker run --rm --entrypoint /opt/idea-venv/bin/python "${IMAGE_TAG}" -m pip check
docker run --rm --entrypoint /opt/guarddog-venv/bin/python "${IMAGE_TAG}" -m pip check

echo "==> Running Python/science/document smoke tests"
docker run --rm \
  --env MPLCONFIGDIR=/tmp/matplotlib \
  --entrypoint /opt/idea-venv/bin/python \
  "${IMAGE_TAG}" /opt/oi_kernel/smoke_test.py

echo "==> Testing legacy system tools, Codex, LaTeX, and Chromium"
docker run --rm --entrypoint bash "${IMAGE_TAG}" -lc '
  set -euo pipefail
  codex --version
  guarddog --help >/dev/null
  curl --version >/dev/null
  git --version
  wget --version >/dev/null
  node --version
  playwright --version
  pdflatex --version >/dev/null
  latexmk --version >/dev/null
  biber --version >/dev/null
  gs --version >/dev/null
  pdfinfo -v 2>&1 | head -n 1
  chktex --version >/dev/null
  tesseract --version >/dev/null
  gdalinfo --version
  printf "%s" "\\documentclass{article}\\begin{document}IDEA\\end{document}" >/tmp/idea.tex
  latexmk -pdf -interaction=nonstopmode -halt-on-error -outdir=/tmp /tmp/idea.tex >/dev/null
  test -s /tmp/idea.pdf
  printf "%s" "<html><body>IDEA browser smoke test</body></html>" >/tmp/idea.html
  playwright screenshot file:///tmp/idea.html /tmp/idea.png >/dev/null
  test -s /tmp/idea.png
'

echo "==> Starting the combined guest services"
CONTAINER_ID="$(docker run --detach "${IMAGE_TAG}")"
cleanup() {
  docker rm --force "${CONTAINER_ID}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

for attempt in $(seq 1 30); do
  if docker exec "${CONTAINER_ID}" curl -fsS http://127.0.0.1:8000/health >/dev/null \
      && docker exec "${CONTAINER_ID}" curl -fsS http://127.0.0.1:8721/health >/dev/null; then
    break
  fi
  if [ "${attempt}" -eq 30 ]; then
    docker logs "${CONTAINER_ID}"
    echo "Guest services did not become healthy" >&2
    exit 1
  fi
  sleep 1
done

docker exec "${CONTAINER_ID}" bash -lc '
  printf "value = 6 * 7\nvalue" >/tmp/kernel_smoke.py
  /opt/idea-venv/bin/python /opt/oi_kernel/client.py \
    --run-file /tmp/kernel_smoke.py --kernel-id image_smoke | grep -q '"42"'
'

STORED_IMAGE_BYTES="$(docker image inspect "${IMAGE_TAG}" --format "{{.Size}}")"
ROOTFS_BYTES="$(docker run --rm --entrypoint du "${IMAGE_TAG}" -sx -B1 / | awk '{print $1}')"
echo "==> Image storage size: ${STORED_IMAGE_BYTES} bytes"
echo "==> Unpacked root filesystem: ${ROOTFS_BYTES} bytes (limit: ${MAX_IMAGE_BYTES})"
if [ "${ROOTFS_BYTES}" -gt "${MAX_IMAGE_BYTES}" ]; then
  echo "Unpacked image exceeds MAX_IMAGE_BYTES" >&2
  exit 1
fi

echo "==> Local microsandbox image validation passed: ${IMAGE_TAG}"
