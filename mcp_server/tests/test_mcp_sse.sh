#!/bin/bash

# Extract access token from cache
ACCESS_TOKEN=$(cat ~/.jhe_mcp/token_cache.json | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")

echo "Testing MCP HTTP/SSE Server"
echo "=============================="
echo ""
echo "Access Token (first 20 chars): ${ACCESS_TOKEN:0:20}..."
echo ""

# Test without authentication (should fail)
echo "Test 1: Request without Authorization header (should fail with 401)"
curl -s -X POST http://localhost:8001/sse \
  -H "Content-Type: application/json" \
  -d '{}' | head -5
echo ""
echo ""

# Test with authentication
echo "Test 2: Request with valid Bearer token (should succeed)"
curl -s -X POST http://localhost:8001/sse \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}' | head -20
echo ""
