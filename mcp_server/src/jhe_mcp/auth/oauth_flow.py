from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import http.server
import logging
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

from jhe_mcp.auth.token_cache import CachedToken, TokenCache
from jhe_mcp.config import Settings

logger = logging.getLogger("jhe_mcp.auth")


class AuthenticationRequired(Exception):
    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(url)


class TokenExchangeError(Exception):
    """JHE's token endpoint returned a non-2xx response.

    Carries the OAuth ``error``/``error_description`` so a failure is
    diagnosable (e.g. ``invalid_grant`` = re-login, vs a transport blip) instead
    of collapsing into a bare status code.
    """

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"token endpoint {status}: {detail}")


@dataclass(frozen=True)
class PkcePair:
    code_verifier: str
    code_challenge: str


def generate_pkce_pair() -> PkcePair:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return PkcePair(code_verifier=verifier, code_challenge=challenge)


def build_authorize_url(
    *,
    authorize_endpoint: str,
    client_id: str,
    redirect_uri: str,
    pkce: PkcePair,
    state: str,
    scope: str = "openid",
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "code_challenge": pkce.code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{authorize_endpoint}?{urllib.parse.urlencode(params)}"


async def _post_token_endpoint(
    token_endpoint: str,
    client_id: str,
    client_secret: str | None,
    grant_fields: dict[str, str],
    timeout: float = 10.0,
) -> dict[str, Any]:
    data = {"client_id": client_id, **grant_fields}
    if client_secret:
        data["client_secret"] = client_secret
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(token_endpoint, data=data)
        if resp.status_code >= 400:
            try:
                body = resp.json()
                detail = body.get("error_description") or body.get("error") or ""
            except ValueError:
                detail = resp.text[:200]
            raise TokenExchangeError(resp.status_code, detail)
        return resp.json()


async def exchange_code_for_tokens(
    *,
    token_endpoint: str,
    client_id: str,
    client_secret: str | None,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    timeout: float = 10.0,
) -> dict[str, Any]:
    return await _post_token_endpoint(
        token_endpoint,
        client_id,
        client_secret,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
        timeout,
    )


async def refresh_access_token(
    *,
    token_endpoint: str,
    client_id: str,
    client_secret: str | None,
    refresh_token: str,
    timeout: float = 10.0,
) -> dict[str, Any]:
    return await _post_token_endpoint(
        token_endpoint,
        client_id,
        client_secret,
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout,
    )


_listener_lock = threading.Lock()
_active_listener: threading.Thread | None = None
_active_url: str | None = None


def _start_callback_listener(
    redirect_uri: str,
    settings: Settings,
    pkce: PkcePair,
    state: str,
    cache: TokenCache,
) -> threading.Thread:
    parsed = urllib.parse.urlparse(redirect_uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or 8765
    completed = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            qs = urllib.parse.urlparse(self.path).query
            params = dict(urllib.parse.parse_qsl(qs))
            if "code" not in params and "error" not in params:
                self.send_response(404)
                self.end_headers()
                return
            if "code" in params and params.get("state") == state:
                try:
                    tokens = asyncio.run(
                        exchange_code_for_tokens(
                            token_endpoint=settings.token_endpoint,
                            client_id=settings.jhe_client_id,
                            client_secret=settings.jhe_client_secret,
                            code=params["code"],
                            redirect_uri=redirect_uri,
                            code_verifier=pkce.code_verifier,
                        )
                    )
                    cached = CachedToken(
                        access_token=tokens["access_token"],
                        refresh_token=tokens.get("refresh_token"),
                        expires_at=int(time.time()) + int(tokens.get("expires_in", 3600)),
                    )
                    cache.save(cached)
                    logger.info("Token exchange complete, cached successfully")
                except (TokenExchangeError, httpx.HTTPError, KeyError, ValueError) as exc:
                    # Expected failure modes: surface *why* (so the user/log can
                    # tell "session expired, re-login" from a transport blip)
                    # rather than a generic "try again" that loops silently.
                    reason = getattr(exc, "detail", None) or str(exc) or type(exc).__name__
                    logger.error("Token exchange failed in callback: %s", reason)
                    self._send_login_failed(reason)
                    completed.set()
                    return
                except Exception:
                    # Never leave the waiting main thread hung: always signal done.
                    logger.exception("Unexpected error in OAuth callback")
                    self._send_login_failed("Unexpected error during login.")
                    completed.set()
                    return
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>JHE login complete</h1>"
                b"You may close this window and retry your request.</body></html>"
            )
            completed.set()

        def _send_login_failed(self, reason: str) -> None:
            self.send_response(500)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            body = (
                f"<html><body><h1>Login failed</h1><p>{html.escape(reason)}</p>"
                "<p>Please retry your request.</p></body></html>"
            )
            self.wfile.write(body.encode())

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer((host, port), Handler)

    def serve() -> None:
        while not completed.is_set():
            server.handle_request()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread


def start_auth_flow(settings: Settings, cache: TokenCache) -> str:
    global _active_listener, _active_url
    with _listener_lock:
        if _active_listener is not None and _active_listener.is_alive():
            return _active_url  # type: ignore[return-value]
        pair = generate_pkce_pair()
        state = secrets.token_urlsafe(16)
        url = build_authorize_url(
            authorize_endpoint=settings.authorize_endpoint,
            client_id=settings.jhe_client_id,
            redirect_uri=settings.redirect_uri,
            pkce=pair,
            state=state,
        )
        _active_listener = _start_callback_listener(
            settings.redirect_uri,
            settings,
            pair,
            state,
            cache,
        )
        _active_url = url
        return url
