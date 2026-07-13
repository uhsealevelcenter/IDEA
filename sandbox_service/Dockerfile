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
# runtime.
RUN curl -fsSL https://install.microsandbox.dev | sh
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
