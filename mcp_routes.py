from datetime import datetime
from typing import Any, Dict, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

import crud
from auth import get_auth_token, get_current_user, get_db
from core.crypto import SecretEncryptionError
from core.mcp_manager import mcp_manager
from models import (
    MCPConnection,
    MCPConnectionCreate,
    MCPConnectionPublic,
    MCPConnectionSummary,
    MCPConnectionUpdate,
    MCPConnectionsPublic,
    MCPPromptRequest,
    MCPToolCallRequest,
    User,
)

router = APIRouter(prefix="/mcp", tags=["mcp"])


def get_user(token: str = Depends(get_auth_token)) -> User:
    user = get_current_user(token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return user


def require_superuser(user: User = Depends(get_user)) -> User:
    if not user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    return user


def _connection_to_public(connection) -> MCPConnectionPublic:
    return crud.mcp_connection_to_public(connection)


@router.get("/connections", response_model=MCPConnectionsPublic)
async def list_connections(user: User = Depends(require_superuser), db: Session = Depends(get_db)):
    connections = crud.list_mcp_connections(session=db)
    return MCPConnectionsPublic(
        data=[_connection_to_public(conn) for conn in connections],
        count=len(connections),
    )


@router.get("/connections/active", response_model=List[MCPConnectionSummary])
async def list_active_connections(user: User = Depends(get_user), db: Session = Depends(get_db)):
    connections = crud.list_active_mcp_connections(session=db)
    return [crud.mcp_connection_to_summary(conn) for conn in connections if conn.is_active]


@router.post("/connections", response_model=MCPConnectionPublic, status_code=status.HTTP_201_CREATED)
async def create_connection(
    payload: MCPConnectionCreate,
    user: User = Depends(require_superuser),
    db: Session = Depends(get_db),
):
    connection = crud.create_mcp_connection(session=db, connection_in=payload, created_by=user.id)
    return _connection_to_public(connection)


def _get_connection_or_404(connection_id: UUID, db: Session) -> MCPConnection:
    connection = crud.get_mcp_connection(session=db, connection_id=connection_id)
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP connection not found")
    return connection


@router.put("/connections/{connection_id}", response_model=MCPConnectionPublic)
async def update_connection(
    connection_id: UUID,
    payload: MCPConnectionUpdate,
    user: User = Depends(require_superuser),
    db: Session = Depends(get_db),
):
    connection = _get_connection_or_404(connection_id, db)
    updated = crud.update_mcp_connection(session=db, db_connection=connection, connection_in=payload)
    await mcp_manager.reset_connection(updated.id)
    return _connection_to_public(updated)


@router.get("/connections/{connection_id}", response_model=MCPConnectionPublic)
async def get_connection(
    connection_id: UUID,
    user: User = Depends(require_superuser),
    db: Session = Depends(get_db),
):
    connection = _get_connection_or_404(connection_id, db)
    return _connection_to_public(connection)


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: UUID,
    user: User = Depends(require_superuser),
    db: Session = Depends(get_db),
):
    connection = _get_connection_or_404(connection_id, db)
    await mcp_manager.reset_connection(connection.id)
    crud.delete_mcp_connection(session=db, db_connection=connection)
    return {}


def _ensure_access(connection, user: User) -> None:
    if not connection.is_active and not user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Connection is not active")


def _touch_connection(db: Session, connection) -> None:
    connection.last_connected_at = datetime.utcnow()
    db.add(connection)
    db.commit()
    db.refresh(connection)


def _prepare_tool_arguments(request: MCPToolCallRequest) -> Dict[str, Any]:
    return request.arguments or {}


@router.get("/connections/{connection_id}/tools")
async def list_tools(
    connection_id: UUID,
    user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    import logging
    import traceback
    logger = logging.getLogger(__name__)

    connection = _get_connection_or_404(connection_id, db)
    _ensure_access(connection, user)
    try:
        logger.info("About to call list_tools for connection %s", connection_id)
        tools = await mcp_manager.list_tools(connection)
        logger.info("Successfully got tools: %s", tools)
    except SecretEncryptionError as exc:
        logger.error("SecretEncryptionError: %s", exc, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Exception in list_tools: %s", exc, exc_info=True)
        logger.error("Full traceback: %s", traceback.format_exc())
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Failed to list tools: {exc}") from exc
    _touch_connection(db, connection)
    return tools


@router.get("/connections/{connection_id}/resources")
async def list_resources(
    connection_id: UUID,
    user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    connection = _get_connection_or_404(connection_id, db)
    _ensure_access(connection, user)
    try:
        resources = await mcp_manager.list_resources(connection)
    except SecretEncryptionError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Resources unavailable: {exc}") from exc
    _touch_connection(db, connection)
    return resources


@router.get("/connections/{connection_id}/prompts")
async def list_prompts(
    connection_id: UUID,
    user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    connection = _get_connection_or_404(connection_id, db)
    _ensure_access(connection, user)
    try:
        prompts = await mcp_manager.list_prompts(connection)
    except SecretEncryptionError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Prompts unavailable: {exc}") from exc
    _touch_connection(db, connection)
    return prompts


@router.post("/connections/{connection_id}/tools/{tool_name}")
async def call_tool(
    connection_id: UUID,
    tool_name: str,
    request: MCPToolCallRequest,
    user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    connection = _get_connection_or_404(connection_id, db)
    _ensure_access(connection, user)
    try:
        result = await mcp_manager.call_tool(connection, tool_name, _prepare_tool_arguments(request))
    except SecretEncryptionError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Tool execution failed: {exc}") from exc
    _touch_connection(db, connection)
    return result


@router.get("/connections/{connection_id}/resources/{resource_uri:path}")
async def read_resource(
    connection_id: UUID,
    resource_uri: str,
    user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    connection = _get_connection_or_404(connection_id, db)
    _ensure_access(connection, user)
    try:
        result = await mcp_manager.read_resource(connection, resource_uri)
    except SecretEncryptionError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to read resource: {exc}") from exc
    _touch_connection(db, connection)
    return result


@router.post("/connections/{connection_id}/prompts/{prompt_name}")
async def get_prompt(
    connection_id: UUID,
    prompt_name: str,
    request: MCPPromptRequest,
    user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    connection = _get_connection_or_404(connection_id, db)
    _ensure_access(connection, user)
    try:
        result = await mcp_manager.get_prompt(connection, prompt_name, request.arguments or {})
    except SecretEncryptionError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to load prompt: {exc}") from exc
    _touch_connection(db, connection)
    return result
