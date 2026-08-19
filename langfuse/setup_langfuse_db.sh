#!/bin/bash
# Idempotently creates/repairs Langfuse's dedicated Postgres role and schema
# on the existing `db` service (see docker-compose.yml and
# init_langfuse_db.sql in this folder). Mirrors litellm/setup_litellm_db.sh.
# Safe to re-run on every deploy.
#
# Requires the `db` service to already be reachable (starts it via
# `docker compose up -d` below if it isn't running yet) and .env to have
# POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB / LANGFUSE_DB_PASSWORD
# set (see example.env).
#
# Usage: ./langfuse/setup_langfuse_db.sh [db-service-name]
#   (db-service-name defaults to "db" - the base docker-compose.yml service)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

DB_SERVICE="${1:-db}"

if [ ! -f .env ]; then
  echo "Error: .env not found in ${REPO_ROOT} (copy example.env and fill it in first)." >&2
  exit 1
fi

set -a
source .env
set +a

: "${POSTGRES_USER:?POSTGRES_USER not set in .env}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD not set in .env}"
: "${POSTGRES_DB:?POSTGRES_DB not set in .env}"
: "${LANGFUSE_DB_PASSWORD:?LANGFUSE_DB_PASSWORD not set in .env - generate one with: openssl rand -hex 20}"

echo "==> Ensuring '${DB_SERVICE}' is up..."
docker compose up -d "${DB_SERVICE}"

echo "==> Waiting for '${DB_SERVICE}' to accept connections..."
tries=0
until docker compose exec -T "${DB_SERVICE}" pg_isready -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" >/dev/null 2>&1; do
  tries=$((tries + 1))
  if [ "${tries}" -ge 60 ]; then
    echo "Error: '${DB_SERVICE}' did not become ready after 60s." >&2
    exit 1
  fi
  sleep 1
done

echo "==> Applying langfuse/init_langfuse_db.sql to database '${POSTGRES_DB}'..."
docker compose exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" "${DB_SERVICE}" \
  psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -v ON_ERROR_STOP=1 \
  -v langfuse_password="${LANGFUSE_DB_PASSWORD}" \
  -v dbname="${POSTGRES_DB}" \
  < langfuse/init_langfuse_db.sql

echo "==> langfuse role + schema ready on '${DB_SERVICE}'."
