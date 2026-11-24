#!/usr/bin/env bash
# Mint a 365-day API access token for the Bruno collection in opencollection/JHE/
# (paste it into the collection's ACCESS_TOKEN variable), or for curl:
#   curl -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/organizations
#
#   scripts/dev/bruno-token.sh                         token for admin@example.com
#   scripts/dev/bruno-token.sh --email someone@example.com
#
# Note: create_bruno_app also makes the user a manager of every organization.
set -euo pipefail
exec "$(dirname "$0")/manage.sh" create_bruno_app "$@"
