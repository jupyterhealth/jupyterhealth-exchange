"""Proof tests for the MCP session auth-isolation fix.

These drive the REAL MCP Streamable HTTP protocol through the FastAPI app
(``initialize`` -> capture ``Mcp-Session-Id`` -> ``notifications/initialized``
-> ``tools/call``) over an httpx ASGI transport.

Background (the bug being fixed): auth used to be done once, in a FastAPI
middleware, and snapshotted into a contextvar at session-init time. The SDK runs
each session's tools in a long-lived task started at ``initialize``, so every
tool call forwarded the *initialize-time* token (stale). Anyone with a victim's
``Mcp-Session-Id`` plus any valid bearer could execute as the victim.

The fix wires the SDK's resource-server auth: ``JheTokenVerifier`` verifies the
bearer on *every* request and the tool reads the per-request token via
``get_access_token()``. So a tool call now forwards the *caller's* token, not the
init-time one. (The MCP SDK 1.27.1 does not subject-bind sessions, so the
observable fix here is "per-request token honored": A's identity no longer leaks.)
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import httpx
import pytest
from jhe_mcp.config import Settings
from jhe_mcp.server_http import build_app

# Map test tokens -> subjects, mirroring distinct authenticated principals.
_TOKEN_TO_SUBJECT = {"AAA": "subjectA", "BBB": "subjectB"}

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
    # Clear any client secret so introspection runs without basic auth.
    monkeypatch.delenv("JHE_CLIENT_SECRET", raising=False)
    return Settings.from_env()


@pytest.fixture
def captured_bearers(monkeypatch):
    """Capture the bearer each tool call forwards to JHE, and stub validation.

    - ``UserinfoValidator.verify`` maps AAA->subjectA, BBB->subjectB (else reject).
    - Introspection is forced "unavailable" so the verifier falls back to
      userinfo-only (audience can't be enforced in this test).
    - ``JheClient._get`` is replaced so no real network call happens and the
      forwarded Authorization bearer is recorded.
    """
    bearers: list[str] = []

    from jhe_mcp.auth.userinfo import TokenValidationError, UserinfoValidator

    async def fake_verify(self, token: str) -> str:
        subject = _TOKEN_TO_SUBJECT.get(token)
        if subject is None:
            raise TokenValidationError("token rejected by userinfo endpoint")
        return subject

    monkeypatch.setattr(UserinfoValidator, "verify", fake_verify)

    from jhe_mcp.auth.token_verifier import JheTokenVerifier

    async def fake_introspect(self, token: str):
        return None  # introspection unavailable -> userinfo-only fallback

    monkeypatch.setattr(JheTokenVerifier, "_introspect_client_id", fake_introspect)

    from jhe_mcp.fhir.client import JheClient, _per_request_bearer

    async def fake_get(self, path, params, treat_404_as_none):
        # Capture exactly the bearer the real client would forward to JHE.
        bearers.append(_per_request_bearer())
        # Shape matches what get_study_count expects.
        return {"count": 0}

    monkeypatch.setattr(JheClient, "_get", fake_get)
    return bearers


def _parse_sse_or_json(resp: httpx.Response) -> dict:
    """Return the JSON-RPC payload from either a JSON or SSE response body."""
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:") :].strip())
        raise AssertionError(f"no SSE data line in: {resp.text!r}")
    return resp.json()


async def _initialize(client: httpx.AsyncClient, token: str) -> str:
    """Run initialize + notifications/initialized; return the Mcp-Session-Id."""
    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "proof-test", "version": "0"},
        },
    }
    resp = await client.post(
        "/mcp",
        json=init_req,
        headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, f"initialize failed: {resp.status_code} {resp.text[:200]}"
    session_id = resp.headers["mcp-session-id"]
    # Send notifications/initialized to complete the handshake.
    notif = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    resp2 = await client.post(
        "/mcp",
        json=notif,
        headers={
            **MCP_HEADERS,
            "Authorization": f"Bearer {token}",
            "Mcp-Session-Id": session_id,
        },
    )
    assert resp2.status_code in (200, 202), f"initialized failed: {resp2.status_code} {resp2.text[:200]}"
    return session_id


async def _tools_call(
    client: httpx.AsyncClient,
    session_id: str,
    token: str,
) -> httpx.Response:
    call_req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "get_study_count", "arguments": {}},
    }
    return await client.post(
        "/mcp",
        json=call_req,
        headers={
            **MCP_HEADERS,
            "Authorization": f"Bearer {token}",
            "Mcp-Session-Id": session_id,
        },
    )


@contextlib.asynccontextmanager
async def _app_client(settings):
    """Lifespan + ASGI client, entered/exited in the same task to keep the

    SDK session manager's anyio task group well-behaved during teardown.
    """
    app = build_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


@pytest.mark.asyncio
async def test_happy_path_forwards_callers_own_token(settings, captured_bearers):
    """init+call with AAA forwards AAA; init+call with BBB forwards BBB."""
    async with _app_client(settings) as client:
        sid_a = await _initialize(client, "AAA")
        resp_a = await _tools_call(client, sid_a, "AAA")
        assert resp_a.status_code == 200, resp_a.text[:300]
        assert captured_bearers[-1] == "AAA"

        sid_b = await _initialize(client, "BBB")
        resp_b = await _tools_call(client, sid_b, "BBB")
        assert resp_b.status_code == 200, resp_b.text[:300]
        assert captured_bearers[-1] == "BBB"


@pytest.mark.asyncio
async def test_s1_mismatched_token_does_not_forward_init_token(settings, captured_bearers):
    """S1: init with AAA, then tools/call with BBB on that session.

    MUST forward BBB (per-request token honored) OR be rejected. It MUST NOT
    forward AAA (the original stale-token leak).
    """
    async with _app_client(settings) as client:
        sid_a = await _initialize(client, "AAA")
        captured_bearers.clear()

        resp = await _tools_call(client, sid_a, "BBB")

    if resp.status_code == 200:
        # Per-request token honored: the call forwarded BBB, not AAA.
        assert captured_bearers, "expected a tool call to have forwarded a bearer"
        assert captured_bearers[-1] == "BBB", f"leaked stale token: {captured_bearers}"
        assert "AAA" not in captured_bearers, f"stale init-time token AAA was forwarded: {captured_bearers}"
    else:
        # Or the request was rejected outright.
        assert resp.status_code in (401, 403), resp.status_code
        assert "AAA" not in captured_bearers, f"stale init-time token AAA was forwarded: {captured_bearers}"


@pytest.mark.asyncio
async def test_s4_attacker_token_on_victim_session_not_executed_as_victim(settings, captured_bearers):
    """S4: tools/call on session-A's id using attacker's valid token (BBB).

    MUST be rejected OR execute as the attacker (forward BBB) — never return
    A's data by forwarding A's token (AAA).
    """
    async with _app_client(settings) as client:
        sid_a = await _initialize(client, "AAA")
        captured_bearers.clear()

        resp = await _tools_call(client, sid_a, "BBB")

    assert "AAA" not in captured_bearers, f"S4 LEAK: victim token AAA forwarded for attacker call: {captured_bearers}"
    if resp.status_code == 200:
        assert captured_bearers[-1] == "BBB", captured_bearers
    else:
        assert resp.status_code in (401, 403), resp.status_code


@pytest.mark.asyncio
async def test_per_request_bearer_is_task_local_under_concurrency():
    """Interleaved concurrent requests each read their OWN bearer, not a shared one.

    This targets the heart of the original bug: the forwarded token must come
    from per-request, task-local state — not a module global / snapshot that a
    second interleaved request could overwrite. Two tasks each install their own
    request context, synchronize at a barrier so both are live simultaneously,
    then read ``_per_request_bearer()``; each must see only its own token.

    (The full Streamable-HTTP protocol isn't driven concurrently here because the
    in-process ASGI test transport serializes requests; this exercises the
    isolation primitive the fix relies on directly.)
    """
    from types import SimpleNamespace

    from jhe_mcp.fhir.client import _per_request_bearer
    from mcp.server.lowlevel.server import request_ctx

    def _ctx(token: str) -> SimpleNamespace:
        access_token = SimpleNamespace(token=token)
        return SimpleNamespace(request=SimpleNamespace(user=SimpleNamespace(access_token=access_token)))

    barrier = asyncio.Barrier(2)

    async def worker(token: str) -> str:
        request_ctx.set(_ctx(token))  # task-local; tasks each get their own context copy
        await barrier.wait()  # ensure both contexts are installed before either reads
        return _per_request_bearer()

    results = await asyncio.gather(worker("AAA"), worker("BBB"))
    assert sorted(results) == ["AAA", "BBB"]  # neither task saw the other's token


@pytest.mark.asyncio
async def test_unauthenticated_tools_call_rejected(settings, captured_bearers):
    """A tool call with no bearer is rejected by the SDK before reaching the tool."""
    async with _app_client(settings) as client:
        sid_a = await _initialize(client, "AAA")
        captured_bearers.clear()
        resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "get_study_count", "arguments": {}},
            },
            headers={**MCP_HEADERS, "Mcp-Session-Id": sid_a},
        )
    assert resp.status_code == 401, resp.text[:200]
    assert captured_bearers == []
