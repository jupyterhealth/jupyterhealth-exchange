#!/bin/bash

# Load Django .env
source .env

# Build JHE_DB_CONN from Django settings
export JHE_DB_CONN="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
export JHE_BASE_URL="http://localhost:8000"
export JHE_CLIENT_ID="${OIDC_CLIENT_ID}"

echo "Starting MCP HTTP Server..."
echo "JHE_BASE_URL: $JHE_BASE_URL"
echo "DB Connection configured: ${JHE_DB_CONN%%:*}://***"

.venv/bin/python mcp_server/src/jhe_mcp_http.py
