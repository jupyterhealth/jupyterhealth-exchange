from __future__ import annotations

import httpx
import pytest
import respx
from jhe_mcp.auth.token_verifier import JheAccessToken, JheTokenVerifier
from jhe_mcp.config import Settings

_BASE = "https://jhe.example.test"


def _settings(*, require_audience: bool = False) -> Settings:
    return Settings(
        jhe_base_url=_BASE,
        jhe_client_id="jhe-mcp-client",
        jhe_client_secret=None,
        redirect_uri="http://localhost/cb",
        authorize_endpoint=f"{_BASE}/o/authorize/",
        token_endpoint=f"{_BASE}/o/token/",
        userinfo_endpoint=f"{_BASE}/o/userinfo/",
        mcp_resource_url="http://testserver",
        broker_key="x" * 40,
        allowed_redirects=(),
        require_audience=require_audience,
    )


@pytest.mark.asyncio
@respx.mock
async def test_invalid_token_returns_none():
    respx.get(f"{_BASE}/o/userinfo/").mock(return_value=httpx.Response(401))
    v = JheTokenVerifier(_settings())
    assert await v.verify_token("bad") is None


@pytest.mark.asyncio
@respx.mock
async def test_userinfo_transport_error_returns_none():
    # FIX 3: a transport-level failure on the userinfo call must fail closed
    # (return None -> clean 401), not raise (which would surface as a 500).
    respx.get(f"{_BASE}/o/userinfo/").mock(side_effect=httpx.ConnectError("boom"))
    v = JheTokenVerifier(_settings())
    assert await v.verify_token("AAA") is None


@pytest.mark.asyncio
@respx.mock
async def test_introspection_unavailable_falls_back_to_userinfo():
    respx.get(f"{_BASE}/o/userinfo/").mock(return_value=httpx.Response(200, json={"sub": "subjectA"}))
    respx.post(f"{_BASE}/o/introspect/").mock(return_value=httpx.Response(403))
    v = JheTokenVerifier(_settings())
    tok = await v.verify_token("AAA")
    assert isinstance(tok, JheAccessToken)
    assert tok.token == "AAA"
    assert tok.subject == "subjectA"
    assert tok.client_id == "jhe-mcp-client"


@pytest.mark.asyncio
@respx.mock
async def test_introspection_unavailable_rejected_when_audience_required():
    # MCP_REQUIRE_AUDIENCE=true (production): if introspection can't confirm the
    # audience, fail closed instead of falling back to userinfo-only.
    respx.get(f"{_BASE}/o/userinfo/").mock(return_value=httpx.Response(200, json={"sub": "subjectA"}))
    respx.post(f"{_BASE}/o/introspect/").mock(return_value=httpx.Response(403))
    v = JheTokenVerifier(_settings(require_audience=True))
    assert await v.verify_token("AAA") is None


@pytest.mark.asyncio
@respx.mock
async def test_active_introspection_accepts_matching_client():
    respx.get(f"{_BASE}/o/userinfo/").mock(return_value=httpx.Response(200, json={"sub": "subjectA"}))
    respx.post(f"{_BASE}/o/introspect/").mock(
        return_value=httpx.Response(200, json={"active": True, "client_id": "jhe-mcp-client"})
    )
    v = JheTokenVerifier(_settings())
    tok = await v.verify_token("AAA")
    assert tok is not None
    assert tok.client_id == "jhe-mcp-client"


@pytest.mark.asyncio
@respx.mock
async def test_foreign_audience_token_rejected():
    """A valid token issued to a DIFFERENT client must be rejected."""
    respx.get(f"{_BASE}/o/userinfo/").mock(return_value=httpx.Response(200, json={"sub": "subjectA"}))
    respx.post(f"{_BASE}/o/introspect/").mock(
        return_value=httpx.Response(200, json={"active": True, "client_id": "some-other-app"})
    )
    v = JheTokenVerifier(_settings())
    assert await v.verify_token("AAA") is None


@pytest.mark.asyncio
@respx.mock
async def test_inactive_introspection_rejected():
    respx.get(f"{_BASE}/o/userinfo/").mock(return_value=httpx.Response(200, json={"sub": "subjectA"}))
    respx.post(f"{_BASE}/o/introspect/").mock(return_value=httpx.Response(200, json={"active": False}))
    v = JheTokenVerifier(_settings())
    assert await v.verify_token("AAA") is None
