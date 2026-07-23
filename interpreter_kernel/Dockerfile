# Guest image for per-user microsandbox microVMs (see
# sandbox_service/msb_sandbox.py). Combines two things in one VM:
#
#   1. open-webui/open-terminal (unmodified, pulled as the base image) -
#      gives run_terminal_tool/write_file_tool's underlying transport
#      access to its maintained grep_search/glob_search tooling (see
#      msb_sandbox.py's grep_search()/glob_search()). Its own OpenWebUI
#      native-tool-calling integration and multi-user isolation modes are
#      NOT used - LangGraph's ConversationOrchestrator/TerminalAgent stays
#      the only thing deciding what runs, and microsandbox (one VM per
#      user) stays the only isolation boundary. See the architecture doc
#      this was built from for the reasoning.
#   2. This repo's own persistent Python kernel daemon (daemon.py), reusing
#      Open Interpreter's *execution engine only* (interpreter/core/
#      computer/terminal/languages/python.py - never interpreter/core/
#      core.py, Open Interpreter's own agent loop) - gives run_python_tool
#      real stateful-kernel semantics that Open Terminal's own /execute
#      (a fresh background process per call - see its main.py) does not
#      provide.
#
# Must be built from the repo root so the COPY below can reach
# interpreter/core/computer:
#   docker build -f interpreter_kernel/Dockerfile -t idea/oi-kernel:slim .
#
# TODO: pin to a specific released tag/digest instead of :slim once you've
# picked one to track, same reasoning as the openwebui image in
# docker-compose.yml - avoids an unreviewed upstream change (e.g. to
# verify_api_key or the /execute API) reaching every user's VM on a
# routine `docker build --pull`.
FROM ghcr.io/open-webui/open-terminal:slim

# The base image's runtime stage removes pip to save space (see its own
# Dockerfile.slim) - ensurepip restores it from Python's bundled wheels
# (a stdlib mechanism, independent of the earlier `pip uninstall`) so we
# can install the kernel daemon's own dependencies on top. If a future
# open-terminal release strips ensurepip's bundled wheels too, this step
# will need to move to building from open-terminal's own Dockerfile.slim
# source instead (add these packages to its builder stage's `pip install`
# line, per that file's own documented customization point) rather than
# layering on top of its published runtime image - see this directory's
# README for the fallback plan.
RUN python3 -m ensurepip --upgrade \
    && python3 -m pip install --no-cache-dir \
        ipykernel==6.29.5 \
        jupyter_client==8.6.3 \
        litellm==1.83.14 \
        matplotlib==3.9.2 \
        matplotlib-inline==0.1.7 \
    && python3 -m ipykernel install --name python3 --sys-prefix

WORKDIR /opt/oi_kernel

# Vendored, unmodified execution engine - see daemon.py's module docstring
# for exactly which submodules are actually imported (not the whole tree).
RUN mkdir -p vendor/interpreter/core
COPY interpreter/core/computer vendor/interpreter/core/computer
RUN touch vendor/interpreter/__init__.py vendor/interpreter/core/__init__.py

COPY interpreter_kernel/daemon.py interpreter_kernel/client.py ./
COPY interpreter_kernel/entrypoint.sh /opt/oi_kernel/entrypoint.sh
RUN chmod +x /opt/oi_kernel/entrypoint.sh

# Optional dependency module (mirrored from the repo root's pyproject.toml)
# - see interpreter_kernel/modules/. Lets run_python_tool sessions `import`
# those packages directly instead of installing them on demand mid-session
# via run_terminal_tool. Toggle/select via ACTIVE_MODULE in
# interpreter_kernel/config.env - "original" (the full pyproject.toml list)
# is enabled by default.
# Note: this is a much larger/slower layer than the minimal baseline above
# (adds cartopy/rasterio/selenium/etc., not all of which the kernel daemon
# itself ever imports - see daemon.py's module docstring).
#
# The "original" module needs GDAL/GEOS/PROJ headers to build rasterio/
# cartopy/geopandas from source (this base image has none of them
# preinstalled) - same system packages as the root Dockerfile's builder
# stage.
RUN apt-get update && apt-get install -y \
    build-essential \
    gdal-bin \
    libgdal-dev \
    libproj-dev \
    libgeos-dev \
    libspatialite-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*
ENV GDAL_CONFIG=/usr/bin/gdal-config
ENV CPLUS_INCLUDE_PATH=/usr/include/gdal
ENV C_INCLUDE_PATH=/usr/include/gdal

COPY interpreter_kernel/config.env config.env
COPY interpreter_kernel/modules/ modules/
RUN . ./config.env \
    && if [ "$ACTIVE_MODULE" != "none" ] && [ -f "modules/$ACTIVE_MODULE/requirements.txt" ]; then \
         python3 -m pip install --no-cache-dir wheel setuptools cython numpy \
         && python3 -m pip install --no-cache-dir rasterio==1.4.3 \
         && python3 -m pip install --no-cache-dir -r "modules/$ACTIVE_MODULE/requirements.txt"; \
       else \
         echo "Skipping dependency module (ACTIVE_MODULE=$ACTIVE_MODULE in config.env)"; \
       fi

ENV PYTHONUNBUFFERED=1
ENV OI_KERNEL_PORT=8721

# 8000 (Open Terminal) and 8721 (kernel daemon) both stay loopback-only -
# EXPOSE here is documentation, not a host port publish (microsandbox VMs
# aren't reached via docker port-mapping at all - see sandbox_service).
EXPOSE 8000 8721

# Overrides open-terminal's own ENTRYPOINT/CMD (tini + entrypoint-slim.sh)
# to also start the kernel daemon - see entrypoint.sh, which itself still
# invokes entrypoint-slim.sh for Open Terminal's own startup.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/opt/oi_kernel/entrypoint.sh"]
