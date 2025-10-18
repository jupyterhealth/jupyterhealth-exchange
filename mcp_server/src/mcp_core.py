"""
JupyterHealth MCP Server Core

Shared MCP server logic that can be used by multiple transport mechanisms:
- stdio transport (local subprocess)
- HTTP/SSE transport (cloud deployment)

This module contains tool definitions, handlers, and business logic
that is transport-agnostic.
"""

from typing import Any, Callable, Optional
from mcp.server import Server
from mcp.types import Tool, TextContent, Resource

from auth import get_valid_tokens, TokenCache, AuthContext
from tools import (
    get_study_count,
    list_studies,
    get_patient_demographics,
    get_study_metadata,
    get_patient_observations,
    get_jhe_schemas,
    get_schema_resource,
)


# ========== Tool Definitions ==========

TOOL_DEFINITIONS = [
    Tool(
        name="get_study_count",
        description="Count total studies accessible to the authenticated user based on their role and organization permissions",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="list_studies",
        description="List all studies accessible to the authenticated user with their IDs, names, and organizations",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_patient_demographics",
        description="Get patient demographics for a specific study. Returns patient IDs, ages, and emails. Requires study_id parameter.",
        inputSchema={
            "type": "object",
            "properties": {"study_id": {"type": "integer", "description": "The study identifier (integer)"}},
            "required": ["study_id"],
        },
    ),
    Tool(
        name="get_study_metadata",
        description="Get metadata about a specific study including name, description, organization, patient count, and observation count",
        inputSchema={
            "type": "object",
            "properties": {"study_id": {"type": "integer", "description": "The study identifier (integer)"}},
            "required": ["study_id"],
        },
    ),
    Tool(
        name="get_patient_observations",
        description="Get FHIR observations for a specific patient, including the FHIR/OMH JSONB data. Returns most recent observations with coding, data source, and full observation data.",
        inputSchema={
            "type": "object",
            "properties": {
                "patient_id": {"type": "integer", "description": "The patient identifier (integer)"},
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of observations to return (default: 10)",
                    "default": 10,
                },
            },
            "required": ["patient_id"],
        },
    ),
]


# ========== Authentication ==========


def authenticate_stdio() -> Optional[tuple[str, Optional[str]]]:
    """
    Authenticate using stdio-based OAuth flow (local process)

    Returns:
        Tuple of (access_token, id_token) or None if authentication fails
    """
    return get_valid_tokens()


def authenticate_http(access_token: str) -> Optional[tuple[str, Optional[str]]]:
    """
    Authenticate using HTTP Bearer token (cloud deployment)

    Args:
        access_token: OAuth access token from Authorization header

    Returns:
        Tuple of (access_token, None) - ID token not available in HTTP mode

    Note:
        In HTTP mode, we validate the access token against the JHE API
        and don't have access to the ID token. We'll need to fetch permissions
        via API calls instead of reading from ID token claims.
    """
    # For HTTP mode, we only have the access token
    # We'll validate it by trying to create an AuthContext
    # which will make an API call to fetch user permissions
    return (access_token, None)


# ========== Tool Execution ==========


def execute_tool(name: str, arguments: Any, auth: AuthContext) -> list[TextContent]:
    """
    Execute a tool with the given arguments and authentication context

    Args:
        name: Tool name
        arguments: Tool-specific arguments
        auth: Authenticated user context

    Returns:
        List of text content with results
    """
    try:
        if name == "get_study_count":
            return get_study_count(auth)

        elif name == "list_studies":
            return list_studies(auth)

        elif name == "get_patient_demographics":
            study_id = arguments.get("study_id")
            if not study_id:
                return [TextContent(type="text", text="❌ Missing required parameter: study_id")]
            return get_patient_demographics(auth, int(study_id))

        elif name == "get_study_metadata":
            study_id = arguments.get("study_id")
            if not study_id:
                return [TextContent(type="text", text="❌ Missing required parameter: study_id")]
            return get_study_metadata(auth, int(study_id))

        elif name == "get_patient_observations":
            patient_id = arguments.get("patient_id")
            if not patient_id:
                return [TextContent(type="text", text="❌ Missing required parameter: patient_id")]
            limit = arguments.get("limit", 10)
            return get_patient_observations(auth, int(patient_id), int(limit))

        else:
            return [TextContent(type="text", text=f"❌ Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"❌ Error executing {name}: {str(e)}")]


def get_tool_handler(
    auth_provider: Callable[[], Optional[tuple[str, Optional[str]]]],
) -> Callable[[str, Any], list[TextContent]]:
    """
    Create a tool handler function with authentication

    Args:
        auth_provider: Function that returns (access_token, id_token) tuple

    Returns:
        Handler function that can be used by MCP server
    """

    def handler(name: str, arguments: Any) -> list[TextContent]:
        """Execute tool with authentication"""
        # Get authentication
        tokens = auth_provider()

        if not tokens:
            return [
                TextContent(type="text", text="❌ Authentication failed. Please check your credentials and try again.")
            ]

        access_token, id_token = tokens

        # Validate token and get user context
        try:
            auth = AuthContext(access_token, id_token)
            auth.validate()
        except PermissionError as e:
            # Token invalid or missing ID token claims
            TokenCache.clear_token()
            return [
                TextContent(type="text", text=f"❌ Authentication error: {e}\nPlease try again to re-authenticate.")
            ]

        # Execute tool
        return execute_tool(name, arguments, auth)

    return handler


# ========== Schema Resources ==========


def get_schema_resources() -> list[Resource]:
    """
    Get list of database schema resources

    Returns:
        List of schema resources for MCP clients
    """
    schemas = get_jhe_schemas()

    resources = []
    for table_name, schema_info in schemas.items():
        resources.append(
            Resource(
                uri=f"schema://jupyterhealth/{table_name}",
                name=f"{table_name.replace('_', ' ').title()} Schema",
                mimeType="application/json",
                description=schema_info.get("description", f"Schema for {table_name} table"),
            )
        )

    return resources


# ========== Server Factory ==========


def create_mcp_server(
    server_name: str = "jupyterhealth-mcp",
    auth_provider: Optional[Callable[[], Optional[tuple[str, Optional[str]]]]] = None,
) -> Server:
    """
    Create a configured MCP server instance

    Args:
        server_name: Name for the MCP server
        auth_provider: Optional custom authentication provider
                      Defaults to stdio-based OAuth flow

    Returns:
        Configured MCP Server instance
    """
    server = Server(server_name)

    # Use stdio auth by default
    if auth_provider is None:
        auth_provider = authenticate_stdio

    # Get tool handler with authentication
    tool_handler = get_tool_handler(auth_provider)

    # Register tool list handler
    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOL_DEFINITIONS

    # Register tool call handler
    @server.call_tool()
    async def call_tool(name: str, arguments: Any) -> list[TextContent]:
        return tool_handler(name, arguments)

    # Register resource list handler
    @server.list_resources()
    async def list_resources() -> list[Resource]:
        return get_schema_resources()

    # Register resource read handler
    @server.read_resource()
    async def read_resource(uri: str) -> str:
        return get_schema_resource(uri)

    return server
