"""
Centralized configuration for langgraph's sandbox-tooling settings.

Each constant below documents which file it was originally defined in (or
is consumed by) so this stays traceable - update values here going
forward rather than back in those modules.
"""

import os

# --- Sandbox service connection ---
# Originally defined in: tools/persistent_terminal.py
# Base URL of the sandbox_service microservice that owns all terminal/
# sandbox execution state (pexpect shells, microsandbox microVMs). See the
# `sandbox` service in docker-compose.yml.
SANDBOX_SERVICE_URL = os.getenv("SANDBOX_SERVICE_URL", "http://sandbox:8020").rstrip("/")

# Originally defined in: tools/persistent_terminal.py (as httpx.Timeout(...))
# HTTP timeouts (seconds) for every call from langgraph to sandbox_service.
# Read timeout is generous since shell commands can legitimately run for a
# long time - sandbox_service's own per-command ceiling is 1800s.
SANDBOX_HTTP_CONNECT_TIMEOUT_SECONDS = 10.0
SANDBOX_HTTP_READ_TIMEOUT_SECONDS = 1800.0
SANDBOX_HTTP_WRITE_TIMEOUT_SECONDS = 60.0
SANDBOX_HTTP_POOL_TIMEOUT_SECONDS = 10.0

# Originally defined in: tools/persistent_terminal.py (also duplicated in
# langgraph_service.py and sandbox_service/main.py - each service reads its
# own copy from the shared .env, so this constant only covers this
# process's own usage when calling sandbox_service).
# Shared secret between this service and sandbox_service (see
# docker-compose.yml / example.env). Empty/unset fails OPEN (no auth
# check) - every production .env should set this.
INTERNAL_SERVICE_TOKEN = os.getenv("INTERNAL_SERVICE_TOKEN", "")

# --- run_terminal() output truncation ---
# Originally defined in: tools/persistent_terminal.py
# Number of lines kept from the start and end of a command's output when
# it's sent back to the LLM inline; everything in between is elided with
# a "[N line(s) omitted]" marker. The full, untruncated output is always
# saved to a temp file regardless of this setting - see
# TEMP_OUTPUT_DIR below and read_output_range_tool.
OUTPUT_HEAD_TAIL_LINES = 10

# Originally defined in: tools/persistent_terminal.py
# Hard cap (in tokens - counted with tiktoken if installed, else a
# ~4-chars/token heuristic) on the inline output text sent back to the
# LLM per run_terminal_tool call, enforced after the line-truncation
# above (only kicks in for a handful of extremely long individual lines).
MAX_OUTPUT_TOKENS = 5000

# Originally defined in: tools/persistent_terminal.py (as _TEMP_OUTPUT_DIR)
# Directory inside the sandbox where each run_terminal_tool call's full,
# untruncated output is saved as a temp file - readable afterward via
# read_output_range_tool(filepath, offset, n_limit).
TEMP_OUTPUT_DIR = "/tmp/idea_command_outputs"
