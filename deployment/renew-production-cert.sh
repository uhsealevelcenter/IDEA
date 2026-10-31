#!/usr/bin/env bash
# Run daily as the deployment user, which has access to Docker.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# Renew all lineages (IDEA and Langfuse) using their saved webroot settings.
# Accept options such as --dry-run for staging validation; do not reload on failure.
docker run --rm \
  -v "$PWD/certbot/conf:/etc/letsencrypt" \
  -v "$PWD/certbot/www:/var/www/certbot" \
  certbot/certbot:latest renew --quiet --non-interactive "$@"
# Reload after successful renewal checks so nginx picks up renewed certificates.
docker compose -p idea exec -T nginx nginx -t
docker compose -p idea exec -T nginx nginx -s reload
