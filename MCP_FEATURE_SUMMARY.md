# MCP Integration Summary

## Overview
- Added end-to-end support for configuring and using Model Context Protocol (MCP) servers from IDEA’s web app.
- Backend now persists MCP connection metadata, securely stores optional auth tokens, and exposes APIs for managing and invoking MCP tools/resources/prompts.
- Frontend includes superuser management UI for MCP connections plus an end-user modal for exploring available MCP capabilities and sending results into chat sessions.

## Key Changes
- **Database & Models**
  - Introduced `MCPConnection` SQLModel (with Pydantic request/response schemas) and an Alembic migration to create the `mcpconnection` table and enum type (`models.py`, `alembic/versions/4a6f9e0bb0f4_add_mcp_connections_table.py`).
- **Security & Infrastructure**
  - Added `core/crypto.py` for Fernet-based secret storage tied to `SECRET_KEY`.
  - Updated project dependencies to include `mcp` and `cryptography` (`pyproject.toml`).
- **Backend Logic & APIs**
  - Extended CRUD helpers for MCP connections, normalizing payloads and encrypting tokens (`crud.py`).
  - Implemented asynchronous MCP client/connection manager supporting streamable HTTP, SSE, and stdio transports (`core/mcp_manager.py`).
  - Added `/mcp` FastAPI router for admin CRUD, listing active connections, and invoking tools/resources/prompts (`mcp_routes.py`, `app.py`).
- **Frontend**
  - Navigation buttons and modals for MCP admin and tool exploration (`frontend/index.html`, `frontend/styles.css`).
  - Admin management logic (`frontend/mcp-manager.js`) and end-user tools interface (`frontend/mcp-tools.js`).
  - Exposed a helper for injecting MCP outputs into the chat timeline (`frontend/assistant.js`) and configured new endpoints (`frontend/config.js`).
- **Chat Integration**
  - On each user message, the backend now lets the LLM decide whether to call MCP tools automatically, executes any selected tools, streams the results into chat, and feeds outputs back into the assistant context (`app.py`).

## Resulting Functionality
1. **Superusers** can create, edit, test, and delete MCP connections from the UI, including transport-specific configuration and secure token entry.
2. **All authenticated users** see available MCP connections, browse tools/resources/prompts, execute them, and push results directly into the conversation stream.
3. The assistant can automatically invoke MCP tools when helpful, blend their outputs into the chat history, and use the results to improve subsequent answers.
4. Backend maintains long-lived MCP sessions, increases reliability through automatic reconnection, and keeps sensitive credentials encrypted at rest.
