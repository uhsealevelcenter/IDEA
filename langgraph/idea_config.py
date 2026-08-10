"""
Centralized configuration for IDEA's LangGraph and sandbox tooling.

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

# Agent orchestration and durable memory. ``manual`` remains available as a
# rollback path while production checkpoints are introduced.
IDEA_AGENT_RUNTIME = os.getenv("IDEA_AGENT_RUNTIME", "manual").strip().lower()
# One switch controls the primary IDEA agent and its lightweight model-backed
# helper tools. Keep both Terra and Sol aliases configured in LiteLLM so a
# rollback only requires changing this environment variable and restarting
# the LangGraph service.
IDEA_AGENT_MODEL = (
    os.getenv("IDEA_AGENT_MODEL", "gpt-5.6-terra").strip()
    or "gpt-5.6-terra"
)


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in {
        "1", "true", "yes", "on"
    }


# Codex is a delegated coding runtime, not IDEA's primary conversation model.
# Dedicated values remain supported, but the developer deployment currently
# reuses the app's external OpenAI-compatible endpoint and credential.
# TODO(low priority): route Codex through a microVM-reachable LiteLLM endpoint
# and use a separate, revocable, model-restricted, low-budget virtual key.
IDEA_CODEX_ENABLED = _env_bool("IDEA_CODEX_ENABLED", True)
IDEA_CODEX_MODEL = os.getenv("IDEA_CODEX_MODEL", IDEA_AGENT_MODEL).strip() or IDEA_AGENT_MODEL
IDEA_CODEX_BASE_URL = (
    os.getenv("IDEA_CODEX_BASE_URL", "").strip()
    or os.getenv("OPENAI_BASE_URL", "").strip()
).rstrip("/")
IDEA_CODEX_API_KEY = (
    os.getenv("IDEA_CODEX_API_KEY", "").strip()
    or os.getenv("OPENAI_API_KEY", "").strip()
)
IDEA_CODEX_MAX_EVENTS = max(1, min(int(os.getenv("IDEA_CODEX_MAX_EVENTS", "100")), 500))
IDEA_KERNEL_SCOPE = os.getenv("IDEA_KERNEL_SCOPE", "chat_assistant").strip().lower()
LANGGRAPH_DATABASE_URL = os.getenv("LANGGRAPH_DATABASE_URL", "").strip()
LANGGRAPH_AES_KEY = os.getenv("LANGGRAPH_AES_KEY", "").strip()
IDEA_MAX_STATE_BYTES = int(os.getenv("IDEA_MAX_STATE_BYTES", "524288"))
IDEA_MAX_RECENT_ACTIONS = int(os.getenv("IDEA_MAX_RECENT_ACTIONS", "50"))
IDEA_MAX_RECENT_EXECUTIONS = int(os.getenv("IDEA_MAX_RECENT_EXECUTIONS", "20"))
IDEA_MAX_TOOL_RESULT_EXCERPT_BYTES = int(
    os.getenv("IDEA_MAX_TOOL_RESULT_EXCERPT_BYTES", "12000")
)
IDEA_MAX_CODE_INLINE_BYTES = int(os.getenv("IDEA_MAX_CODE_INLINE_BYTES", "100000"))
IDEA_MAX_EXECUTION_MEMORY_BYTES = int(
    os.getenv("IDEA_MAX_EXECUTION_MEMORY_BYTES", "48000")
)
IDEA_MAX_MODEL_TOOL_OBSERVATION_BYTES = int(
    os.getenv("IDEA_MAX_MODEL_TOOL_OBSERVATION_BYTES", "6000")
)
IDEA_MAX_MODEL_HISTORY_MESSAGE_BYTES = int(
    os.getenv("IDEA_MAX_MODEL_HISTORY_MESSAGE_BYTES", "16000")
)
IDEA_MAX_IDENTICAL_TOOL_CALLS = int(
    os.getenv("IDEA_MAX_IDENTICAL_TOOL_CALLS", "3")
)
IDEA_CHECKPOINT_MAP_TTL_SECONDS = int(
    os.getenv("IDEA_CHECKPOINT_MAP_TTL_SECONDS", "31536000")
)

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
# a "[N line(s) omitted]" marker. When this truncates the inline result,
# the full output is saved to a temp file - see TEMP_OUTPUT_DIR below and
# read_output_range_tool.
OUTPUT_HEAD_TAIL_LINES = 10

# Originally defined in: tools/persistent_terminal.py
# Hard cap (in tokens - counted with tiktoken if installed, else a
# ~4-chars/token heuristic) on the inline output text sent back to the
# LLM per run_terminal_tool call, enforced after the line-truncation
# above (only kicks in for a handful of extremely long individual lines).
MAX_OUTPUT_TOKENS = 5000

# Originally defined in: tools/persistent_terminal.py (as _TEMP_OUTPUT_DIR)
# Directory inside the sandbox where truncated run_terminal_tool calls'
# full output is saved as a temp file - readable afterward via
# read_output_range_tool(filepath, offset, n_limit).
TEMP_OUTPUT_DIR = "/tmp/idea_command_outputs"

# --- LiteLLM proxy ---
# Originally defined in: agents/terminal_agent.py
# All LLM calls are routed through the LiteLLM proxy (see litellm/ and
# docker-compose.yml's `litellm` service) instead of hitting the Azure AI
# Foundry endpoint directly - this is what makes per-user spend tracking
# (via LITELLM_END_USER_HEADER below) and the shared $100 budget on
# LITELLM_VIRTUAL_KEY possible. "http://litellm:8080" is this service's
# hostname on the docker network (see docker-compose.yml), not a host
# port - LITELLM_PROXY_URL only needs overriding for local, non-Docker
# development.
LITELLM_PROXY_URL = os.getenv("LITELLM_PROXY_URL", "http://litellm:8080").rstrip("/")

# Single virtual key shared by every user (not one key per user) - see
# example.env for how to generate it (POST /key/generate with max_budget:
# 100). Per-user attribution instead comes from LITELLM_END_USER_HEADER
# below, which LiteLLM records against the *end user*, not the key.
LITELLM_VIRTUAL_KEY = os.getenv("LITELLM_VIRTUAL_KEY", "")

# HTTP header LiteLLM proxy uses to attribute spend/usage to an individual
# end user despite every request sharing the one LITELLM_VIRTUAL_KEY above
# - see https://docs.litellm.ai/docs/proxy/users. Set to the user's email
# (see terminal_agent.py) rather than their Open WebUI user id, per
# product requirements.
LITELLM_END_USER_HEADER = "x-litellm-end-user-id"

# Keep an upstream provider stall bounded. Complex tool-using turns can need
# more than a minute before the provider returns its first streamed chunk, so
# use a three-minute ceiling. Retries are performed explicitly by
# TerminalGraphRuntime (rather than invisibly inside the OpenAI client) so the
# UI can report them and avoid replaying partial streamed output.
IDEA_MODEL_REQUEST_TIMEOUT_SECONDS = float(
    os.getenv("IDEA_MODEL_REQUEST_TIMEOUT_SECONDS", "180")
)
IDEA_MODEL_MAX_RETRIES = int(os.getenv("IDEA_MODEL_MAX_RETRIES", "1"))
