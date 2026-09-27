#!/usr/bin/env bash
# One-shot database setup script to initialize all service roles, schemas,
# and tables (LiteLLM, LangGraph, and Langfuse).
# Safe and idempotent to run on every deploy or new host provisioning.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=========================================================="
echo "==> Setting up LiteLLM database role and schema..."
echo "=========================================================="
"${SCRIPT_DIR}/setup_litellm_db.sh"

echo "=========================================================="
echo "==> Setting up LangGraph database role, schema, and tables..."
echo "=========================================================="
"${SCRIPT_DIR}/setup_langgraph_db.sh"

echo "=========================================================="
echo "==> Setting up Langfuse database role and schema..."
echo "=========================================================="
"${SCRIPT_DIR}/setup_langfuse_db.sh"

echo "=========================================================="
echo "==> All service database roles and schemas are initialized!"
echo "=========================================================="
