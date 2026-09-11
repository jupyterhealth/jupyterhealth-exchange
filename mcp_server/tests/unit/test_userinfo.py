import httpx
import pytest
import respx
from httpx import Response
from jhe_mcp.auth.upstream import UpstreamAuthError
from jhe_mcp.auth.userinfo import TokenValidationError, UserinfoValidator


@pytest.mark.asyncio
async def test_verify_valid_token_returns_sub():
    with respx.mock(assert_all_called=True) as router:
        router.get("http://jhe/o/userinfo/").mock(return_value=Response(200, json={"sub": "user-1"}))
        v = UserinfoValidator(userinfo_endpoint="http://jhe/o/userinfo/")
        assert await v.verify("tok") == "user-1"


@pytest.mark.asyncio
async def test_verify_rejects_401():
    with respx.mock() as router:
        router.get("http://jhe/o/userinfo/").mock(return_value=Response(401))
        v = UserinfoValidator(userinfo_endpoint="http://jhe/o/userinfo/")
        with pytest.raises(TokenValidationError, match="rejected"):
            await v.verify("bad")


@pytest.mark.asyncio
async def test_verify_caches_subject():
    with respx.mock(assert_all_called=False) as router:
        route = router.get("http://jhe/o/userinfo/").mock(return_value=Response(200, json={"sub": "u"}))
        v = UserinfoValidator(userinfo_endpoint="http://jhe/o/userinfo/", cache_ttl=300)
        await v.verify("tok")
        await v.verify("tok")
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_verify_missing_sub_raises_upstream_500():
    """JHE answered 200 without a `sub` — JHE misanswered, so it is not the token's fault."""
    with respx.mock() as router:
        router.get("http://jhe/o/userinfo/").mock(return_value=Response(200, json={}))
        v = UserinfoValidator(userinfo_endpoint="http://jhe/o/userinfo/")
        with pytest.raises(UpstreamAuthError) as exc_info:
            await v.verify("tok")
        assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_cache_bounded_by_max_entries():
    """Inserting more than max_entries tokens keeps the cache size at or below the limit."""
    max_entries = 5
    with respx.mock(assert_all_called=False) as router:
        router.get("http://jhe/o/userinfo/").mock(return_value=Response(200, json={"sub": "u"}))
        v = UserinfoValidator(
            userinfo_endpoint="http://jhe/o/userinfo/",
            cache_ttl=300,
            max_entries=max_entries,
        )
        for i in range(max_entries + 3):
            await v.verify(f"token-{i}")
        assert len(v._cache) <= max_entries


@pytest.mark.asyncio
async def test_expired_cache_entry_is_evicted_on_read():
    """An expired cache entry is removed and the endpoint is re-called."""
    with respx.mock(assert_all_called=False) as router:
        route = router.get("http://jhe/o/userinfo/").mock(return_value=Response(200, json={"sub": "u"}))
        v = UserinfoValidator(userinfo_endpoint="http://jhe/o/userinfo/", cache_ttl=0)
        await v.verify("tok")
        # cache_ttl=0 means every entry is immediately expired
        await v.verify("tok")
        assert route.call_count == 2


@pytest.mark.asyncio
async def test_verify_transport_error_raises_upstream_503():
    with respx.mock() as router:
        router.get("http://jhe/o/userinfo/").mock(side_effect=httpx.ConnectError("boom"))
        v = UserinfoValidator(userinfo_endpoint="http://jhe/o/userinfo/")
        with pytest.raises(UpstreamAuthError) as exc_info:
            await v.verify("tok")
        assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_verify_server_error_raises_upstream_503():
    with respx.mock() as router:
        router.get("http://jhe/o/userinfo/").mock(return_value=Response(502))
        v = UserinfoValidator(userinfo_endpoint="http://jhe/o/userinfo/")
        with pytest.raises(UpstreamAuthError) as exc_info:
            await v.verify("tok")
        assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_verify_not_found_raises_upstream_500():
    with respx.mock() as router:
        router.get("http://jhe/o/userinfo/").mock(return_value=Response(404))
        v = UserinfoValidator(userinfo_endpoint="http://jhe/o/userinfo/")
        with pytest.raises(UpstreamAuthError) as exc_info:
            await v.verify("tok")
        assert exc_info.value.status_code == 500


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"text": "<html>nope</html>"}, {"json": ["not", "an", "object"]}, {"json": {"sub": 123}}])
async def test_verify_unusable_body_raises_upstream_500(body):
    """A proxy page, or any body that is not an object with a string `sub`, must not escape."""
    with respx.mock() as router:
        router.get("http://jhe/o/userinfo/").mock(return_value=Response(200, **body))
        v = UserinfoValidator(userinfo_endpoint="http://jhe/o/userinfo/")
        with pytest.raises(UpstreamAuthError) as exc_info:
            await v.verify("tok")
        assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_verify_throttled_raises_upstream_503():
    """429 is transient, so it belongs with the retryable statuses, not misconfiguration."""
    with respx.mock() as router:
        router.get("http://jhe/o/userinfo/").mock(return_value=Response(429))
        v = UserinfoValidator(userinfo_endpoint="http://jhe/o/userinfo/")
        with pytest.raises(UpstreamAuthError) as exc_info:
            await v.verify("tok")
        assert exc_info.value.status_code == 503
