#!/bin/sh
# Entrypoint for the sandbox_service container - starts the FastAPI app
# (main.py), but first (optionally) configures microsandbox's registry
# credentials so it can pull a *private* SANDBOX_IMAGE (see
# ../interpreter_kernel/README.md's "Private registry auth" section).
#
# `msb registry login` (the CLI-documented way to store registry creds -
# https://docs.microsandbox.dev/cli/image-commands) persists secrets in the
# OS credential store (Keychain/Credential Manager/Secret Service), none of
# which exist in this headless python:3.11-slim container. microsandbox's
# documented headless alternative is a `password_env` entry in
# ~/.microsandbox/config.json, which is what this generates instead.
set -eu

if [ -n "${GHCR_PAT:-}" ]; then
  mkdir -p /root/.microsandbox
  # /root/.microsandbox is the idea_microsandbox_data volume (see
  # docker-compose.yml) - this file is small and safe to (re)write on every
  # container start; it doesn't touch the rest of that volume (msb's own
  # sandbox/image state).
  cat > /root/.microsandbox/config.json <<EOF
{
  "registries": {
    "hosts": {
      "ghcr.io": {
        "auth": {
          "username": "${GHCR_USERNAME:-uhsealevelcenter}",
          "password_env": "GHCR_PAT"
        }
      }
    }
  }
}
EOF
  echo "Configured microsandbox registry auth for ghcr.io (user: ${GHCR_USERNAME:-uhsealevelcenter})"
fi

exec uvicorn main:app --host 0.0.0.0 --port 8020
