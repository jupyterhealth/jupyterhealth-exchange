# JupyterHealth Universal MCP Server

A production-ready Model Context Protocol (MCP) server for JupyterHealth Exchange with built-in authentication, role-based access control, and secure query capabilities.

## What is this?

This MCP server enables LLM applications (Claude Desktop, Claude Code, OpenAI, etc.) to securely query JupyterHealth Exchange data with proper authentication and permissions enforcement.

### Key Features

- 🔐 **Interactive OAuth Authentication** - Browser-based login with secure token caching
- 🎫 **Automatic Token Management** - One-time login, tokens cached locally
- 🛡️ **Role-Based Access Control** - Respects JHE organization and study permissions
- 🔒 **Safe Query Interface** - Pre-defined queries prevent SQL injection
- 🌐 **Dual Transport Support** - Local (stdio) or Cloud (HTTP/SSE)
- 📊 **Schema Discovery** - LLMs learn JHE database structure automatically

---

## Deployment Options

### Option 1: Local (stdio)
- ✅ Runs on your machine as subprocess
- ✅ Direct database access (fastest)
- ✅ Best for: Development, power users

### Option 2: Cloud (HTTP/SSE)
- ✅ Deployed to Fly.io/AWS
- ✅ Accessible from anywhere via HTTPS
- ✅ Best for: Production, multiple users

**Both use identical OAuth authentication!** The only difference is the transport mechanism.

---

## Quick Start

### Prerequisites

1. JupyterHealth Exchange account (username/password)
2. OAuth client ID (ask your JHE admin or see [Registering OAuth Clients](#registering-oauth-clients))
3. MCP-compatible client (Claude Desktop, Cursor, etc.)

### Local Setup (stdio)

**1. Install**
```bash
cd jupyterhealth-exchange/mcp_server
pip install -r requirements.txt
```

**2. Configure Claude Desktop**

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "jupyterhealth": {
      "command": "python",
      "args": [
        "/path/to/jupyterhealth-exchange/mcp_server/src/jhe_mcp_server.py"
      ],
      "env": {
        "JHE_BASE_URL": "https://jhe.fly.dev",
        "JHE_CLIENT_ID": "your-client-id-here",
        "JHE_DB_CONN": "postgresql://user:pass@host:5432/jhe"
      }
    }
  }
}
```

**3. Use it!**

Ask Claude: "How many studies can I access?"

- **First time**: Browser opens → Log in → Token cached at `~/.jhe_mcp/token_cache.json`
- **Next time**: Uses cached token automatically

---

### Cloud Setup (HTTP/SSE)

**1. Configure Claude Desktop**

```json
{
  "mcpServers": {
    "jupyterhealth-cloud": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://jhe.fly.dev:8001/sse"
      ]
    }
  }
}
```

**For local testing** (before deploying to cloud):
```json
{
  "mcpServers": {
    "jupyterhealth-cloud": {
      "command": "/path/to/npx",
      "args": [
        "mcp-remote",
        "http://localhost:8001/sse"
      ]
    }
  }
}
```

**Note:** Find your npx path with `which npx` (often `~/.nvm/versions/node/vXX.XX.X/bin/npx`)

**2. Use it!**

Same experience as local - browser popup, automatic caching!

---

## Authentication Flow

Both local and cloud use the **same interactive OAuth flow**:

```
1. User asks Claude a question requiring JHE data
   ↓
2. MCP server checks for cached token (~/.jhe_mcp/token_cache.json)
   ├─ Token exists & valid → Use it (skip to step 9)
   └─ No token or expired → Continue to step 3
   ↓
3. MCP server opens browser to JHE login page
   ↓
4. User enters username + password in browser
   ↓
5. User clicks "Authorize" to grant MCP access
   ↓
6. Browser redirects back with authorization code
   ↓
7. MCP server exchanges code for access token + ID token
   ↓
8. Tokens saved to ~/.jhe_mcp/token_cache.json (chmod 600)
   ↓
9. MCP server reads permissions from ID token custom claims
   ↓
10. Query executed with user's permissions
```

**Key Benefit**: ID token contains `jhe_permissions` custom claims, so permission checks are ~100x faster (no API calls needed!)

---

## Available Tools

### 1. `get_study_count`
Count studies accessible to authenticated user.

**Example:**
```
User: "How many studies can I access?"
Claude: [calls get_study_count] → "You have access to 12 studies"
```

### 2. `list_studies`
List all accessible studies with IDs, names, and organizations.

**Example:**
```
User: "What studies can I see?"
Claude: [calls list_studies] → Lists all studies with details
```

### 3. `get_patient_demographics`
Get patient demographics for a specific study.

**Parameters:**
- `study_id` (integer, required)

**Example:**
```
User: "Show me patient demographics for study 5"
Claude: [calls get_patient_demographics(study_id=5)]
```

### 4. `get_study_metadata`
Get metadata about a specific study.

**Parameters:**
- `study_id` (integer, required)

**Example:**
```
User: "What is study 5 about?"
Claude: [calls get_study_metadata(study_id=5)]
```

### 5. `get_patient_observations`
Get FHIR observations for a specific patient.

**Parameters:**
- `patient_id` (integer, required)
- `limit` (integer, optional, default: 10)

**Example:**
```
User: "Show recent observations for patient 101"
Claude: [calls get_patient_observations(patient_id=101)]
```

---

## Comparison: Local vs Cloud

| Feature | Local (stdio) | Cloud (HTTP/SSE) |
|---------|---------------|------------------|
| **Authentication** | Browser OAuth popup | Browser OAuth popup ✓ Same! |
| **Token Cache** | ~/.jhe_mcp/token_cache.json | ~/.jhe_mcp/token_cache.json ✓ Same! |
| **First Use** | Opens browser for login | Opens browser for login ✓ Same! |
| **Performance** | ~5ms per query | ~15ms per query (HTTP overhead) |
| **Database Access** | Direct | Via MCP server |
| **Setup Complexity** | Medium (need DB creds) | Easy (no DB creds) |
| **Credentials Needed** | DB + OAuth client ID | OAuth client ID only |
| **Best For** | Power users, dev/test | Production, multiple users |
| **Deployment** | User's machine | Cloud (Fly.io, AWS, etc.) |

---

## For Administrators

### Deploying Cloud MCP Server

The cloud MCP server is deployed alongside the Django app:

```bash
cd jupyterhealth-exchange
flyctl deploy
```

This deploys both services:
- Django app: `https://jhe.fly.dev` (port 8000)
- MCP server: `https://jhe.fly.dev:8001/sse` (port 8001)

### Registering OAuth Clients

Users need a CLIENT_ID to authenticate. Create one in JHE Django admin:

**Via Django Admin UI:**

1. Log into `https://jhe.fly.dev/admin/`
2. Navigate to "OAuth2 Provider" → "Applications"
3. Click "Add Application"
4. Fill in settings:
   - **Name**: "MCP Client for [User Name]"
   - **Client Type**: "Public"
   - **Authorization Grant Type**: "Authorization code"
   - **Redirect URIs**: `http://localhost:8765/callback`
   - **Skip authorization**: False
5. Save and provide the **Client ID** to the user

**Via Django Shell:**

```python
python manage.py shell

from oauth2_provider.models import Application

app = Application.objects.create(
    name="MCP Client for John Doe",
    client_type="public",
    authorization_grant_type="authorization-code",
    redirect_uris="http://localhost:8765/callback",
    skip_authorization=False
)

print(f"Client ID: {app.client_id}")
```

---

## Troubleshooting

### "Browser doesn't open"
- Check firewall settings allow browser communication
- Manually visit the URL printed in terminal/logs
- Ensure port 8765 is available for OAuth callback

### "Token expired" or "Authentication failed"
```bash
# Delete cached token to trigger fresh OAuth flow
rm ~/.jhe_mcp/token_cache.json
```

Next request will open browser for re-authentication.

### "Permission denied to study X"
- Verify your JHE account has access to the study
- Check your role in the study's organization (manager/member/viewer)
- Contact your JHE administrator to grant access

### "Connection refused" (Local stdio)
- Verify `JHE_DB_CONN` connection string is correct
- Test database access: `psql $JHE_DB_CONN`
- Check network access to database host

### "Connection timeout" (Cloud HTTP/SSE)
- Check MCP server is running: `curl https://jhe.fly.dev:8001/health`
- Verify firewall allows HTTPS traffic on port 8001
- Check Fly.io logs: `flyctl logs -a jhe`

### "Tool execution error: AuthContext.__init__() got an unexpected keyword argument"
This was a bug in the initial HTTP/SSE implementation. Update to latest version of `jhe_mcp_http.py` which uses `AuthContext(token=..., id_token=...)` instead of `AuthContext(access_token=..., id_token=...)`

### Claude Desktop shows tools but they fail when called
1. Check the MCP server logs: `tail -f ~/Library/Logs/Claude/mcp-server-jupyterhealth-cloud.log`
2. Verify the server is running and accessible
3. Test authentication manually by deleting cached token: `rm ~/.jhe_mcp/token_cache.json`
4. For HTTP/SSE: Ensure `mcp-remote` package is installed (it auto-installs via npx)

---

## Security

### Authentication
- ✅ OAuth 2.0 with PKCE (Proof Key for Code Exchange)
- ✅ Tokens stored with restricted permissions (chmod 600)
- ✅ Automatic token refresh via refresh_token
- ✅ ID token validation via JHE OAuth provider

### Authorization
- ✅ Role-based access control (manager, member, viewer)
- ✅ Organization-scoped permissions
- ✅ Study-level access validation
- ✅ Permissions read from ID token custom claims (no API calls)

### Query Safety
- ✅ No arbitrary SQL execution
- ✅ Pre-defined, parameterized queries only
- ✅ SQL injection prevention
- ✅ Row-level filtering by user permissions

---

## Development

### Repository Structure

The MCP server is part of the JupyterHealth Exchange monorepo:

```
jupyterhealth-exchange/              # Django 5.2 FHIR R5 API + MCP Server
├── core/                            # Django app - Core models & APIs
│   ├── models/                      # Patient, Study, Organization, etc.
│   ├── views/                       # FHIR R5 API endpoints
│   ├── permissions.py               # Role-based access control
│   └── migrations/                  # Database schema migrations
│
├── jhe/                             # Django project settings
│   ├── settings.py                  # Database, OAuth, CORS config
│   ├── urls.py                      # URL routing
│   └── wsgi.py                      # WSGI application
│
├── mcp_server/                      # 🎯 MCP Server (YOU ARE HERE)
│   ├── src/
│   │   ├── jhe_mcp_server.py        # Stdio transport (local)
│   │   ├── jhe_mcp_http.py          # HTTP/SSE transport (cloud)
│   │   ├── mcp_core.py              # Shared tool definitions
│   │   ├── auth/
│   │   │   ├── oauth_handler.py     # OAuth flow + callback server
│   │   │   ├── token_cache.py       # Secure token storage
│   │   │   └── auth_context.py      # Permissions from ID token
│   │   ├── tools/
│   │   │   ├── study_tools.py       # Study queries
│   │   │   └── schema_tools.py      # Schema discovery
│   │   └── config.py                # Environment config
│   ├── requirements.txt             # MCP dependencies
│   └── README.md                    # This file
│
├── data/                            # FHIR/OMH schema definitions
│   ├── omh/                         # Open mHealth schemas
│   └── iglu/                        # Iglu schema registry
│
├── docs/                            # Sphinx documentation
├── scripts/                         # Utility scripts
├── tests/                           # Django test suite
│
├── manage.py                        # Django management CLI
├── Pipfile / Pipfile.lock           # Python dependencies (pipenv)
├── Dockerfile                       # Container image for Django + MCP
├── fly.toml                         # Fly.io deployment config
└── .env                             # Local environment variables
```

**Key Points:**
- **Monorepo**: Django app and MCP server share same codebase
- **Shared deployment**: Both services deploy together (Dockerfile, fly.toml)
- **Shared database**: MCP server queries same PostgreSQL as Django
- **Shared OAuth**: MCP uses Django's OAuth2 provider for authentication
- **Independent operation**: MCP server can run standalone (stdio) or alongside Django (HTTP/SSE)

### Running Tests

```bash
# From jupyterhealth-exchange root
pytest mcp_server/tests/
```

### Manual Testing

**Test local stdio server:**
```bash
cd mcp_server
python src/jhe_mcp_server.py
```

**Test cloud HTTP/SSE server:**
```bash
cd jupyterhealth-exchange

# Export required environment variables
export JHE_BASE_URL="http://localhost:8000"
export JHE_CLIENT_ID="your-client-id"
export JHE_DB_CONN="postgresql://user:pass@host:5432/jhe"

# Start the HTTP/SSE server
python mcp_server/src/jhe_mcp_http.py
```

Server will start on `http://localhost:8001`

**Test the endpoints:**
```bash
# Server info
curl http://localhost:8001/

# Health check
curl http://localhost:8001/health

# Test SSE connection
curl http://localhost:8001/sse
```

**Test with Claude Desktop:**

1. Update `claude_desktop_config.json`:
   ```json
   {
     "mcpServers": {
       "jupyterhealth-cloud": {
         "command": "/path/to/npx",
         "args": ["mcp-remote", "http://localhost:8001/sse"]
       }
     }
   }
   ```

2. Restart Claude Desktop
3. Ask: "How many studies can I access?"
4. Browser will open for OAuth login on first request

---

## License

MIT License - See [LICENSE](../LICENSE) file

---

## Support

- **MCP Protocol Docs**: https://modelcontextprotocol.io
- **JHE Documentation**: https://github.com/the-commons-project/jupyterhealth-exchange
- **Issues**: https://github.com/the-commons-project/jupyterhealth-exchange/issues
