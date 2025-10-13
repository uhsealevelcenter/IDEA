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

    The tool_id format is: mcp_{connection_id}_{tool_name}

    Examples:
        # List ERDDAP servers
        result = call_mcp_tool('mcp_27cf12b7b85f4ab9ac48edb82cbd2eb1_list_servers')

        # Search for datasets
        result = call_mcp_tool(
            'mcp_27cf12b7b85f4ab9ac48edb82cbd2eb1_search_datasets',
            query='sea surface temperature',
            server_url='https://coastwatch.pfeg.noaa.gov/erddap'
        )

    Args:
        tool_id: The MCP tool identifier (format: mcp_{connection_id}_{tool_name})
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

    connection_id_str, tool_name = parts

    # Convert connection_id back to UUID format (add hyphens)
    if len(connection_id_str) != 32:
        return {"error": f"Invalid connection ID in tool_id: {connection_id_str}"}

    connection_id_formatted = f"{connection_id_str[:8]}-{connection_id_str[8:12]}-{connection_id_str[12:16]}-{connection_id_str[16:20]}-{connection_id_str[20:]}"

    try:
        connection_uuid = UUID(connection_id_formatted)
    except ValueError as e:
        return {"error": f"Invalid UUID in tool_id: {connection_id_formatted}: {e}"}

    # Import here to avoid circular dependencies
    from models import MCPConnection
    from sqlmodel import Session
    from core.db import engine
    from core.mcp_manager import mcp_manager

    # Query database for the connection
    with Session(engine) as session:
        connection = session.get(MCPConnection, connection_uuid)
        if not connection:
            return {"error": f"MCP connection not found: {connection_uuid}"}

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

                    tool_id = f"mcp_{connection.id.hex}_{tool_name}".replace("-", "_")
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
