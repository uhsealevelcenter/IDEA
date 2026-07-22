# Dockerfile for the Sandbox Service - owns all persistent terminal/sandbox
# state (see terminal_registry.py). Kept separate from the langgraph service
# so langgraph itself can stay stateless with respect to terminal execution.
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    bash \
    git \
    curl \
    wget \
    build-essential \
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
RUN curl -fsSL https://install.microsandbox.dev | sh || echo "⚠️  microsandbox install failed (expected on hosts without a working aarch64 build, e.g. Apple Silicon Mac) - continuing; will fall back to the local shell backend at runtime."
ENV PATH="/root/.microsandbox/bin:/root/.local/bin:${PATH}"

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy service code
COPY msb_sandbox.py .
COPY terminal_registry.py .
COPY main.py .

ENV PYTHONUNBUFFERED=1

EXPOSE 8020

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8020"]
