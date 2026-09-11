"""A failure to *check* a bearer token must surface as a 5xx, not a 401 (#774).

Drives the real Streamable HTTP endpoint, so the assertions cover what a client
actually receives — above all the absence of the ``WWW-Authenticate`` challenge.
"""

from __future__ import annotations

import contextlib

import httpx
import pytest
from jhe_mcp.auth.upstream import UpstreamAuthError, UpstreamAuthErrorMiddleware
from jhe_mcp.auth.userinfo import TokenValidationError, UserinfoValidator
from jhe_mcp.config import Settings
from jhe_mcp.server_http import build_app

MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


@pytest.fixture
def settings(monkeypatch) -> Settings:
    monkeypatch.setenv("JHE_BASE_URL", "https://jhe.example.test")
    monkeypatch.setenv("JHE_CLIENT_ID", "jhe-mcp-client")
    monkeypatch.setenv("MCP_BROKER_KEY", "x" * 40)
    monkeypatch.setenv("MCP_RESOURCE_URL", "http://testserver")
    monkeypatch.delenv("JHE_CLIENT_SECRET", raising=False)
    return Settings.from_env()


@pytest.fixture
def failing_verify(monkeypatch):
    """Make ``UserinfoValidator.verify`` raise whatever the test installs."""

    def _install(exc: Exception) -> None:
        async def fake_verify(self, token: str) -> str:
            raise exc

        monkeypatch.setattr(UserinfoValidator, "verify", fake_verify)

    return _install


@contextlib.asynccontextmanager
async def _app_client(settings):
    """Lifespan + ASGI client in one task, to keep the SDK's anyio task group well-behaved."""
    app = build_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


async def _initialize(client: httpx.AsyncClient) -> httpx.Response:
    return await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "upstream-error-test", "version": "0"},
            },
        },
        headers={**MCP_HEADERS, "Authorization": "Bearer AAA"},
    )


@pytest.mark.asyncio
async def test_transient_upstream_failure_returns_503(settings, failing_verify):
    failing_verify(UpstreamAuthError("userinfo endpoint unreachable", status_code=503))
    async with _app_client(settings) as client:
        resp = await _initialize(client)
    assert resp.status_code == 503, resp.text[:200]
    assert resp.json()["error"] == "temporarily_unavailable"
    assert resp.headers["retry-after"] == "30"
    assert "www-authenticate" not in resp.headers


@pytest.mark.asyncio
async def test_misconfigured_upstream_returns_500(settings, failing_verify):
    failing_verify(UpstreamAuthError("userinfo returned 404", status_code=500))
    async with _app_client(settings) as client:
        resp = await _initialize(client)
    assert resp.status_code == 500, resp.text[:200]
    assert resp.json()["error"] == "server_error"
    assert "retry-after" not in resp.headers
    assert "www-authenticate" not in resp.headers


@pytest.mark.asyncio
async def test_rejected_token_still_returns_401(settings, failing_verify):
    """Regression guard: the SDK's own 401 path is untouched."""
    failing_verify(TokenValidationError("token rejected by userinfo endpoint"))
    async with _app_client(settings) as client:
        resp = await _initialize(client)
    assert resp.status_code == 401, resp.text[:200]
    assert "www-authenticate" in resp.headers


@pytest.mark.asyncio
async def test_non_http_scope_is_not_turned_into_a_response():
    """Only HTTP scopes can carry a 5xx; anything else must propagate untouched."""

    async def app(scope, receive, send):
        raise UpstreamAuthError("boom")

    with pytest.raises(UpstreamAuthError):
        await UpstreamAuthErrorMiddleware(app)({"type": "lifespan"}, None, None)


@pytest.mark.asyncio
async def test_upstream_failure_is_logged(settings, failing_verify, caplog):
    """An outage must leave a server-side trace; the 401 it replaced used to log one."""
    failing_verify(UpstreamAuthError("userinfo returned 502", status_code=503))
    with caplog.at_level("ERROR", logger="jhe_mcp.auth.upstream"):
        async with _app_client(settings) as client:
            await _initialize(client)
    assert "userinfo returned 502" in caplog.text
