#!/usr/bin/env python3
"""
JupyterHealth Universal MCP Server (HTTP/SSE transport)

Cloud-deployable MCP server with HTTP/SSE transport for remote access.
This version can be deployed to Fly.io, AWS, or other cloud providers.

Key differences from stdio version:
- Uses HTTP/SSE transport instead of stdio
- Same interactive OAuth flow (browser popup)
- Can handle multiple concurrent client connections
- Designed for 24/7 cloud deployment

Usage:
    uvicorn jhe_mcp_http:app --host 0.0.0.0 --port 8001

Authentication:
    Same as stdio version - interactive OAuth flow with browser popup
    on first request. Tokens cached at ~/.jhe_mcp/token_cache.json

Configuration via environment variables (see .env.example)
"""
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Dict, Any

# Add src directory to Python path for imports
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from mcp.types import (
    JSONRPCMessage,
    JSONRPCRequest,
    JSONRPCResponse,
    JSONRPCError,
)
from config import validate_config
from mcp_core import TOOL_DEFINITIONS, execute_tool, authenticate_stdio
from auth import AuthContext

# Initialize FastAPI app
app = FastAPI(
    title="JupyterHealth MCP Server",
    description="Model Context Protocol server for JupyterHealth Exchange with OAuth authentication",
    version="1.0.0",
)

# Session management
sse_sessions: Dict[str, asyncio.Queue] = {}


# ========== MCP Request Models ==========


class MCPRequest(BaseModel):
    """MCP JSON-RPC request"""

    jsonrpc: str = "2.0"
    id: int | str | None = None
    method: str
    params: Dict[str, Any] | None = None


# ========== MCP Message Handlers ==========


async def handle_initialize(params: Dict[str, Any] | None, request_id: int | str | None) -> Dict[str, Any]:
    """Handle MCP initialize request"""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "jupyterhealth-mcp-http", "version": "1.0.0"},
        },
    }


async def handle_tools_list(params: Dict[str, Any] | None, request_id: int | str | None) -> Dict[str, Any]:
    """Handle tools/list request"""
    # Get tools from TOOL_DEFINITIONS
    tools = [
        {"name": tool.name, "description": tool.description, "inputSchema": tool.inputSchema}
        for tool in TOOL_DEFINITIONS
    ]

    return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}}


async def handle_tools_call(params: Dict[str, Any] | None, request_id: int | str | None) -> Dict[str, Any]:
    """Handle tools/call request"""
    if not params or "name" not in params:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32602, "message": "Invalid params: name is required"},
        }

    tool_name = params["name"]
    arguments = params.get("arguments", {})

    try:
        # Authenticate - triggers browser OAuth flow if no cached token
        tokens = authenticate_stdio()
        if not tokens:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32001, "message": "Authentication failed"}}

        # Create auth context
        access_token, id_token = tokens
        auth = AuthContext(token=access_token, id_token=id_token)

        # Execute tool
        result = execute_tool(tool_name, arguments, auth)

        # Convert TextContent list to dict format
        content = [{"type": content_item.type, "text": content_item.text} for content_item in result]

        return {"jsonrpc": "2.0", "id": request_id, "result": {"content": content}}
    except Exception as e:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32603, "message": f"Tool execution error: {str(e)}"},
        }


async def handle_mcp_request(mcp_request: MCPRequest) -> Dict[str, Any]:
    """Route MCP request to appropriate handler"""
    method = mcp_request.method
    params = mcp_request.params
    request_id = mcp_request.id

    if method == "initialize":
        return await handle_initialize(params, request_id)
    elif method == "tools/list":
        return await handle_tools_list(params, request_id)
    elif method == "tools/call":
        return await handle_tools_call(params, request_id)
    elif method == "notifications/initialized":
        # Client acknowledges initialization - no response needed
        return None
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}


# ========== SSE Endpoint ==========


@app.get("/sse")
async def sse_connection():
    """
    Establish SSE connection for MCP communication

    This endpoint establishes a Server-Sent Events connection and sends
    the session-specific endpoint URL for posting MCP messages.

    Returns:
        StreamingResponse with SSE events
    """
    # Generate unique session ID
    session_id = str(uuid.uuid4())

    # Create message queue for this session
    message_queue: asyncio.Queue = asyncio.Queue()
    sse_sessions[session_id] = message_queue

    async def event_generator():
        """Generate SSE events"""
        try:
            # Send initial endpoint event with session-specific URL
            endpoint_data = f"/messages?session_id={session_id}"
            yield f"event: endpoint\ndata: {endpoint_data}\n\n"

            # Keep connection alive and forward messages from queue
            while True:
                try:
                    # Wait for message with timeout to allow periodic keep-alive
                    message = await asyncio.wait_for(message_queue.get(), timeout=30.0)

                    # Send message as SSE event
                    message_json = json.dumps(message)
                    yield f"data: {message_json}\n\n"

                except asyncio.TimeoutError:
                    # Send keep-alive comment
                    yield ": keep-alive\n\n"
                    continue

        except asyncio.CancelledError:
            # Client disconnected
            print(f"SSE connection closed for session {session_id}")
        finally:
            # Clean up session
            if session_id in sse_sessions:
                del sse_sessions[session_id]

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable proxy buffering
        },
    )


# ========== Messages Endpoint ==========


@app.post("/messages")
async def messages_endpoint(
    request: Request, session_id: str = Query(..., description="Session ID from SSE endpoint event")
):
    """
    Receive MCP messages and send responses via SSE

    This endpoint receives MCP JSON-RPC requests and sends responses
    back through the established SSE connection.

    Args:
        request: FastAPI request with MCP message in body
        session_id: Session ID from SSE connection

    Returns:
        MCP response (also sent via SSE)
    """
    # Validate session exists
    if session_id not in sse_sessions:
        return {"jsonrpc": "2.0", "error": {"code": -32000, "message": f"Invalid session_id: {session_id}"}}

    # Parse MCP request
    try:
        body = await request.json()
        mcp_request = MCPRequest(**body)
    except Exception as e:
        error_response = {"jsonrpc": "2.0", "error": {"code": -32700, "message": f"Parse error: {str(e)}"}}
        # Send via SSE
        message_queue = sse_sessions[session_id]
        await message_queue.put(error_response)
        return error_response

    # Handle MCP request
    response = await handle_mcp_request(mcp_request)

    # If there's a response (some notifications don't have responses)
    if response is not None:
        # Send response via SSE channel
        message_queue = sse_sessions[session_id]
        await message_queue.put(response)

    # Also return response directly
    return response if response is not None else {"status": "ok"}


# ========== Health Check Endpoint ==========


@app.get("/health")
async def health_check():
    """
    Health check endpoint for monitoring

    Returns:
        Health status information
    """
    return {
        "status": "healthy",
        "service": "jupyterhealth-mcp-http",
        "version": "1.0.0",
        "active_sessions": len(sse_sessions),
    }


# ========== Server Info Endpoint ==========


@app.get("/")
async def root():
    """
    Root endpoint with server information

    Returns:
        Server information and usage instructions
    """
    return {
        "service": "JupyterHealth MCP Server",
        "version": "1.0.0",
        "transport": "HTTP/SSE",
        "endpoints": {
            "/sse": "Establish SSE connection (GET)",
            "/messages": "Send MCP messages (POST with session_id)",
            "/health": "Health check endpoint",
            "/": "This information page",
        },
        "authentication": {
            "type": "Interactive OAuth 2.0 Flow",
            "method": "Browser popup (same as stdio version)",
            "cache": "~/.jhe_mcp/token_cache.json",
            "instructions": "Connect via MCP client - browser opens automatically on first request",
        },
        "documentation": {
            "mcp_protocol": "https://modelcontextprotocol.io",
            "jhe_docs": "https://github.com/the-commons-project/jupyterhealth-exchange",
        },
    }


# ========== Startup Event ==========


@app.on_event("startup")
async def startup_event():
    """Validate configuration on startup"""
    try:
        validate_config()
        print("✓ Configuration validated successfully")
    except ValueError as e:
        print(f"✗ Configuration error: {e}", file=sys.stderr)
        sys.exit(1)


# ========== Main Entry Point ==========

if __name__ == "__main__":
    import uvicorn

    # Get port from environment or use default
    port = int(os.getenv("MCP_PORT", "8001"))
    host = os.getenv("MCP_HOST", "0.0.0.0")

    print(f"Starting JupyterHealth MCP Server (HTTP/SSE)")
    print(f"Listening on {host}:{port}")
    print(f"SSE endpoint: GET http://{host}:{port}/sse")
    print(f"Messages endpoint: POST http://{host}:{port}/messages?session_id=XXX")

    uvicorn.run("jhe_mcp_http:app", host=host, port=port, log_level="info", access_log=True)
