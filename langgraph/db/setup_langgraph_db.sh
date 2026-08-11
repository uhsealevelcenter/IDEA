#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

if [[ ! -f .env ]]; then
  echo "Error: .env not found in ${REPO_ROOT} (copy example.env and fill it in first)." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

: "${POSTGRES_USER:?POSTGRES_USER not set in .env}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD not set in .env}"
: "${POSTGRES_DB:?POSTGRES_DB not set in .env}"
: "${LANGGRAPH_DB_PASSWORD:?LANGGRAPH_DB_PASSWORD not set in .env - generate one with: openssl rand -hex 20}"

DB_SERVICE="${1:-db}"

echo "==> Ensuring '${DB_SERVICE}' is up..."
docker compose up -d "${DB_SERVICE}"

echo "==> Waiting for '${DB_SERVICE}' to accept connections..."
tries=0
until docker compose exec -T "${DB_SERVICE}" pg_isready -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" >/dev/null 2>&1; do
  tries=$((tries + 1))
  if [[ "${tries}" -ge 60 ]]; then
    echo "Error: '${DB_SERVICE}' did not become ready after 60s." >&2
    exit 1
  fi
  sleep 1
done

echo "==> Applying LangGraph role and schema..."
docker compose exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" "${DB_SERVICE}" \
  psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -v ON_ERROR_STOP=1 \
  -v dbname="${POSTGRES_DB}" \
  -v langgraph_password="${LANGGRAPH_DB_PASSWORD}" \
  -f /dev/stdin < langgraph/db/init_langgraph_db.sql

echo "==> Building LangGraph and creating checkpoint tables..."
docker compose build langgraph
docker compose run --rm --no-deps langgraph \
  python -c 'from idea_graph.checkpoints import setup_checkpointer; setup_checkpointer()'

echo "==> LangGraph checkpoint role, schema, and tables are ready."
