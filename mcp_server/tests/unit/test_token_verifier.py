from __future__ import annotations

import httpx
import pytest
import respx
from jhe_mcp.auth.token_verifier import JheAccessToken, JheTokenVerifier
from jhe_mcp.auth.upstream import UpstreamAuthError
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
async def test_userinfo_transport_error_raises_upstream_error():
    # #774: JHE being unreachable means the token was never checked, so it must
    # surface as a 503 — not a 401 telling the client to discard a valid token.
    respx.get(f"{_BASE}/o/userinfo/").mock(side_effect=httpx.ConnectError("boom"))
    v = JheTokenVerifier(_settings())
    with pytest.raises(UpstreamAuthError) as exc_info:
        await v.verify_token("AAA")
    assert exc_info.value.status_code == 503


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
async def test_introspection_transport_error_raises_503_when_audience_required():
    # MCP_REQUIRE_AUDIENCE=true: the audience could not be checked because JHE is
    # unreachable — transient, so ask the client to retry rather than re-auth.
    respx.get(f"{_BASE}/o/userinfo/").mock(return_value=httpx.Response(200, json={"sub": "subjectA"}))
    respx.post(f"{_BASE}/o/introspect/").mock(side_effect=httpx.ConnectError("boom"))
    v = JheTokenVerifier(_settings(require_audience=True))
    with pytest.raises(UpstreamAuthError) as exc_info:
        await v.verify_token("AAA")
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
@respx.mock
async def test_introspection_forbidden_raises_500_when_audience_required():
    # 403/404 means the broker app lacks introspection rights or the URL is wrong:
    # permanent misconfiguration, so retrying will not help.
    respx.get(f"{_BASE}/o/userinfo/").mock(return_value=httpx.Response(200, json={"sub": "subjectA"}))
    respx.post(f"{_BASE}/o/introspect/").mock(return_value=httpx.Response(403))
    v = JheTokenVerifier(_settings(require_audience=True))
    with pytest.raises(UpstreamAuthError) as exc_info:
        await v.verify_token("AAA")
    assert exc_info.value.status_code == 500


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


@pytest.mark.asyncio
@respx.mock
async def test_introspection_non_object_body_raises_500_when_audience_required():
    """JSON that parses but is not an object must not escape as an AttributeError."""
    respx.get(f"{_BASE}/o/userinfo/").mock(return_value=httpx.Response(200, json={"sub": "subjectA"}))
    respx.post(f"{_BASE}/o/introspect/").mock(return_value=httpx.Response(200, json=["nope"]))
    v = JheTokenVerifier(_settings(require_audience=True))
    with pytest.raises(UpstreamAuthError) as exc_info:
        await v.verify_token("AAA")
    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
@respx.mock
async def test_introspection_non_object_body_falls_back_in_dev():
    respx.get(f"{_BASE}/o/userinfo/").mock(return_value=httpx.Response(200, json={"sub": "subjectA"}))
    respx.post(f"{_BASE}/o/introspect/").mock(return_value=httpx.Response(200, json=["nope"]))
    v = JheTokenVerifier(_settings())
    tok = await v.verify_token("AAA")
    assert tok is not None
    assert tok.client_id == "jhe-mcp-client"
