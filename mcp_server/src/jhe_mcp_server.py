#!/usr/bin/env python3
"""
JupyterHealth Universal MCP Server (stdio transport)

A production-ready MCP server for JupyterHealth Exchange with:
- SMART on FHIR authentication (OAuth 2.0 + PKCE)
- Role-based access control
- Safe, pre-defined query tools
- Automatic token management

This version uses stdio transport for local subprocess execution.
For cloud deployment with HTTP/SSE, see jhe_mcp_http.py

Usage:
    python jhe_mcp_server.py

Configuration via environment variables (see .env.example)
"""
import asyncio
import sys
from pathlib import Path

from mcp.server.stdio import stdio_server

# Add src directory to Python path for imports
sys.path.insert(0, str(Path(__file__).parent))

from config import validate_config  # noqa: E402
from mcp_core import create_mcp_server  # noqa: E402


# ========== Server Entry Point ==========


async def main():
    """Run MCP server on stdio"""
    # Validate configuration
    try:
        validate_config()
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    # Create server with stdio authentication
    server = create_mcp_server(server_name="jupyterhealth-mcp")

    # Run server
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped by user", file=sys.stderr)
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)
