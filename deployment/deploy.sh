#!/usr/bin/env bash
# Per-environment deployment command executed on the target host by GitHub Actions (.github/workflows/deploy.yml).
# Runs in the repository root ($APP_DIR) on the destination VM.
set -euo pipefail

TARGET_ENV="${1:-}"

if [ -z "${TARGET_ENV}" ]; then
  echo "Usage: $0 <dev|staging|next-dev|prod>" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

echo "==> Loading deployment configuration for environment: ${TARGET_ENV}"
if [ -f "deployment/load_env.py" ]; then
  eval "$(python3 deployment/load_env.py "${TARGET_ENV}")"
fi

if [ -f ./.env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

case "${TARGET_ENV}" in
  dev)
    echo "==> Deploying dev environment"
    docker compose up -d --remove-orphans
    ;;

  staging)
    echo "==> Deploying staging environment"
    docker compose up -d --build --remove-orphans
    curl --retry 24 --retry-delay 5 --retry-all-errors -fsS http://localhost:3001/health >/dev/null
    ./deployment/post_deploy/register_idea_pipe.sh
    ./deployment/post_deploy/configure_openwebui.py
    ./deployment/post_deploy/deploy_assistants_openwebui.py
    docker compose restart nginx
    ;;

  next-dev)
    echo "==> Deploying next-dev environment"
    docker compose up -d --build --remove-orphans
    curl --retry 24 --retry-delay 5 --retry-all-errors -fsS http://localhost:3001/health >/dev/null
    ./deployment/post_deploy/register_idea_pipe.sh
    ./deployment/post_deploy/configure_openwebui.py
    ./deployment/post_deploy/deploy_assistants_openwebui.py
    docker compose restart nginx
    ;;

  prod)
    echo "==> Deploying prod environment"
    docker compose up -d --build --remove-orphans
    curl --retry 24 --retry-delay 5 --retry-all-errors -fsS http://localhost:3001/health >/dev/null
    ./deployment/post_deploy/register_idea_pipe.sh
    ./deployment/post_deploy/configure_openwebui.py
    ./deployment/post_deploy/deploy_assistants_openwebui.py --reconcile
    docker compose restart nginx
    ;;

  *)
    echo "Error: Unknown environment '${TARGET_ENV}'. Expected one of: dev, staging, next-dev, prod" >&2
    exit 1
    ;;
esac
