# Upstream LiteLLM images (same registry/tags for both names):
#   - litellm             — general proxy image
#   - litellm-database    — proxy built for Postgres-backed mode (virtual keys, spend,
#                           Prisma/UI tooling pre-baked per upstream). This is NOT a Postgres
#                           server itself - LITELLM_DATABASE_URL (assembled in
#                           docker-compose.yml's `litellm` service) still points at the
#                           existing `db` service's dedicated `litellm` role/schema, see
#                           litellm/init_litellm_db.sql.
#
# You run one container from one base image - not "litellm + litellm-database" as two services.
#
# Local build:
#   docker build -t idea-litellm:local .
#
# https://docs.litellm.ai/docs/proxy/deploy — "Deploy with Database" recommends the
# litellm-database image, which is what's used here.
ARG LITELLM_IMAGE=litellm-database
ARG LITELLM_VERSION=v1.92.0

FROM ghcr.io/berriai/${LITELLM_IMAGE}:${LITELLM_VERSION}

# ARG is not inherited past FROM; re-declare for LABEL expansion.
ARG LITELLM_IMAGE
ARG LITELLM_VERSION

# Set the working directory to /app
WORKDIR /app

# Production environment variables
ENV LITELLM_MODE="PRODUCTION"
ENV LITELLM_LOG=INFO

LABEL litellm.upstream-image="ghcr.io/berriai/${LITELLM_IMAGE}"
LABEL litellm.upstream-tag="${LITELLM_VERSION}"

# Copy the configuration file into the container at /app - also
# bind-mounted read-only over this in docker-compose.yml so it can be
# edited without a rebuild.
COPY litellm_config.yaml .

# Expose the necessary port
EXPOSE 8080

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health/liveliness', timeout=5).read()"

# Production CMD with auto-scaled workers based on CPU count
CMD ["--port", "8080", "--config", "litellm_config.yaml"]
