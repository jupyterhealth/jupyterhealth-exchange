# JHE MCP Server

The JHE MCP Server (`jhe_mcp`) exposes JupyterHealth Exchange (JHE) data to LLM clients via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/). It acts as an **OAuth broker**: it presents an OAuth 2.0 Authorization Server interface to MCP clients while delegating the actual authentication to JHE (powered by django-oauth-toolkit + OIDC). The user logs in at the JHE instance; the MCP server forwards the resulting JHE-issued tokens and proxies data requests on the user's behalf. Because JHE enforces per-user RBAC, each user sees only the studies, patients, and observations they are authorized to access — the MCP server inherits those boundaries automatically.

The server is deployed at **`https://jhe-mcp.fly.dev`** (Fly app `jhe-mcp`).

---

## Architecture

```
LLM Client (e.g. Claude Desktop)
        │  MCP over Streamable HTTP (/mcp)
        ▼
┌─────────────────────────────────────┐
│         JHE MCP Server              │
│  OAuth Broker (Authorization façade)│
│  MCP Tools (studies, patients, obs) │
└────────────┬────────────────────────┘
             │  OAuth 2.0 + REST API calls
             ▼
┌─────────────────────────────────────┐
│       JupyterHealth Exchange        │
│  Authorization Server (django-oauth-│
│  toolkit + OIDC + PKCE)             │
│  RBAC enforced per user             │
│  Data: studies / patients / obs     │
└─────────────────────────────────────┘
```

**Flow:**
1. The LLM client connects to the MCP server's Streamable HTTP endpoint (`/mcp`) and initiates OAuth.
2. The MCP server redirects the user to **JHE's own login screen**.
3. The user enters **their own** JHE credentials; JHE issues an authorization code to the MCP server.
4. The MCP server exchanges the code for JHE tokens and hands them back to the client. The server is **stateless** — it stores no tokens; the client holds and refreshes them.
5. Subsequent MCP tool calls carry the user's token, which the server forwards to JHE REST API requests — RBAC is enforced entirely by JHE.

> **Two distinct OAuth identities — don't conflate them:**
> - **MCP server ↔ JHE:** the MCP server is a **confidential** OAuth client of JHE, holding `JHE_CLIENT_ID` + `JHE_CLIENT_SECRET`. These live **only in the server deployment** (Fly secrets) and are never seen by end users.
> - **LLM client ↔ MCP server:** the LLM client (e.g. via `mcp-remote`) authenticates to the broker as a **public** client — a `client_id` only, **no secret**. The end user's actual identity is established by logging in at JHE with their own credentials.

---

## Registering the OAuth Client in JHE

The MCP server must be registered as an OAuth 2.0 confidential client in the JHE instance it will talk to. There are two ways to do this. In the URLs below, replace `<jhe-host>` with your JHE instance's host (for example, the host where JupyterHealth Exchange is deployed).

> **Important:** Do **not** use JHE's portal "Clients" page for this. That page creates a public client with a fixed `{SITE_URL}/auth/callback` redirect URI and cannot issue a `client_secret`. Use one of the two admin paths below instead.

### Option A — Self-service (any admin/staff user)

Navigate to:
```
https://<jhe-host>/o/applications/register/
```

Log in with a staff or superuser account, fill in the fields from the table below, and save.

### Option B — Django admin (superuser)

Navigate to:
```
https://<jhe-host>/admin/oauth2_provider/application/add/
```

Fill in the same fields from the table below. The admin form also exposes a **User** field — set it to the admin user creating the record, or leave it blank.

### Field Values

| Field | Value |
|---|---|
| Name | `JHE MCP Server` |
| Client type | `Confidential` |
| Authorization grant type | `Authorization code` |
| Redirect URIs | `https://jhe-mcp.fly.dev/oauth/callback` |
| Algorithm | `RSA with SHA-256 (RS256)` |
| Post logout redirect URIs | *(leave blank)* |
| Allowed origins | *(leave blank)* |
| User *(admin form only)* | the admin user creating it, or blank |

> **Copy the `client_secret` immediately after saving.** django-oauth-toolkit 3.x hashes the secret on save and never displays it again. If you lose it, you must regenerate a new one.

> **PKCE (S256)** is enforced globally via the `PKCE_REQUIRED` setting in JHE — it is not a per-application field and does not need to be configured here.

### Setting Fly Secrets

After registering, store the credentials as Fly secrets for the `jhe-mcp` app:

```bash
fly secrets set -a jhe-mcp \
  JHE_CLIENT_ID=<client_id> \
  JHE_CLIENT_SECRET=<client_secret> \
  MCP_BROKER_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))") \
  MCP_RESOURCE_URL=https://jhe-mcp.fly.dev
```

### Reproducible JHE-side seeding

Manual registration (above) is a one-time setup. To make the broker's JHE OAuth
application **reproducible** — so a fresh `python manage.py seed` recreates it
(as a confidential client with `skip_authorization=True`, i.e. no consent
prompt) instead of needing the admin UI — set the **same** credentials on the
**JHE deployment** too:

```bash
fly secrets set -a jhe \
  MCP_OAUTH_CLIENT_ID=<client_id> \
  MCP_OAUTH_CLIENT_SECRET=<client_secret>
  # optional: MCP_OAUTH_REDIRECT_URI=https://jhe-mcp.fly.dev/oauth/callback (this is the default)
```

`seed.py::seed_mcp_broker_application` reads these and creates/updates the
`JHE MCP Server` application; when they're unset (local/CI seeds) it's skipped.
They must match the `JHE_CLIENT_ID` / `JHE_CLIENT_SECRET` set on `jhe-mcp` above,
so the broker can authenticate against the seeded record.

---

## Deploying

The MCP server is an **optional, standalone service** — it is *not* part of a
JupyterHealth Exchange deployment. JHE and the MCP server are deployed
**independently**; deploying JHE never deploys the MCP server. The MCP server
connects to JHE over the network as an OAuth client (via `JHE_BASE_URL`), so one
JHE instance can have zero, one, or several MCP servers pointed at it.

### JHE *without* the MCP server (default)

Deploy JupyterHealth Exchange as usual. No MCP-related steps are needed — JHE is
fully functional on its own; the only thing absent is LLM/MCP access. Nothing in
this section applies.

### JHE *with* the MCP server

The server ships as a standard **container** (the `Dockerfile` in this
`mcp_server/` directory) and runs on any container platform — a plain Docker
host, Kubernetes, Cloud Run, ECS, Fly.io, etc. To stand it up against a running
JHE instance:

1. **Register the OAuth client** in that JHE — see [Registering the OAuth Client in JHE](#registering-the-oauth-client-in-jhe).
2. **Provide the required environment variables / secrets** via your platform's
   mechanism — `JHE_BASE_URL`, `JHE_CLIENT_ID`, `JHE_CLIENT_SECRET`,
   `MCP_BROKER_KEY`, `MCP_RESOURCE_URL` (full list in
   [Configuration](#configuration)). `JHE_BASE_URL` is what aims the server at a
   particular JHE; `MCP_RESOURCE_URL` must be the server's own public URL.
3. **Build and run the container**, exposing its HTTP port (`8401`) behind TLS.
   The image builds reproducibly from the committed `uv.lock`:
   ```bash
   docker build -t jhe-mcp .
   docker run -p 8401:8401 --env-file .env jhe-mcp   # or your platform's run/secret mechanism
   ```
4. **Verify** it's healthy:
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' https://<your-host>/health   # expect 200
   ```

No CD pipeline is shipped — deploy with whatever your platform uses. Runtime
config lives in your platform's secret store, not in this repo. To run against a
different JHE, change `JHE_BASE_URL` (and the matching client credentials) and
redeploy.

#### Reference deployment (Fly.io)

Our hosted instance runs on [Fly.io](https://fly.io) (app `jhe-mcp`), using the
`fly.toml` in this directory. Set secrets as in [Setting Fly Secrets](#setting-fly-secrets), then:

```bash
fly deploy -a jhe-mcp    # build from uv.lock + deploy
fly logs   -a jhe-mcp    # tail logs
fly status -a jhe-mcp    # machine / image status
```

---

## Connecting an LLM Client

The server speaks the modern MCP **Streamable HTTP transport** at `/mcp` and implements **OAuth 2.0 Dynamic Client Registration (DCR, RFC 7591)** plus discovery metadata (RFC 9728 / RFC 8414). That means clients **connect directly to the URL and register themselves** — no bridge, no manually-issued client ID. On first connect the user is sent to **JHE's own login page**; after they sign in, the client caches and refreshes the JHE-issued token automatically.

> **The end user supplies nothing but their JHE login.** The broker's confidential JHE credentials (`JHE_CLIENT_ID` / `JHE_CLIENT_SECRET`) live solely in the server deployment and are never seen by clients or users. Each client mints its own public client ID via DCR.

In every client below, the only value you provide is the server URL: **`https://jhe-mcp.fly.dev/mcp`**.

### Claude Code

```bash
claude mcp add --transport http jhe https://jhe-mcp.fly.dev/mcp
```

The first tool call opens JHE's login in your browser. Verify with `claude mcp list` / `claude mcp get jhe`.

### Claude Desktop

Add a connector via **Settings → Connectors → Add custom connector**, with URL `https://jhe-mcp.fly.dev/mcp`. (Recent desktop builds support remote connectors with OAuth + DCR directly.)

### Google Gemini (Gemini CLI)

`~/.gemini/settings.json` — use the `httpUrl` field for Streamable HTTP:

```json
{
  "mcpServers": {
    "jhe": {
      "httpUrl": "https://jhe-mcp.fly.dev/mcp"
    }
  }
}
```

### ChatGPT (OpenAI)

- **ChatGPT app "Connectors" (developer mode):** add a connector with URL `https://jhe-mcp.fly.dev/mcp`. The server advertises the discovery metadata and DCR that ChatGPT expects.
- **Responses / Agents API:** the API does not run the OAuth flow for you — obtain a JHE access token out-of-band and pass it on the `mcp` tool:
  ```json
  {"type":"mcp","server_label":"jhe","server_url":"https://jhe-mcp.fly.dev/mcp","authorization":"<JHE access token>","require_approval":"never"}
  ```

### Fallback: stdio-only clients

For a client that cannot speak remote Streamable HTTP at all, bridge with [`mcp-remote`](https://github.com/geelen/mcp-remote) (it will DCR automatically — no client ID needed):

```bash
npx -y mcp-remote https://jhe-mcp.fly.dev/mcp
```

---

## Configuration

The server is configured entirely via environment variables (or Fly secrets in production).

| Variable | Required | Purpose |
|---|---|---|
| `JHE_BASE_URL` | Yes | Base URL of the JHE instance (e.g. `https://jhe.fly.dev`) |
| `JHE_CLIENT_ID` | Yes | The broker's **confidential** OAuth client ID at JHE (server-side only; never given to end users or LLM clients) |
| `JHE_CLIENT_SECRET` | No (required only for a confidential client — recommended) | The broker's confidential client secret at JHE (server-side only; copy immediately — hashed on save) |
| `MCP_RESOURCE_URL` | Yes | Public URL of this MCP server (e.g. `https://jhe-mcp.fly.dev`) |
| `MCP_BROKER_KEY` | Yes | Random secret used to encrypt OAuth state and authorization codes; generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `MCP_ALLOWED_REDIRECTS` | No | Comma-separated list of non-loopback redirect URIs to allow (for additional MCP client types) |
| `MCP_HTTP_PORT` | No | Port the HTTP server listens on (default: `8401`) |
| `MCP_REQUIRE_AUDIENCE` | No | When `true`, reject tokens whose audience can't be confirmed via JHE introspection (fail closed). Default `false` (dev): fall back to userinfo-only validation when introspection is unavailable. Set `true` in production. See [Security considerations](#security-considerations--known-limitations). |

---

## Local Development

### Setup

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

### Running Tests

```bash
uv run pytest tests/unit
```

### Running the Server

```bash
jhe-mcp-http
```

Set the required environment variables (see [Configuration](#configuration)) before starting, or create a `.env` file and load it into your shell.

---

## Tools

The MCP server exposes the following tools to LLM clients. Every tool runs as the
authenticated user and only returns data that user is authorized to see.

Studies:

- **`get_study_count`** — Returns the total number of studies the authenticated user can access.
- **`list_studies`** — Lists all studies visible to the authenticated user, with key metadata.
- **`get_study_metadata`** — Retrieves detailed metadata for a specific study by ID.
- **`list_study_patients`** — Lists patients enrolled in a specific study, returning ID, name, and email for each.

Patients:

- **`get_patient_demographics`** — Returns demographic information for a specific patient by patient ID.
- **`get_patient_date_range`** — Returns the earliest and latest observation dates and total count for a patient (first/last-data answers without paging).

Observations:

- **`count_patient_observations`** — Returns the exact observation count for a patient, optionally filtered by OMH data type and date range, without returning records.
- **`count_study_observations`** — Returns the observation count across a whole study in one call; with `by_patient=True` returns a `{patient_id: count}` map.
- **`summarize_patient_observations`** — Returns a compact per-data-type digest for a patient (`{type: {count, earliest, latest}}`) — the "show me everything" overview.
- **`get_patient_observations`** — Fetches one page of a patient's observations (with total/pagination), optionally filtered by OMH data type and date range; defaults to compact records, with `verbosity="full"` for the raw OMH body.

OMH schemas:

- **`get_omh_schema`** — Returns the full OMH JSON schema for a data type by short name (e.g. `heart-rate`, `blood-glucose`). Schemas are also browsable as resources at `omh://schema/<name>`.

---

## Security considerations & known limitations

The broker is intentionally **stateless** (no database), which shapes a few deliberate tradeoffs. These are by design — call them out in review rather than treat them as oversights:

- **Authorization codes are single-use *by TTL only*.** The broker stores no consumption record, so a code is valid until it expires (`CODE_TTL`, 30s) rather than being invalidated on first use. The exposure is bounded by the short TTL, PKCE, and the exact `redirect_uri` binding. True single-use would require shared server-side state.
- **Open Dynamic Client Registration.** `/register` is unauthenticated and accepts any `https` (or loopback `http`) redirect URI — this is the standard public-DCR model. A registered client may use any `https` redirect it declared, so the protection against token redirection is **JHE login** (the user must authenticate), PKCE, and the exact redirect match. Issued `client_id`s are Fernet-signed and expire after 7 days; the only bulk revocation is rotating `MCP_BROKER_KEY`.
- **No per-authorization consent prompt.** JHE's OAuth application for the broker is configured `skip_authorization=True` (matching JHE's other first-party clients), so after login the user is not shown a separate "approve this app" screen. This is a deliberate first-party-trust choice for a dev deployment; a public-facing deployment may want consent **on** for transparency (set `skip_authorization=False` on the application — django-oauth-toolkit then renders its consent page). Note this flag lives on the JHE-side `Application` record (runtime DB), not in the broker's code.
- **Token revalidation latency.** Bearer tokens are validated against JHE's `/o/userinfo`, cached for 60s. A token revoked at JHE may remain accepted for up to that window.
- **Audience enforcement is best-effort and fails *open* by default.** Every request's bearer token is confirmed live via `/o/userinfo`, then — best-effort — checked against JHE token introspection (`/o/introspect/`) to confirm it was issued to *our* broker client and reject foreign-audience tokens. If introspection is unavailable (JHE returns 403/404, is unreachable, or returns a malformed body) the broker falls back to **userinfo-only** validation: it then accepts *any* live JHE token regardless of which client it was issued to. This is the dev default. Set `MCP_REQUIRE_AUDIENCE=true` in production to instead **fail closed** — reject the request when audience cannot be confirmed.
- **Loopback redirect URIs (native-app pattern).** For a client that authorizes without DCR, `/authorize` accepts a `http(s)://localhost|127.0.0.1|[::1]` redirect on any port — the RFC 8252 native-app loopback flow, where the app binds an ephemeral local port. Any-port loopback is intended; the local-interception risk is the documented, accepted limitation of all loopback OAuth flows and is mitigated by the **required PKCE (S256)** plus the exact `redirect_uri` match at `/token`. DCR clients are constrained to their registered redirects; non-loopback, non-registered redirects must be in `MCP_ALLOWED_REDIRECTS`.

### Operational follow-ups (not yet implemented)

- **Rate-limit `/register`, `/authorize`, `/token`** — deferred to the production cutover; the current deployment is dev-only. This must be enforced at a real **edge** (e.g. Cloudflare/WAF rate rules) or via a **distributed, shared-state** limiter, not in-process: with `min_machines_running = 0` and potentially multiple machines, in-app counters reset on cold-start and don't bound request volume across instances, so an in-process limiter can't provide the guarantee. (Fly `concurrency` limits cap *concurrent* requests per machine, not request *rate* — a blunt stopgap at best.)
- **Per-client revocation** would require introducing a small persistent client store (trades away statelessness).

> **Reproducible images are already in place** (this was previously listed here as a follow-up): the `Dockerfile` installs exactly from the committed `uv.lock` via `uv sync --frozen` — no `pyproject.toml` range resolution at build time — on a digest-pinned base image with a pinned `uv` binary, multi-stage, non-root, with the package manager stripped from the runtime layer.
