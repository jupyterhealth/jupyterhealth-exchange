import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from httpx import Response
from jhe_mcp.auth.oauth_flow import (
    PkcePair,
    TokenExchangeError,
    build_authorize_url,
    exchange_code_for_tokens,
    generate_pkce_pair,
)


def test_generate_pkce_pair_lengths():
    pair = generate_pkce_pair()
    assert 43 <= len(pair.code_verifier) <= 128
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(pair.code_verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert pair.code_challenge == expected


def test_build_authorize_url():
    pair = PkcePair(code_verifier="v", code_challenge="c")
    url = build_authorize_url(
        authorize_endpoint="http://jhe/o/authorize/",
        client_id="abc",
        redirect_uri="http://localhost:8765/callback",
        pkce=pair,
        scope="openid",
        state="xyz",
    )
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    assert q["client_id"] == ["abc"]
    assert q["redirect_uri"] == ["http://localhost:8765/callback"]
    assert q["response_type"] == ["code"]
    assert q["code_challenge"] == ["c"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["scope"] == ["openid"]
    assert q["state"] == ["xyz"]


@pytest.mark.asyncio
async def test_exchange_code_for_tokens_posts_form():
    with respx.mock(assert_all_called=True) as router:
        route = router.post("http://jhe/o/token/").mock(
            return_value=Response(
                200,
                json={
                    "access_token": "a",
                    "refresh_token": "r",
                    "expires_in": 3600,
                    "id_token": "id-jwt",
                    "token_type": "Bearer",
                },
            )
        )
        tokens = await exchange_code_for_tokens(
            token_endpoint="http://jhe/o/token/",
            client_id="abc",
            client_secret=None,
            code="code-123",
            redirect_uri="http://localhost:8765/callback",
            code_verifier="v",
        )
        assert tokens["access_token"] == "a"
        assert route.called
        sent = route.calls[0].request
        body = sent.content.decode()
        assert "grant_type=authorization_code" in body
        assert "code=code-123" in body
        assert "code_verifier=v" in body
        assert "client_id=abc" in body


@pytest.mark.asyncio
async def test_exchange_code_surfaces_oauth_error_detail():
    # A non-2xx from the token endpoint must surface the OAuth error reason,
    # not collapse into a bare status code.
    with respx.mock(assert_all_called=True) as router:
        router.post("http://jhe/o/token/").mock(
            return_value=Response(400, json={"error": "invalid_grant", "error_description": "code expired"})
        )
        with pytest.raises(TokenExchangeError) as exc:
            await exchange_code_for_tokens(
                token_endpoint="http://jhe/o/token/",
                client_id="abc",
                client_secret=None,
                code="bad",
                redirect_uri="http://localhost:8765/callback",
                code_verifier="v",
            )
        assert exc.value.status == 400
        assert "code expired" in exc.value.detail
