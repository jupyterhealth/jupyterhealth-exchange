# Environment for the local docker compose stack

Every committed file in this directory holds **public, local-only development values**.
They unlock nothing outside your own machine, and they are committed on purpose so
`docker compose up` works with no setup. Never reuse them in a real deployment, and never
add them as fallbacks in `jhe/settings.py` (other organizations self-host JHE; a fallback
there could let a deployment silently run on a public key).

| file | committed | used by | holds |
|---|---|---|---|
| `jhe.env` | yes | `jhe`, `cron` | every Django setting the stack needs except the OIDC signing key, which `compose/jhe-entrypoint.sh` generates into `local/oidc.key` on first boot so no private key is committed |
| `db.env` | yes | `db` | Postgres user/password/database |
| `mcp.env` | yes | `mcp` | MCP server's JHE client credentials and broker key |
| `local.secret.env` | **no** (gitignored) | `jhe`, `cron` | your real third-party credentials; optional, overrides `jhe.env` |

Values that depend on the published ports (`SITE_URL`, `MCP_OAUTH_REDIRECT_URI`, the MCP
server's JHE URLs) are set in `docker-compose.yml` itself and beat every file.

Values that must agree across files:

- `jhe.env` `MCP_OAUTH_CLIENT_ID` / `MCP_OAUTH_CLIENT_SECRET` = `mcp.env` `JHE_CLIENT_ID` / `JHE_CLIENT_SECRET`
- `jhe.env` `DB_NAME` / `DB_USER` / `DB_PASSWORD` = `db.env` `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD`

## Real credentials (`local.secret.env`)

Create `compose/environment/local.secret.env` only if you need it, for example:

```
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=you@example.com
SMTP_PASSWORD=...
```

With `SMTP_HOST` unset (the default), emails such as patient invitations, signup
verification and one-time login codes are printed to `docker compose logs jhe` instead.

Open Wearables and EHR client credentials are runtime settings (`JheSetting` rows,
editable under System Settings when logged in as `admin@example.com`), not env vars.
