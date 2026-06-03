from unittest.mock import MagicMock, patch

import pytest
from jhe_mcp.auth.context import AuthContext, _current, set_current_auth
from jhe_mcp.auth.oauth_flow import AuthenticationRequired, start_auth_flow
from jhe_mcp.auth.token_cache import TokenCacheMiss
from jhe_mcp.config import Settings


@pytest.fixture
def settings():
    return Settings(
        jhe_base_url="http://jhe",
        jhe_client_id="test-client",
        jhe_client_secret=None,
        redirect_uri="http://localhost:8765/callback",
        authorize_endpoint="http://jhe/o/authorize/",
        token_endpoint="http://jhe/o/token/",
        userinfo_endpoint="http://jhe/o/userinfo/",
        mcp_resource_url="https://jhe-mcp.fly.dev",
        broker_key=None,
        allowed_redirects=(),
    )


@pytest.fixture
def cache():
    return MagicMock()


@pytest.fixture(autouse=True)
def _reset_listener():
    import jhe_mcp.auth.oauth_flow as mod

    mod._active_listener = None
    mod._active_url = None
    yield
    mod._active_listener = None
    mod._active_url = None


@pytest.fixture
def auth():
    token = set_current_auth(AuthContext(bearer_token="t", subject="u", expires_at=0))
    yield
    _current.reset(token)


def test_start_auth_flow_returns_url(settings, cache):
    with patch("jhe_mcp.auth.oauth_flow._start_callback_listener") as mock_listener:
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        mock_listener.return_value = mock_thread
        url = start_auth_flow(settings, cache)
    assert "response_type=code" in url
    assert "client_id=test-client" in url
    assert url.startswith("http://jhe/o/authorize/")


def test_start_auth_flow_reuses_active_listener(settings, cache):
    with patch("jhe_mcp.auth.oauth_flow._start_callback_listener") as mock_listener:
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        mock_listener.return_value = mock_thread
        url1 = start_auth_flow(settings, cache)
        url2 = start_auth_flow(settings, cache)
    assert url1 == url2
    assert mock_listener.call_count == 1


def test_start_auth_flow_restarts_dead_listener(settings, cache):
    with patch("jhe_mcp.auth.oauth_flow._start_callback_listener") as mock_listener:
        dead_thread = MagicMock()
        dead_thread.is_alive.return_value = False
        alive_thread = MagicMock()
        alive_thread.is_alive.return_value = True
        mock_listener.side_effect = [dead_thread, alive_thread]
        url1 = start_auth_flow(settings, cache)
        import jhe_mcp.auth.oauth_flow as mod

        mod._active_listener = dead_thread
        url2 = start_auth_flow(settings, cache)
    assert mock_listener.call_count == 2
    assert url1 != url2


@pytest.mark.asyncio
async def test_ensure_auth_raises_when_no_token(settings, cache):
    cache_mock = MagicMock()
    cache_mock.load.side_effect = TokenCacheMiss()

    with patch("jhe_mcp.auth.oauth_flow._start_callback_listener") as mock_listener:
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        mock_listener.return_value = mock_thread

        from jhe_mcp.auth.oauth_flow import start_auth_flow

        with pytest.raises(AuthenticationRequired) as exc_info:
            url = start_auth_flow(settings, cache_mock)
            raise AuthenticationRequired(url)
        assert "response_type=code" in exc_info.value.url


@pytest.mark.asyncio
async def test_before_catches_auth_required():
    from jhe_mcp.auth.oauth_flow import AuthenticationRequired
    from jhe_mcp.core import build_server

    async def hook_that_raises():
        raise AuthenticationRequired("http://example.com/auth")

    settings_obj = Settings(
        jhe_base_url="http://jhe",
        jhe_client_id="test",
        jhe_client_secret=None,
        redirect_uri="http://localhost:8765/callback",
        authorize_endpoint="http://jhe/o/authorize/",
        token_endpoint="http://jhe/o/token/",
        userinfo_endpoint="http://jhe/o/userinfo/",
        mcp_resource_url="https://jhe-mcp.fly.dev",
        broker_key=None,
        allowed_redirects=(),
    )
    mcp = build_server(settings_obj, pre_tool_hook=hook_that_raises)
    result = await mcp.call_tool("get_study_count", {})
    text = result[0].text if hasattr(result[0], "text") else str(result)
    assert "Authentication required" in text
    assert "http://example.com/auth" in text
