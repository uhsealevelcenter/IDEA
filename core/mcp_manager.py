import asyncio
import json
import logging
from contextlib import AsyncExitStack
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID
from enum import Enum

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client

from core.crypto import decrypt_secret, SecretEncryptionError
from models import MCPConnection, MCPTransportType

logger = logging.getLogger(__name__)


def _serialise(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    dict_method = getattr(obj, "dict", None)
    if callable(dict_method):
        return dict_method()
    if isinstance(obj, list):
        return [_serialise(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _serialise(value) for key, value in obj.items()}
    return obj


def _normalise_headers(headers: Optional[Dict[str, str]]) -> Dict[str, str]:
    return {str(k): str(v) for k, v in (headers or {}).items()}


def _normalise_env(env: Optional[Dict[str, str]]) -> Dict[str, str]:
    return {str(k): str(v) for k, v in (env or {}).items()}


class ManagedMCPClient:
    def __init__(self, connection_id: UUID):
        self.connection_id = connection_id
        self._lock = asyncio.Lock()
        self._session: Optional[ClientSession] = None
        self._exit_stack: Optional[AsyncExitStack] = None
        self._cached_fingerprint: Optional[str] = None
        self.server_info: Optional[Dict[str, Any]] = None

    @staticmethod
    def _fingerprint(connection: MCPConnection) -> str:
        payload = {
            "transport": connection.transport.value,
            "endpoint": connection.endpoint,
            "command": connection.command,
            "command_args": connection.command_args or [],
            "headers": connection.headers or {},
            "config": connection.config or {},
            "has_auth_token": bool(connection.auth_token),
        }
        return json.dumps(payload, sort_keys=True)

    async def _ensure_session(self, connection: MCPConnection) -> ClientSession:
        fingerprint = self._fingerprint(connection)
        if self._session and fingerprint == self._cached_fingerprint:
            return self._session

        async with self._lock:
            fingerprint = self._fingerprint(connection)
            if self._session and fingerprint == self._cached_fingerprint:
                return self._session

            if self._session:
                await self._close_session()

            await self._open_session(connection, fingerprint)
            return self._session  # type: ignore[return-value]

    async def _open_session(self, connection: MCPConnection, fingerprint: str) -> None:
        exit_stack = AsyncExitStack()
        try:
            session = await self._create_session(exit_stack, connection)
            init_result = await session.initialize()
            self.server_info = _serialise(init_result.serverInfo)
            self._exit_stack = exit_stack
            self._session = session
            self._cached_fingerprint = fingerprint
            logger.info(
                "MCP connection %s initialised with server %s",
                connection.id,
                self.server_info,
            )
        except Exception:
            await exit_stack.aclose()
            raise

    async def _create_session(self, exit_stack: AsyncExitStack, connection: MCPConnection) -> ClientSession:
        token = None
        if connection.auth_token not in (None, ""):
            try:
                token = decrypt_secret(connection.auth_token)
            except SecretEncryptionError as exc:
                logger.error("Failed to decrypt MCP token for %s: %s", connection.id, exc)
                raise

        config = connection.config or {}
        timeout = config.get("timeout", 30)

        if connection.transport == MCPTransportType.STREAMABLE_HTTP:
            if not connection.endpoint:
                raise ValueError("Streamable HTTP transport requires an endpoint URL")
            headers = _normalise_headers(connection.headers)
            if token and "Authorization" not in headers:
                scheme = config.get("auth_scheme", "Bearer")
                headers["Authorization"] = f"{scheme} {token}" if scheme else token  # type: ignore[arg-type]
            transport_cm = streamablehttp_client(
                url=connection.endpoint,
                headers=headers if headers else None,
                timeout=timeout,
            )
        elif connection.transport == MCPTransportType.SSE:
            if not connection.endpoint:
                raise ValueError("SSE transport requires an endpoint URL")
            headers = _normalise_headers(connection.headers)
            if token and "Authorization" not in headers:
                scheme = config.get("auth_scheme", "Bearer")
                headers["Authorization"] = f"{scheme} {token}" if scheme else token  # type: ignore[arg-type]
            transport_cm = sse_client(
                url=connection.endpoint,
                headers=headers if headers else None,
                timeout=timeout,
            )
        elif connection.transport == MCPTransportType.STDIO:
            if not connection.command:
                raise ValueError("Stdio transport requires a command")
            command = [connection.command, *(connection.command_args or [])]
            env = _normalise_env(config.get("env"))
            if token and config.get("token_env_var"):
                env.setdefault(str(config["token_env_var"]), token)
            elif token and "MCP_AUTH_TOKEN" not in env:
                env["MCP_AUTH_TOKEN"] = token

            cwd = config.get("cwd")
            transport_cm = stdio_client(command=command, cwd=cwd, env=env or None)
        else:  # pragma: no cover - defensive
            raise ValueError(f"Unsupported transport {connection.transport}")

        read_stream, write_stream, _ = await exit_stack.enter_async_context(transport_cm)
        session = await exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
        return session

    async def _close_session(self) -> None:
        if self._exit_stack:
            try:
                await self._exit_stack.aclose()
            finally:
                self._exit_stack = None
        self._session = None
        self._cached_fingerprint = None

    async def close(self) -> None:
        async with self._lock:
            await self._close_session()

    async def get_session(self, connection: MCPConnection) -> ClientSession:
        return await self._ensure_session(connection)


class MCPConnectionManager:
    def __init__(self):
        self._clients: Dict[UUID, ManagedMCPClient] = {}
        self._lock = asyncio.Lock()

    async def _get_client(self, connection_id: UUID) -> ManagedMCPClient:
        async with self._lock:
            client = self._clients.get(connection_id)
            if client is None:
                client = ManagedMCPClient(connection_id)
                self._clients[connection_id] = client
            return client

    async def get_session(self, connection: MCPConnection) -> ClientSession:
        client = await self._get_client(connection.id)
        return await client.get_session(connection)

    async def reset_connection(self, connection_id: UUID) -> None:
        async with self._lock:
            client = self._clients.pop(connection_id, None)
        if client:
            await client.close()

    async def close_all(self) -> None:
        async with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        await asyncio.gather(*(client.close() for client in clients), return_exceptions=True)

    async def list_tools(self, connection: MCPConnection) -> Dict[str, Any]:
        session = await self.get_session(connection)
        logger.info("Calling list_tools on session: %s", session)
        result = await session.list_tools()
        logger.info("list_tools result: %s (type: %s)", result, type(result))
        serialised = _serialise(result)
        logger.info("Serialised result: %s", serialised)
        return serialised

    async def list_resources(self, connection: MCPConnection) -> Dict[str, Any]:
        session = await self.get_session(connection)
        try:
            result = await session.list_resources()
        except Exception as exc:
            logger.info("MCP connection %s does not support list_resources: %s", connection.id, exc)
            raise
        return _serialise(result)

    async def list_prompts(self, connection: MCPConnection) -> Dict[str, Any]:
        session = await self.get_session(connection)
        try:
            result = await session.list_prompts()
        except Exception as exc:
            logger.info("MCP connection %s does not support list_prompts: %s", connection.id, exc)
            raise
        return _serialise(result)

    async def call_tool(self, connection: MCPConnection, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        session = await self.get_session(connection)
        result = await session.call_tool(tool_name, arguments)
        return _serialise(result)

    async def read_resource(self, connection: MCPConnection, uri: str) -> Dict[str, Any]:
        session = await self.get_session(connection)
        result = await session.read_resource(uri)
        return _serialise(result)

    async def get_prompt(self, connection: MCPConnection, prompt_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        session = await self.get_session(connection)
        result = await session.get_prompt(prompt_name, arguments)
        return _serialise(result)


mcp_manager = MCPConnectionManager()
