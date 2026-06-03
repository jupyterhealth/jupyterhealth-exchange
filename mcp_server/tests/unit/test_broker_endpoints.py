import dataclasses
import urllib.parse

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jhe_mcp.auth import broker_state, pkce
from jhe_mcp.auth.broker import build_broker_router
from jhe_mcp.config import Settings


def _settings() -> Settings:
    return Settings(
        jhe_base_url="https://jhe.fly.dev",
        jhe_client_id="mcp-client",
        jhe_client_secret="mcp-secret",
        redirect_uri="unused",
        authorize_endpoint="https://jhe.fly.dev/o/authorize/",
        token_endpoint="https://jhe.fly.dev/o/token/",
        userinfo_endpoint="https://jhe.fly.dev/o/userinfo/",
        mcp_resource_url="https://jhe-mcp.fly.dev",
        broker_key="unit-test-key",
        allowed_redirects=(),
    )


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(build_broker_router(_settings()))
    return TestClient(app, follow_redirects=False)


def test_protected_resource_metadata():
    r = _client().get("/.well-known/oauth-protected-resource")
    assert r.status_code == 200
    body = r.json()
    assert body["resource"] == "https://jhe-mcp.fly.dev"
    assert body["authorization_servers"] == ["https://jhe-mcp.fly.dev"]


def test_authorization_server_metadata():
    r = _client().get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    body = r.json()
    assert body["authorization_endpoint"] == "https://jhe-mcp.fly.dev/authorize"
    assert body["token_endpoint"] == "https://jhe-mcp.fly.dev/token"
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["registration_endpoint"] == "https://jhe-mcp.fly.dev/register"


def test_authorize_rejects_non_loopback_redirect():
    v = pkce.generate_verifier()
    r = _client().get(
        "/authorize",
        params={
            "response_type": "code",
            "redirect_uri": "https://evil.test/cb",
            "code_challenge": pkce.challenge_from_verifier(v),
            "code_challenge_method": "S256",
            "client_id": "llm",
        },
    )
    assert r.status_code == 400


def test_authorize_requires_pkce():
    r = _client().get(
        "/authorize",
        params={
            "response_type": "code",
            "redirect_uri": "http://localhost:9999/cb",
            "client_id": "llm",
        },
    )
    assert r.status_code == 400


def test_authorize_redirects_to_jhe_with_our_client():
    v = pkce.generate_verifier()
    r = _client().get(
        "/authorize",
        params={
            "response_type": "code",
            "redirect_uri": "http://localhost:9999/cb",
            "code_challenge": pkce.challenge_from_verifier(v),
            "code_challenge_method": "S256",
            "client_id": "llm",
            "state": "llm-state-123",
        },
    )
    assert r.status_code == 302
    loc = urllib.parse.urlparse(r.headers["location"])
    q = urllib.parse.parse_qs(loc.query)
    assert loc.netloc == "jhe.fly.dev"
    assert q["client_id"] == ["mcp-client"]
    assert q["redirect_uri"] == ["https://jhe-mcp.fly.dev/oauth/callback"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["state"][0] != "llm-state-123"


@respx.mock
def test_callback_exchanges_and_redirects_back():
    respx.post("https://jhe.fly.dev/o/token/").mock(
        return_value=httpx.Response(200, json={"access_token": "JHE-TOK", "token_type": "Bearer"})
    )
    s = _settings()
    state = broker_state.encode(
        s.broker_key,
        {
            "t": "state",
            "llm_redirect_uri": "http://localhost:9999/cb",
            "llm_state": "llm-state-123",
            "llm_code_challenge": "chal",
            "llm_client_id": "llm",
            "up_verifier": pkce.generate_verifier(),
        },
    )
    r = _client().get("/oauth/callback", params={"code": "JHE-CODE", "state": state})
    assert r.status_code == 302
    loc = urllib.parse.urlparse(r.headers["location"])
    q = urllib.parse.parse_qs(loc.query)
    assert loc.netloc == "localhost:9999"
    assert q["state"] == ["llm-state-123"]
    assert "code" in q


def test_token_authorization_code_returns_jhe_token():
    s = _settings()
    v = pkce.generate_verifier()
    mc = broker_state.encode(
        s.broker_key,
        {
            "t": "code",
            "token": {"access_token": "JHE-TOK", "token_type": "Bearer"},
            "llm_code_challenge": pkce.challenge_from_verifier(v),
            "llm_redirect_uri": "http://localhost:9999/cb",
            "llm_client_id": "llm",
        },
    )
    r = _client().post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": mc,
            "code_verifier": v,
            "redirect_uri": "http://localhost:9999/cb",
            "client_id": "llm",
        },
    )
    assert r.status_code == 200
    assert r.json()["access_token"] == "JHE-TOK"


def test_token_rejects_bad_verifier():
    s = _settings()
    v = pkce.generate_verifier()
    mc = broker_state.encode(
        s.broker_key,
        {
            "t": "code",
            "token": {"access_token": "JHE-TOK"},
            "llm_code_challenge": pkce.challenge_from_verifier(v),
            "llm_redirect_uri": "http://localhost:9999/cb",
            "llm_client_id": "llm",
        },
    )
    r = _client().post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": mc,
            "code_verifier": "WRONG",
            "redirect_uri": "http://localhost:9999/cb",
            "client_id": "llm",
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_authorize_rejects_userinfo_in_redirect():
    v = pkce.generate_verifier()
    r = _client().get(
        "/authorize",
        params={
            "response_type": "code",
            "redirect_uri": "https://evil.com@localhost/cb",
            "code_challenge": pkce.challenge_from_verifier(v),
            "code_challenge_method": "S256",
            "client_id": "llm",
        },
    )
    assert r.status_code == 400


def test_missing_broker_key_raises():
    s = dataclasses.replace(_settings(), broker_key=None)
    with pytest.raises(RuntimeError, match="MCP_BROKER_KEY"):
        build_broker_router(s)


def test_token_unsupported_grant_type():
    r = _client().post("/token", data={"grant_type": "client_credentials"})
    assert r.status_code == 400
    assert r.json()["error"] == "unsupported_grant_type"


def test_token_rejects_client_id_mismatch():
    s = _settings()
    v = pkce.generate_verifier()
    mc = broker_state.encode(
        s.broker_key,
        {
            "t": "code",
            "token": {"access_token": "JHE-TOK"},
            "llm_code_challenge": pkce.challenge_from_verifier(v),
            "llm_redirect_uri": "http://localhost:9999/cb",
            "llm_client_id": "llm",
        },
    )
    r = _client().post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": mc,
            "code_verifier": v,
            "redirect_uri": "http://localhost:9999/cb",
            "client_id": "different-client",
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_token_rejects_redirect_uri_mismatch():
    s = _settings()
    v = pkce.generate_verifier()
    mc = broker_state.encode(
        s.broker_key,
        {
            "t": "code",
            "token": {"access_token": "JHE-TOK"},
            "llm_code_challenge": pkce.challenge_from_verifier(v),
            "llm_redirect_uri": "http://localhost:9999/cb",
            "llm_client_id": "llm",
        },
    )
    r = _client().post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": mc,
            "code_verifier": v,
            "redirect_uri": "http://localhost:1111/different",
            "client_id": "llm",
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


@respx.mock
def test_callback_returns_502_on_upstream_error():
    respx.post("https://jhe.fly.dev/o/token/").mock(return_value=httpx.Response(400, text="bad"))
    s = _settings()
    state = broker_state.encode(
        s.broker_key,
        {
            "t": "state",
            "llm_redirect_uri": "http://localhost:9999/cb",
            "llm_state": None,
            "llm_code_challenge": "chal",
            "llm_client_id": "llm",
            "up_verifier": pkce.generate_verifier(),
        },
    )
    r = _client().get("/oauth/callback", params={"code": "JHE-CODE", "state": state})
    assert r.status_code == 502


def _wrapped_refresh(client_id: str, jhe_refresh: str = "JHE-RT") -> str:
    s = _settings()
    return broker_state.encode(
        s.broker_key,
        {"t": "refresh", "jhe_refresh": jhe_refresh, "client_id": client_id},
    )


@respx.mock
def test_token_refresh_proxies_to_jhe():
    route = respx.post("https://jhe.fly.dev/o/token/").mock(
        return_value=httpx.Response(200, json={"access_token": "NEW-TOK", "token_type": "Bearer"})
    )
    r = _client().post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": _wrapped_refresh("llm", "JHE-RT"),
            "client_id": "llm",
        },
    )
    assert r.status_code == 200
    assert r.json()["access_token"] == "NEW-TOK"
    # The raw JHE refresh token is forwarded upstream, never the wrapper.
    sent = urllib.parse.parse_qs(route.calls[0].request.content.decode())
    assert sent["refresh_token"] == ["JHE-RT"]


def test_token_rejects_expired_code(monkeypatch):
    from jhe_mcp.auth import broker as broker_mod

    monkeypatch.setattr(broker_mod, "CODE_TTL", -1)  # force any code to be expired
    s = _settings()
    v = pkce.generate_verifier()
    mc = broker_state.encode(
        s.broker_key,
        {
            "t": "code",
            "token": {"access_token": "JHE-TOK"},
            "llm_code_challenge": pkce.challenge_from_verifier(v),
            "llm_redirect_uri": "http://localhost:9999/cb",
            "llm_client_id": "llm",
        },
    )
    r = _client().post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": mc,
            "code_verifier": v,
            "redirect_uri": "http://localhost:9999/cb",
            "client_id": "llm",
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


@respx.mock
def test_token_refresh_normalizes_upstream_error():
    respx.post("https://jhe.fly.dev/o/token/").mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant", "secret_detail": "LEAK"})
    )
    r = _client().post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": _wrapped_refresh("llm"),
            "client_id": "llm",
        },
    )
    assert r.status_code == 400
    assert r.json() == {"error": "invalid_grant"}  # generic; upstream body NOT echoed
    assert "secret_detail" not in r.text


def test_register_issues_client_id_and_echoes_redirects():
    r = _client().post("/register", json={"redirect_uris": ["https://app.example.com/oauth/callback"]})
    assert r.status_code == 201
    body = r.json()
    assert body["client_id"]
    assert body["redirect_uris"] == ["https://app.example.com/oauth/callback"]
    assert body["token_endpoint_auth_method"] == "none"


def test_register_rejects_missing_redirects():
    assert _client().post("/register", json={}).status_code == 400


def test_register_rejects_bad_redirect_scheme():
    r = _client().post("/register", json={"redirect_uris": ["javascript:alert(1)"]})
    assert r.status_code == 400


def test_authorize_accepts_registered_https_redirect():
    c = _client()
    reg = c.post("/register", json={"redirect_uris": ["https://app.example.com/oauth/callback"]}).json()
    v = pkce.generate_verifier()
    r = c.get(
        "/authorize",
        params={
            "response_type": "code",
            "redirect_uri": "https://app.example.com/oauth/callback",
            "code_challenge": pkce.challenge_from_verifier(v),
            "code_challenge_method": "S256",
            "client_id": reg["client_id"],
        },
    )
    assert r.status_code == 302
    assert "jhe.fly.dev/o/authorize/" in r.headers["location"]


def test_authorize_rejects_redirect_not_in_registration():
    c = _client()
    reg = c.post("/register", json={"redirect_uris": ["https://app.example.com/oauth/callback"]}).json()
    v = pkce.generate_verifier()
    r = c.get(
        "/authorize",
        params={
            "response_type": "code",
            "redirect_uri": "https://evil.example.com/cb",
            "code_challenge": pkce.challenge_from_verifier(v),
            "code_challenge_method": "S256",
            "client_id": reg["client_id"],
        },
    )
    assert r.status_code == 400


def test_authorize_non_client_token_falls_back_to_loopback():
    # A signed blob whose "t" != "client" must NOT be treated as a registration;
    # it falls back to the loopback/allow-list rule.
    from jhe_mcp.auth import broker_state

    s = _settings()
    fake = broker_state.encode(s.broker_key, {"t": "state", "redirect_uris": ["https://evil.example.com/cb"]})
    v = pkce.generate_verifier()
    r = _client().get(
        "/authorize",
        params={
            "response_type": "code",
            "redirect_uri": "https://evil.example.com/cb",
            "code_challenge": pkce.challenge_from_verifier(v),
            "code_challenge_method": "S256",
            "client_id": fake,
        },
    )
    assert r.status_code == 400  # not loopback, not registered -> rejected


@respx.mock
def test_mcp_transport_accepts_production_host(monkeypatch):
    monkeypatch.setenv("JHE_BASE_URL", "https://jhe.fly.dev")
    monkeypatch.setenv("JHE_CLIENT_ID", "mcp-client")
    monkeypatch.setenv("MCP_RESOURCE_URL", "https://jhe-mcp.fly.dev")
    monkeypatch.setenv("MCP_BROKER_KEY", "unit-test-key-padded-to-32-chars!")
    respx.get("https://jhe.fly.dev/o/userinfo/").mock(return_value=httpx.Response(200, json={"sub": "user-1"}))
    # The resource-server TokenVerifier also probes JHE introspection for the
    # audience check; 403 = introspection unavailable -> userinfo-only fallback.
    respx.post("https://jhe.fly.dev/o/introspect/").mock(return_value=httpx.Response(403))
    from jhe_mcp.config import Settings
    from jhe_mcp.server_http import build_app

    app = build_app(Settings.from_env())
    with TestClient(app) as client:  # context manager runs lifespan / session manager
        r = client.post(
            "/mcp",
            headers={
                "host": "jhe-mcp.fly.dev",
                "Authorization": "Bearer good-token",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"},
                },
            },
        )
    # The key assertion: NOT a 421 host rejection. (Without FIX 1 this is 421.)
    assert r.status_code != 421


def test_build_app_serves_metadata_and_401_header(monkeypatch):
    monkeypatch.setenv("JHE_BASE_URL", "https://jhe.fly.dev")
    monkeypatch.setenv("JHE_CLIENT_ID", "mcp-client")
    monkeypatch.setenv("MCP_RESOURCE_URL", "https://jhe-mcp.fly.dev")
    monkeypatch.setenv("MCP_BROKER_KEY", "unit-test-key-padded-to-32-chars!")
    from jhe_mcp.config import Settings
    from jhe_mcp.server_http import build_app

    app = build_app(Settings.from_env())
    client = TestClient(app, follow_redirects=False)

    # discovery is served by the mounted broker router
    assert client.get("/.well-known/oauth-protected-resource").status_code == 200

    # unauthenticated /mcp returns 401 with a resource_metadata pointer
    r = client.get("/mcp")
    assert r.status_code == 401
    assert "resource_metadata" in r.headers.get("www-authenticate", "")


# FIX 1: upstream error redirect -------------------------------------------


def test_callback_error_redirects_to_client_without_token_call():
    # Valid state + error=access_denied must redirect error back to client;
    # no token endpoint should be called (no respx mock needed — a network call
    # would raise an error, and we assert 302, not 502).
    s = _settings()
    state = broker_state.encode(
        s.broker_key,
        {
            "t": "state",
            "llm_redirect_uri": "http://localhost:9999/cb",
            "llm_state": "llm-state-123",
            "llm_code_challenge": "chal",
            "llm_client_id": "llm",
            "up_verifier": pkce.generate_verifier(),
        },
    )
    r = _client().get(
        "/oauth/callback",
        params={
            "error": "access_denied",
            "error_description": "user cancelled SECRET-DETAIL",
            "state": state,
        },
    )
    assert r.status_code == 302
    loc = urllib.parse.urlparse(r.headers["location"])
    q = urllib.parse.parse_qs(loc.query)
    assert loc.netloc == "localhost:9999"
    assert q["error"] == ["access_denied"]
    assert q["state"] == ["llm-state-123"]
    assert "code" not in q
    # FIX 4: upstream error_description is never relayed.
    assert "error_description" not in q
    assert "SECRET-DETAIL" not in r.text


def test_callback_error_sanitizes_unknown_error_code():
    # FIX 4: an upstream error code outside the RFC 6749 set collapses to
    # server_error and the description is dropped.
    s = _settings()
    state = broker_state.encode(
        s.broker_key,
        {
            "t": "state",
            "llm_redirect_uri": "http://localhost:9999/cb",
            "llm_state": "llm-state-123",
            "llm_code_challenge": "chal",
            "llm_client_id": "llm",
            "up_verifier": pkce.generate_verifier(),
        },
    )
    r = _client().get(
        "/oauth/callback",
        params={
            "error": "weird_upstream_code<script>",
            "error_description": "leaky text",
            "state": state,
        },
    )
    assert r.status_code == 302
    q = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["location"]).query)
    assert q["error"] == ["server_error"]
    assert "error_description" not in q
    assert "leaky text" not in r.text


# FIX 2: domain-separated type tags ----------------------------------------


def test_token_rejects_state_blob_used_as_code():
    # A state-typed blob must not be accepted as an authorization code.
    s = _settings()
    v = pkce.generate_verifier()
    state_blob = broker_state.encode(
        s.broker_key,
        {
            "t": "state",
            "llm_redirect_uri": "http://localhost:9999/cb",
            "llm_state": "x",
            "llm_code_challenge": pkce.challenge_from_verifier(v),
            "llm_client_id": "llm",
            "up_verifier": pkce.generate_verifier(),
        },
    )
    r = _client().post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": state_blob,
            "code_verifier": v,
            "redirect_uri": "http://localhost:9999/cb",
            "client_id": "llm",
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_callback_rejects_code_blob_used_as_state():
    # A code-typed blob must not be accepted as a state param in /oauth/callback.
    s = _settings()
    v = pkce.generate_verifier()
    code_blob = broker_state.encode(
        s.broker_key,
        {
            "t": "code",
            "token": {"access_token": "JHE-TOK"},
            "llm_code_challenge": pkce.challenge_from_verifier(v),
            "llm_redirect_uri": "http://localhost:9999/cb",
            "llm_client_id": "llm",
        },
    )
    r = _client().get("/oauth/callback", params={"code": "JHE-CODE", "state": code_blob})
    assert r.status_code == 400
    assert "invalid state" in r.text


# FIX A: refresh tokens bound to the issuing client ------------------------


def test_token_authorization_code_wraps_refresh_token():
    # The authorization_code response must NOT expose the raw JHE refresh_token;
    # it is replaced with an opaque broker-issued wrapper bound to the client.
    s = _settings()
    v = pkce.generate_verifier()
    mc = broker_state.encode(
        s.broker_key,
        {
            "t": "code",
            "token": {"access_token": "JHE-TOK", "refresh_token": "RAW-JHE-RT", "token_type": "Bearer"},
            "llm_code_challenge": pkce.challenge_from_verifier(v),
            "llm_redirect_uri": "http://localhost:9999/cb",
            "llm_client_id": "llm",
        },
    )
    r = _client().post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": mc,
            "code_verifier": v,
            "redirect_uri": "http://localhost:9999/cb",
            "client_id": "llm",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"] == "JHE-TOK"
    # opaque / wrapped: not the raw value
    assert body["refresh_token"] != "RAW-JHE-RT"
    assert "RAW-JHE-RT" not in r.text
    # and it decodes to a refresh-typed blob bound to this client
    decoded = broker_state.decode(s.broker_key, body["refresh_token"], None)
    assert decoded["t"] == "refresh"
    assert decoded["client_id"] == "llm"
    assert decoded["jhe_refresh"] == "RAW-JHE-RT"


@respx.mock
def test_token_refresh_matching_client_succeeds_and_rewraps():
    respx.post("https://jhe.fly.dev/o/token/").mock(
        return_value=httpx.Response(
            200, json={"access_token": "NEW-TOK", "refresh_token": "NEW-JHE-RT", "token_type": "Bearer"}
        )
    )
    s = _settings()
    r = _client().post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": _wrapped_refresh("llm", "JHE-RT"),
            "client_id": "llm",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"] == "NEW-TOK"
    # response re-wrapped: not the raw new JHE refresh token
    assert body["refresh_token"] != "NEW-JHE-RT"
    assert "NEW-JHE-RT" not in r.text
    decoded = broker_state.decode(s.broker_key, body["refresh_token"], None)
    assert decoded["t"] == "refresh"
    assert decoded["client_id"] == "llm"
    assert decoded["jhe_refresh"] == "NEW-JHE-RT"


def test_token_refresh_client_id_mismatch_rejected():
    # A wrapped refresh token issued to "client-a" cannot be redeemed by "client-b".
    r = _client().post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": _wrapped_refresh("client-a"),
            "client_id": "client-b",
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_authorize_requires_client_id():
    # FIX 1: /authorize without client_id must be rejected (RFC 6749 §4.1.1),
    # otherwise the issued code/refresh would be bound to None.
    v = pkce.generate_verifier()
    r = _client().get(
        "/authorize",
        params={
            "response_type": "code",
            "redirect_uri": "http://localhost:9999/cb",
            "code_challenge": pkce.challenge_from_verifier(v),
            "code_challenge_method": "S256",
        },
    )
    assert r.status_code == 400
    assert "client_id required" in r.text


def test_token_refresh_rejects_empty_client_id():
    # FIX 1 (defensive): a wrapper bound to "" presented with an empty/missing
    # client_id must be rejected (no None == None / "" == "" bypass).
    r = _client().post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": _wrapped_refresh(""),
            "client_id": "",
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_token_refresh_rejects_non_refresh_typed_blob():
    # A blob whose "t" != "refresh" must not be accepted as a refresh token.
    s = _settings()
    not_refresh = broker_state.encode(
        s.broker_key,
        {"t": "code", "jhe_refresh": "x", "client_id": "llm"},
    )
    r = _client().post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": not_refresh,
            "client_id": "llm",
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


# FIX C: log hygiene -------------------------------------------------------


def test_configure_logging_quiets_httpx():
    import logging

    from jhe_mcp.server_http import configure_logging

    logging.getLogger("httpx").setLevel(logging.INFO)
    configure_logging()
    assert logging.getLogger("httpx").level == logging.WARNING
