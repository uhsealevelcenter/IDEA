# Dockerfile for the Sandbox Service - owns all persistent terminal/sandbox
# state (see terminal_registry.py). Kept separate from the langgraph service
# so langgraph itself can stay stateless with respect to terminal execution.
FROM rust:1.94-bookworm AS native-sdk
ARG MICROSANDBOX_COMMIT=60d4dc8a436fb9365491567ec21d073e924e3c6d
WORKDIR /src
RUN apt-get update && apt-get install -y libcap-ng-dev && rm -rf /var/lib/apt/lists/*
RUN git init . && git remote add origin https://github.com/microsandbox/microsandbox.git \
    && git fetch --depth 1 origin ${MICROSANDBOX_COMMIT} && git checkout --detach FETCH_HEAD
COPY native/pid-identity.patch /tmp/pid-identity.patch
RUN git apply --check /tmp/pid-identity.patch && git apply /tmp/pid-identity.patch
# Build the original 0.7.2 SDK with only native process-identity validation.
# Catalog schema, guest protocol, and installed msb runtime remain unchanged.
RUN CARGO_BUILD_JOBS=4 cargo build --locked -p microsandbox-py --profile ci --features pyo3/extension-module \
    && strip /src/target/ci/lib_microsandbox.so \
    && cp /src/target/ci/lib_microsandbox.so /idea-microsandbox.so
RUN tail -n +$(grep -n '^// PID numbers survive' sdk/rust/lib/backend/local/sandbox/mod.rs | cut -d: -f1) \
      sdk/rust/lib/backend/local/sandbox/mod.rs > /tmp/identity-tests.rs \
    && rustc --edition 2024 --test /tmp/identity-tests.rs -o /tmp/identity-tests \
    && /tmp/identity-tests

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    bash \
    git \
    curl \
    wget \
    build-essential \
    libcap-ng0 \
    && rm -rf /var/lib/apt/lists/*

# Install the microsandbox (msb) runtime used for per-user isolated microVM
# execution (msb_sandbox.py). Requires /dev/kvm to be passed through to this
# container (see docker-compose.yml's `sandbox.devices` / KVM_DEVICE_PATH) -
# falls back to a plain local shell automatically if KVM isn't available at
# runtime (see terminal_registry.py's _use_microsandbox/microsandbox_available).
#
# The installer's linux-aarch64 tarball is currently missing
# libkrunfw.so.5.6.0 upstream (fails with "install: cannot stat
# 'libkrunfw.so.5.6.0'"), which breaks this build step entirely on Apple
# Silicon Macs (Docker Desktop builds natively as linux/aarch64) even
# though microsandbox couldn't be used there anyway (macOS has no /dev/kvm
# to pass through). Tolerate that failure here (`|| echo ...`) so the
# image still builds and runs on Mac dev machines via the local-shell
# fallback - real KVM-capable hosts (e.g. a Jetstream2 VM) still get a
# working microsandbox install.
ARG MSB_VERSION=v0.7.2
RUN curl -fsSL https://install.microsandbox.dev | sed "s|VERSION=\$(curl.*)|VERSION=\${MSB_VERSION}|" | sh || echo "⚠️  microsandbox install failed (expected on hosts without a working aarch64 build, e.g. Apple Silicon Mac) - continuing; will fall back to the local shell backend at runtime."
RUN if [ -d /root/.microsandbox ]; then cp -a /root/.microsandbox /opt/microsandbox-runtime; fi
ENV PATH="/root/.microsandbox/bin:/root/.local/bin:${PATH}"

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=native-sdk /idea-microsandbox.so /usr/local/lib/python3.11/site-packages/microsandbox/_microsandbox.abi3.so
RUN python -c 'from microsandbox import _microsandbox; assert _microsandbox.idea_pid_identity_guard()'

# Copy service code
COPY msb_sandbox.py .
COPY lifecycle_recovery.py .
COPY lifecycle_smoke.py .
COPY offline_catalog_check.py .
COPY terminal_registry.py .
COPY main.py .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENV PYTHONUNBUFFERED=1

EXPOSE 8020

# See entrypoint.sh - configures microsandbox's registry auth for a private
# SANDBOX_IMAGE (from GHCR_USERNAME/GHCR_PAT) before starting the app.
CMD ["./entrypoint.sh"]
