# JHE MCP Server

The JHE MCP Server (`jhe_mcp`) exposes JupyterHealth Exchange (JHE) data to LLM clients via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/). It acts as an **OAuth broker**: it presents an OAuth 2.0 Authorization Server interface to MCP clients while delegating the actual authentication to JHE (powered by django-oauth-toolkit + OIDC). The user logs in at the JHE instance; the MCP server forwards the resulting JHE-issued tokens and proxies data requests on the user's behalf. Because JHE enforces per-user RBAC, each user sees only the studies, patients, and observations they are authorized to access — the MCP server inherits those boundaries automatically.

The server is deployed at **`https://jhe-mcp.fly.dev`** (Fly app `jhe-mcp`).

> 📖 **Full documentation lives on the JupyterHealth docs site:**
> **<https://jupyterhealth.github.io/software-documentation/jhe/mcp-server>**
>
> The docs site is the source of truth for deployment, OAuth client registration, the configuration reference, and connecting an LLM client. This README is the developer-oriented quick start for working in this directory.

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

The MCP server is **stateless** — it stores no tokens. The client holds and refreshes the JHE-issued token, and every MCP tool call carries that token, which the server forwards to JHE REST/FHIR requests where RBAC is enforced.

> **Two distinct OAuth identities — don't conflate them:**
>
> - **MCP server ↔ JHE:** the MCP server is a **confidential** OAuth client of JHE, holding `JHE_CLIENT_ID` + `JHE_CLIENT_SECRET`. These live **only in the server deployment** (Fly secrets) and are never seen by end users.
> - **LLM client ↔ MCP server:** the LLM client (e.g. via `mcp-remote`) authenticates to the broker as a **public** client — a `client_id` only, **no secret**. The end user's actual identity is established by logging in at JHE with their own credentials.

See [Architecture in the docs](https://jupyterhealth.github.io/software-documentation/jhe/mcp-server#architecture) for the full request/redirect flow.

---

## Local Development

### Setup

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

### Running tests

```bash
uv run pytest tests/unit
```

### Running the server

```bash
jhe-mcp-http
```

Set the required environment variables before starting (see the [Configuration reference](https://jupyterhealth.github.io/software-documentation/jhe/mcp-server#configuration) in the docs), or create a `.env` file and load it into your shell.

---

## Tools

The MCP server exposes the following tools to LLM clients. Every tool runs as the authenticated user and only returns data that user is authorized to see. See the [Tools section in the docs](https://jupyterhealth.github.io/software-documentation/jhe/mcp-server#tools) for full descriptions.

- **Studies:** `get_study_count`, `list_studies`, `get_study_metadata`, `list_study_patients`
- **Patients:** `search_patients`, `get_patient_demographics`, `get_patient_date_range`
- **Observations:** `count_patient_observations`, `count_study_observations`, `summarize_patient_observations`, `get_patient_observations`
- **OMH schemas:** `get_omh_schema` (schemas are also browsable as resources at `omh://schema/<name>`)

The observation and patient-search tools rely on JHE's FHIR search support (`date`, `_summary=count`, `_sort`, and the Patient search params introduced in #667); a JHE without it silently ignores unknown search params, so date windows and ordering would not be applied. **Deploy order matters:** roll out the JHE backend with #667 before (never after) deploying this server — against a stale backend the ignored `_sort` silently corrupts `get_patient_date_range`, it doesn't just widen date windows.

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

---

## Documentation

The full guide is on the JupyterHealth docs site: **<https://jupyterhealth.github.io/software-documentation/jhe/mcp-server>**. It covers:

- [Registering the OAuth client in JHE](https://jupyterhealth.github.io/software-documentation/jhe/mcp-server#registering-the-oauth-client-in-jhe) — self-service and Django-admin paths, field values, and reproducible JHE-side seeding
- [Configuration reference](https://jupyterhealth.github.io/software-documentation/jhe/mcp-server#configuration) — all environment variables
- [Connecting an LLM client](https://jupyterhealth.github.io/software-documentation/jhe/mcp-server#connecting-an-llm-client) — Claude Code/Desktop, Gemini CLI, ChatGPT, and the `mcp-remote` fallback
- [Deploying](https://jupyterhealth.github.io/software-documentation/jhe/mcp-server#deploying) — container deployment on any platform, plus the Fly.io reference deployment
