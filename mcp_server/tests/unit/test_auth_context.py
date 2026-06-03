import pytest
from jhe_mcp.auth.context import AuthContext, current_auth, set_current_auth


def test_auth_context_fields():
    ctx = AuthContext(bearer_token="abc", subject="user-1", expires_at=1234567890)
    assert ctx.bearer_token == "abc"
    assert ctx.subject == "user-1"
    assert ctx.expires_at == 1234567890


def test_current_auth_returns_none_when_unset():
    assert current_auth() is None


def test_set_and_get_current_auth():
    ctx = AuthContext(bearer_token="t", subject="u", expires_at=0)
    token = set_current_auth(ctx)
    try:
        assert current_auth() is ctx
    finally:
        from jhe_mcp.auth.context import _current

        _current.reset(token)
    assert current_auth() is None


def test_current_auth_required_raises_when_none():
    from jhe_mcp.auth.context import current_auth_required

    with pytest.raises(RuntimeError, match="No authenticated context"):
        current_auth_required()
