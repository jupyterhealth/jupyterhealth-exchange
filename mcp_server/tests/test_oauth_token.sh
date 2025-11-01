#!/bin/bash

# Load .env to get OIDC_CLIENT_ID
source .env

# Get OAuth token using password grant
TOKEN_RESPONSE=$(curl -s -X POST http://localhost:8000/o/token/ \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=password" \
  -d "username=mary@example.com" \
  -d "password=Jhe1234!" \
  -d "client_id=${OIDC_CLIENT_ID}" \
  -d "scope=openid")

echo "Token Response:"
echo "$TOKEN_RESPONSE" | python3 -m json.tool

# Extract access token
ACCESS_TOKEN=$(echo "$TOKEN_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null)

if [ -n "$ACCESS_TOKEN" ]; then
  echo ""
  echo "✓ Access Token obtained successfully!"
  echo "Token (first 50 chars): ${ACCESS_TOKEN:0:50}..."
  echo ""
  echo "Saving to access_token.txt for testing..."
  echo "$ACCESS_TOKEN" > access_token.txt
else
  echo ""
  echo "✗ Failed to get access token"
fi
