FROM python:3.11 AS builder

# TODO: file upload virus scan
# Install build dependencies including GDAL
RUN apt-get update && apt-get install -y \
    build-essential \
    gdal-bin \
    libgdal-dev \
    libproj-dev \
    libgeos-dev \
    libspatialite-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Set GDAL environment variables
ENV GDAL_CONFIG /usr/bin/gdal-config
ENV CPLUS_INCLUDE_PATH /usr/include/gdal
ENV C_INCLUDE_PATH /usr/include/gdal

# Set a specific GDAL version to avoid version detection issues
ENV GDAL_VERSION 3.6.2

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copy only requirements first
COPY requirements.txt .

# Install dependencies in a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# RUN pip install --no-cache-dir --upgrade pip && \
#     pip install --no-cache-dir --retries 10 --timeout 100 -r requirements.txt
# Download dependencies as a separate step to take advantage of Docker's caching.
# Leverage a cache mount to /root/.cache/pip to speed up subsequent builds.
# Leverage a bind mount to requirements.txt to avoid having to copy them into
# into this layer.
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=bind,source=requirements.txt,target=requirements.txt \
    pip install --no-cache-dir wheel setuptools cython numpy && \
    pip install --no-cache-dir rasterio==1.4.3 && \
    python -m pip install -r requirements.txt

# Final stage
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install runtime dependencies including GDAL
RUN apt-get update && apt-get install -y \
    wget \
    libexpat1-dev \
    curl \
    gdal-bin \
    libgdal-dev \
    libproj-dev \
    libgeos-dev \
    libspatialite-dev \
    && rm -rf /var/lib/apt/lists/*

# Set GDAL environment variables for runtime
ENV GDAL_CONFIG /usr/bin/gdal-config
ENV GDAL_DATA /usr/share/gdal

# ## Install runtime dependencies (For puppeteer pdf generation, under development)
# RUN apt-get update && apt-get install -y \
#     wget \
#     curl \
#     libnss3 \
#     libx11-xcb1 \
#     libxcomposite1 \
#     libxcursor1 \
#     libxdamage1 \
#     libxi6 \
#     libxtst6 \
#     libglib2.0-0 \
#     libxrandr2 \
#     libasound2 \
#     libpangocairo-1.0-0 \
#     fonts-liberation \
#     libatk-bridge2.0-0 \
#     libatk1.0-0 \
#     libcups2 \
#     libdrm2 \
#     libdbus-1-3 \
#     libxss1 \
#     libgtk-3-0 \
#     ca-certificates \
#     gnupg \
#     lsb-release \
#     && rm -rf /var/lib/apt/lists/*
## 

## Install runtime dependencies (For puppeteer pdf generation, under development)
# # Install Node.js
# RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
#     apt-get install -y nodejs
##

# create directories for data
# # Initialize ClamAV
# RUN freshclam
WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application files
COPY . .

## Install runtime dependencies (For puppeteer pdf generation, under development)
# # npm install (after copying the app files)
# #RUN npm init -y && npm install puppeteer get-stdin
# # Install Chromium manually
# RUN apt-get update && apt-get install -y chromium chromium-driver
# RUN npm install puppeteer
##

# Create required directories
# RUN echo '#!/bin/bash\n\
# mkdir -p /app/data/benchmarks\n\
# mkdir -p /app/data/metadata\n\
# mkdir -p /app/data/altimetry\n\
# mkdir -p /app/data/papers\n\
# mkdir -p /app/static\n\
# chmod 755 /app/data\n\
# chmod +x /app/scripts/fetch_data.sh\n\
# exec "$@"' > /entrypoint.sh && \
# chmod +x /entrypoint.sh

# Setup paper-qa settings
# RUN pqa -s default_setting --llm gpt-4o-mini --summary-llm gpt-4o-mini save

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Setup paper-qa settings
# RUN pqa -s default_setting --llm gpt-4o-mini --summary-llm gpt-4o-mini save

EXPOSE 8001
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app:app", "--reload", "--port", "8001", "--host", "0.0.0.0"]