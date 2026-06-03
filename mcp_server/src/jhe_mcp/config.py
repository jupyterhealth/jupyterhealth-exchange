from __future__ import annotations

import os
from dataclasses import dataclass

# The OAuth/OIDC scopes JHE defines and the broker advertises/issues. JHE only
# supports these two; RBAC is enforced by JHE, not by scope. Single source of
# truth so the broker metadata and the issued AccessToken can't drift.
JHE_SCOPES: tuple[str, ...] = ("openid", "email")


@dataclass(frozen=True)
class Settings:
    jhe_base_url: str
    jhe_client_id: str
    jhe_client_secret: str | None
    redirect_uri: str
    authorize_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str
    mcp_resource_url: str
    broker_key: str | None
    allowed_redirects: tuple[str, ...]
    # When True, reject a token whose audience can't be confirmed via JHE
    # introspection (fail closed). When False (dev default), fall back to
    # userinfo-only validation if introspection is unavailable. See
    # JheTokenVerifier and README "Security considerations".
    require_audience: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        base = os.environ.get("JHE_BASE_URL")
        if not base:
            raise RuntimeError("JHE_BASE_URL is required")
        client_id = os.environ.get("JHE_CLIENT_ID")
        if not client_id:
            raise RuntimeError("JHE_CLIENT_ID is required")
        base = base.rstrip("/")
        mcp_resource_url = os.environ.get("MCP_RESOURCE_URL", "https://jhe-mcp.fly.dev").rstrip("/")
        raw_redirects = os.environ.get("MCP_ALLOWED_REDIRECTS", "")
        allowed_redirects = tuple(r.strip() for r in raw_redirects.split(",") if r.strip())
        broker_key = os.environ.get("MCP_BROKER_KEY")
        if broker_key is not None and len(broker_key) < 32:
            raise RuntimeError("MCP_BROKER_KEY must be at least 32 characters")
        require_audience = os.environ.get("MCP_REQUIRE_AUDIENCE", "false").strip().lower() in ("1", "true", "yes")
        return cls(
            jhe_base_url=base,
            jhe_client_id=client_id,
            jhe_client_secret=os.environ.get("JHE_CLIENT_SECRET"),
            redirect_uri=os.environ.get("JHE_REDIRECT_URI", "http://localhost:8765/callback"),
            authorize_endpoint=os.environ.get("JHE_AUTHORIZE_ENDPOINT", f"{base}/o/authorize/"),
            token_endpoint=os.environ.get("JHE_TOKEN_ENDPOINT", f"{base}/o/token/"),
            userinfo_endpoint=os.environ.get("JHE_USERINFO_ENDPOINT", f"{base}/o/userinfo/"),
            mcp_resource_url=mcp_resource_url,
            broker_key=broker_key,
            allowed_redirects=allowed_redirects,
            require_audience=require_audience,
        )
