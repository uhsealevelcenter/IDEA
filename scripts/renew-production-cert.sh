#!/usr/bin/env bash
# Run daily as the deployment user, which has access to Docker.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# Use the same webroot served by nginx on port 80.
docker run --rm \
  -v "$PWD/certbot/conf:/etc/letsencrypt" \
  -v "$PWD/certbot/www:/var/www/certbot" \
  certbot/certbot:latest renew --quiet --non-interactive
# Reload after successful renewal checks so nginx picks up renewed certificates.
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T nginx nginx -t
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T nginx nginx -s reload
