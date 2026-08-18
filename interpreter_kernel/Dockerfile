# syntax=docker/dockerfile:1.7

# Guest image for per-user microsandbox microVMs. Open Terminal remains the
# transport service, while IDEA's Python/Codex environment is isolated in its
# own virtual environment so research dependencies cannot downgrade the base
# service's FastAPI/uvicorn stack.

ARG OPEN_TERMINAL_IMAGE=ghcr.io/open-webui/open-terminal@sha256:b338796cb8934f5cfeb4fd5ded700331781a90cec5b031ffaf4d41cf382a691b
ARG NODE_IMAGE=docker.io/library/node@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436

FROM ${NODE_IMAGE} AS browser-builder
ARG PLAYWRIGHT_VERSION=1.60.0
ARG CODEX_CLI_VERSION=0.147.0
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN --mount=type=cache,target=/root/.npm \
    npm install --global \
        "@openai/codex@${CODEX_CLI_VERSION}" \
        "@playwright/test@${PLAYWRIGHT_VERSION}" \
    && playwright install chromium \
    && npm cache clean --force

FROM ${OPEN_TERMINAL_IMAGE} AS python-builder
USER root
ARG TARGETARCH

# Compilers and geospatial headers exist only in this stage. Most selected
# projects publish wheels, but this preserves support for TADC and packages
# that occasionally need a source build on arm64.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libgdal-dev \
        libgeos-dev \
        libproj-dev \
        libspatialite-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m ensurepip --upgrade \
    && python3 -m venv --copies /opt/idea-venv \
    && python3 -m venv --copies /opt/guarddog-venv

COPY config.env /tmp/config.env
COPY modules/ /tmp/modules/
# numcodecs 0.15.1 has no CPython 3.12 Linux ARM64 wheel. Its source build
# can see the QEMU host's x86 flags and incorrectly add SSE2/AVX2 sources and
# compiler options. Disable those paths only for ARM64; CFLAGS must also be
# present so setup.py does not emit unsupported -mno-* flags.
RUN --mount=type=cache,target=/root/.cache/pip \
    . /tmp/config.env \
    && test -f "/tmp/modules/${ACTIVE_MODULE}/requirements.lock" \
    && if [ "${TARGETARCH}" = "arm64" ]; then \
        export CFLAGS="-O2" \
            DISABLE_NUMCODECS_SSE2=1 \
            DISABLE_NUMCODECS_AVX2=1; \
    fi \
    && /opt/idea-venv/bin/python -m pip install --upgrade \
        pip==26.1.2 setuptools==84.0.0 wheel==0.47.0 \
    && /opt/idea-venv/bin/python -m pip install -r "/tmp/modules/${ACTIVE_MODULE}/requirements.lock" \
    && /opt/idea-venv/bin/python -m pip check \
    && /opt/idea-venv/bin/python -m ipykernel install --name python3 --sys-prefix \
    && /opt/guarddog-venv/bin/python -m pip install --upgrade pip==26.1.2 \
    && /opt/guarddog-venv/bin/python -m pip install guarddog==3.1.0 \
    && /opt/guarddog-venv/bin/python -m pip check

FROM ${OPEN_TERMINAL_IMAGE}
USER root

ENV VIRTUAL_ENV=/opt/idea-venv
ENV PATH=/opt/idea-venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ENV NODE_PATH=/usr/local/lib/node_modules
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
ENV GDAL_DATA=/usr/share/gdal
ENV PROJ_DATA=/usr/share/proj

# Legacy IDEA system capabilities plus small additions demonstrated useful in
# recent CIndRA work. Build headers stay in python-builder; this stage receives
# only runtime utilities and libraries. Keep this expensive, stable layer ahead
# of the application environments so normal lock/CLI updates can reuse it.
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        biber \
        chktex \
        file \
        fonts-liberation \
        fonts-noto-color-emoji \
        gdal-bin \
        ghostscript \
        latexmk \
        libmagic1 \
        poppler-utils \
        proj-bin \
        tesseract-ocr \
        texlive-bibtex-extra \
        texlive-fonts-recommended \
        texlive-latex-base \
        texlive-latex-extra \
        texlive-latex-recommended \
        texlive-science \
        unzip \
        zip \
    && rm -rf /var/lib/apt/lists/* /tmp/*

# Open Terminal currently supplies curl and Git. Install wget explicitly so
# the full legacy command-line acquisition surface does not depend on an
# incidental base-image package.
RUN apt-get update && apt-get install -y --no-install-recommends wget \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-builder /opt/idea-venv /opt/idea-venv
COPY --from=python-builder /opt/guarddog-venv /opt/guarddog-venv
COPY --from=browser-builder /usr/local/ /usr/local/
COPY --from=browser-builder /opt/ms-playwright /opt/ms-playwright

# Playwright supplies the authoritative dependency list for its pinned browser.
RUN playwright install-deps chromium \
    && rm -rf /var/lib/apt/lists/* /tmp/* \
    && ln -s /opt/guarddog-venv/bin/guarddog /usr/local/bin/guarddog \
    && ln -s "$(find /opt/ms-playwright -type f -name chrome | head -n 1)" /usr/local/bin/chromium

WORKDIR /opt/oi_kernel

# Vendored, unmodified Open Interpreter execution engine. Only the leaf Python
# and shell runners are imported by daemon.py.
RUN mkdir -p vendor/interpreter/core
COPY interpreter/core/computer vendor/interpreter/core/computer
RUN touch vendor/interpreter/__init__.py vendor/interpreter/core/__init__.py

COPY daemon.py client.py codex_runner.py smoke_test.py ./
COPY entrypoint.sh /opt/oi_kernel/entrypoint.sh
RUN chmod +x /opt/oi_kernel/entrypoint.sh

ENV PYTHONUNBUFFERED=1
ENV OI_KERNEL_PORT=8721

EXPOSE 8000 8721
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/opt/oi_kernel/entrypoint.sh"]
