"""
Static MCP tools helper module.

This module provides functions to call MCP tools by querying the database
for active MCP connections at runtime. No file regeneration needed.
"""
import asyncio
import json
from uuid import UUID
from typing import Any, Dict, Optional


def call_mcp_tool(tool_id: str, **kwargs) -> Dict[str, Any]:
    """
    Call an MCP tool by its ID.

    The tool_id format is: mcp_{connection_id_prefix}_{tool_name}
    where connection_id_prefix is the first 12 characters of the connection UUID hex.

    Examples:
        # List ERDDAP servers
        result = call_mcp_tool('mcp_27cf12b7b85f_list_servers')

        # Search for datasets
        result = call_mcp_tool(
            'mcp_27cf12b7b85f_search_datasets',
            query='sea surface temperature',
            server_url='https://coastwatch.pfeg.noaa.gov/erddap'
        )

    Args:
        tool_id: The MCP tool identifier (format: mcp_{connection_id_prefix}_{tool_name})
        **kwargs: Tool-specific arguments

    Returns:
        Dict containing the tool execution result or error
    """
    # Parse tool_id to extract connection_id and tool_name
    if not tool_id.startswith('mcp_'):
        return {"error": f"Invalid tool_id format: {tool_id}"}

    parts = tool_id[4:].split('_', 1)  # Remove 'mcp_' prefix and split once
    if len(parts) != 2:
        return {"error": f"Invalid tool_id format: {tool_id}. Expected format: mcp_{{connection_id}}_{{tool_name}}"}

    connection_id_prefix, tool_name = parts

    # Import here to avoid circular dependencies
    from models import MCPConnection
    from sqlmodel import Session, select
    from core.db import engine
    from core.mcp_manager import mcp_manager
    import crud

    # Query database for the connection by prefix matching
    with Session(engine) as session:
        connection = None
        
        # Try full UUID first (32 chars)
        if len(connection_id_prefix) == 32:
            connection_id_formatted = f"{connection_id_prefix[:8]}-{connection_id_prefix[8:12]}-{connection_id_prefix[12:16]}-{connection_id_prefix[16:20]}-{connection_id_prefix[20:]}"
            try:
                connection_uuid = UUID(connection_id_formatted)
                connection = session.get(MCPConnection, connection_uuid)
            except ValueError:
                pass
        
        # Fall back to prefix matching (12 chars)
        if connection is None and len(connection_id_prefix) >= 12:
            # Get all active connections and find one that matches the prefix
            connections = crud.list_active_mcp_connections(session=session)
            for conn in connections:
                if conn.id.hex[:len(connection_id_prefix)] == connection_id_prefix:
                    connection = conn
                    break
        
        if not connection:
            return {"error": f"MCP connection not found for prefix: {connection_id_prefix}"}

        if not connection.is_active:
            return {"error": f"MCP connection is inactive: {connection.name}"}

        # Call the MCP tool asynchronously
        import nest_asyncio
        nest_asyncio.apply()

        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(
            mcp_manager.call_tool(connection, tool_name, kwargs)
        )

        # Print execution info for visibility
        print(f"\n🔧 MCP Tool: {connection.name} / {tool_name}")
        print(f"   Arguments: {json.dumps(kwargs, indent=2) if kwargs else '(none)'}")

        # Pretty-print result preview
        result_str = json.dumps(result, indent=2) if isinstance(result, dict) else str(result)
        if len(result_str) > 500:
            print(f"   Result preview: {result_str[:500]}...\n")
        else:
            print(f"   Result: {result_str}\n")

        return result


def list_available_tools() -> Dict[str, Any]:
    """
    List all available MCP tools from active connections.

    Returns:
        Dict mapping tool_id to tool metadata
    """
    from sqlmodel import Session
    from core.db import engine
    import crud
    from core.mcp_manager import mcp_manager

    tools_info = {}

    with Session(engine) as session:
        connections = crud.list_active_mcp_connections(session=session)

        for connection in connections:
            try:
                # Use nest_asyncio for async calls
                import nest_asyncio
                nest_asyncio.apply()

                loop = asyncio.get_event_loop()
                tools_payload = loop.run_until_complete(
                    mcp_manager.list_tools(connection)
                )

                tools = tools_payload.get("tools") or []

                for tool in tools:
                    tool_name = tool.get("name")
                    if not tool_name:
                        continue

                    # Use 12-char prefix format consistent with gather_available_mcp_tools
                    import re
                    prefix = f"mcp_{connection.id.hex[:12]}_"
                    slug = re.sub(r"[^a-zA-Z0-9_]", "_", str(tool_name)).lower()
                    tool_id = f"{prefix}{slug}"
                    
                    tools_info[tool_id] = {
                        "connection_id": str(connection.id),
                        "connection_name": connection.name,
                        "tool_name": tool_name,
                        "description": tool.get("description", ""),
                        "parameters": tool.get("inputSchema", {}),
                    }
            except Exception as exc:
                print(f"Warning: Failed to list tools for {connection.name}: {exc}")
                continue

    return tools_info


def get_tool_info(tool_id: str) -> Optional[Dict[str, Any]]:
    """
    Get metadata about a specific MCP tool.

    Args:
        tool_id: The MCP tool identifier

    Returns:
        Dict containing tool metadata or None if not found
    """
    all_tools = list_available_tools()
    return all_tools.get(tool_id)
