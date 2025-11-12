#!/bin/bash

# JupyterHealth MCP Server - Local Testing Script
# Tests both stdio and HTTP/SSE transports without deploying to cloud

set -e  # Exit on error

echo "=========================================="
echo "JupyterHealth MCP Server - Local Tests"
echo "=========================================="
echo ""

# Load environment
source .env
export JHE_DB_CONN="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
export JHE_BASE_URL="http://localhost:8000"
export JHE_CLIENT_ID="${OIDC_CLIENT_ID}"

echo "Configuration:"
echo "  JHE_BASE_URL: $JHE_BASE_URL"
echo "  JHE_CLIENT_ID: $JHE_CLIENT_ID"
echo "  DB Connection: postgresql://${DB_USER}:***@${DB_HOST}:${DB_PORT}/${DB_NAME}"
echo ""

# Check if Django is running
echo "Test 1: Checking Django server..."
if curl -s http://localhost:8000/ > /dev/null; then
    echo "✓ Django server is running on port 8000"
else
    echo "✗ Django server not running. Please start it:"
    echo "  .venv/bin/python manage.py runserver"
    exit 1
fi
echo ""

# Check if we have a cached OAuth token
echo "Test 2: Checking OAuth token cache..."
if [ -f ~/.jhe_mcp/token_cache.json ]; then
    echo "✓ Token cache exists at ~/.jhe_mcp/token_cache.json"
    ACCESS_TOKEN=$(cat ~/.jhe_mcp/token_cache.json | python3 -c "import sys, json; print(json.load(sys.stdin).get('access_token', ''))" 2>/dev/null)
    if [ -n "$ACCESS_TOKEN" ]; then
        echo "  Access token: ${ACCESS_TOKEN:0:20}..."
    fi
else
    echo "✗ No cached token found. First MCP request will trigger OAuth flow."
fi
echo ""

# Test 3: Start MCP HTTP server in background
echo "Test 3: Starting MCP HTTP/SSE server..."
.venv/bin/python mcp_server/src/jhe_mcp_http.py > /tmp/mcp_http.log 2>&1 &
MCP_PID=$!
echo "  Started with PID: $MCP_PID"
sleep 3

# Check if MCP server started successfully
if ps -p $MCP_PID > /dev/null; then
    echo "✓ MCP HTTP server is running"
else
    echo "✗ MCP HTTP server failed to start. Check logs:"
    cat /tmp/mcp_http.log
    exit 1
fi
echo ""

# Test 4: Health check
echo "Test 4: Testing MCP HTTP server health endpoint..."
HEALTH_RESPONSE=$(curl -s http://localhost:8001/health)
if echo "$HEALTH_RESPONSE" | grep -q "healthy"; then
    echo "✓ Health check passed"
    echo "  Response: $HEALTH_RESPONSE"
else
    echo "✗ Health check failed"
    echo "  Response: $HEALTH_RESPONSE"
    kill $MCP_PID
    exit 1
fi
echo ""

# Test 5: Server info endpoint
echo "Test 5: Testing MCP server info endpoint..."
INFO_RESPONSE=$(curl -s http://localhost:8001/ | python3 -m json.tool 2>/dev/null)
if echo "$INFO_RESPONSE" | grep -q "JupyterHealth MCP Server"; then
    echo "✓ Server info endpoint works"
    echo "$INFO_RESPONSE" | head -15
else
    echo "✗ Server info endpoint failed"
    kill $MCP_PID
    exit 1
fi
echo ""

# Test 6: Test stdio MCP server (if we have a token)
if [ -f ~/.jhe_mcp/token_cache.json ]; then
    echo "Test 6: Testing stdio MCP server..."
    echo "  Note: This will use the cached token"

    # Create a simple test - just import and validate config
    cat > /tmp/test_stdio_mcp.py <<'EOF'
import sys
sys.path.insert(0, 'mcp_server/src')

try:
    from config import validate_config
    validate_config()
    print("✓ Stdio MCP server configuration is valid")
except Exception as e:
    print(f"✗ Stdio MCP server configuration error: {e}")
    sys.exit(1)
EOF

    .venv/bin/python /tmp/test_stdio_mcp.py
    rm /tmp/test_stdio_mcp.py
else
    echo "Test 6: Skipping stdio test (no cached token)"
fi
echo ""

# Test 7: Verify mcp_core module
echo "Test 7: Testing mcp_core module..."
cat > /tmp/test_mcp_core.py <<'EOF'
import sys
sys.path.insert(0, 'mcp_server/src')

try:
    from mcp_core import TOOL_DEFINITIONS, create_mcp_server
    print(f"✓ mcp_core module loaded successfully")
    print(f"  Found {len(TOOL_DEFINITIONS)} tool definitions:")
    for tool in TOOL_DEFINITIONS:
        print(f"    - {tool.name}")
except Exception as e:
    print(f"✗ mcp_core module error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
EOF

.venv/bin/python /tmp/test_mcp_core.py
rm /tmp/test_mcp_core.py
echo ""

# Clean up
echo "=========================================="
echo "Cleanup"
echo "=========================================="
echo "Stopping MCP HTTP server (PID: $MCP_PID)..."
kill $MCP_PID
sleep 1
echo "✓ Stopped MCP HTTP server"
echo ""

# Summary
echo "=========================================="
echo "Test Summary"
echo "=========================================="
echo "✓ All tests passed!"
echo ""
echo "Next steps to test with Claude Desktop:"
echo ""
echo "1. Configure Claude Desktop (~/Library/Application Support/Claude/claude_desktop_config.json):"
echo ""
echo "   For LOCAL stdio version:"
echo '   {'
echo '     "mcpServers": {'
echo '       "jupyterhealth": {'
echo '         "command": "python",'
echo '         "args": ["'$(pwd)'/mcp_server/src/jhe_mcp_server.py"],'
echo '         "env": {'
echo '           "JHE_BASE_URL": "'$JHE_BASE_URL'",'
echo '           "JHE_CLIENT_ID": "'$JHE_CLIENT_ID'",'
echo '           "JHE_DB_CONN": "postgresql://..."'
echo '         }'
echo '       }'
echo '     }'
echo '   }'
echo ""
echo "   For CLOUD HTTP/SSE version (start MCP server first):"
echo '   {'
echo '     "mcpServers": {'
echo '       "jupyterhealth-cloud": {'
echo '         "command": "npx",'
echo '         "args": ["mcp-remote", "http://localhost:8001/sse"],'
echo '         "env": {'
echo '           "JHE_CLIENT_ID": "'$JHE_CLIENT_ID'"'
echo '         }'
echo '       }'
echo '     }'
echo '   }'
echo ""
echo "2. Restart Claude Desktop"
echo ""
echo "3. Ask Claude: 'How many studies can I access?'"
echo ""
echo "4. First time: Browser will open for OAuth login"
echo "   Subsequent requests: Uses cached token"
echo ""
