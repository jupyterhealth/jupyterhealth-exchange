import pytest
import respx
from httpx import Response
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
async def test_verify_missing_sub_raises():
    with respx.mock() as router:
        router.get("http://jhe/o/userinfo/").mock(return_value=Response(200, json={}))
        v = UserinfoValidator(userinfo_endpoint="http://jhe/o/userinfo/")
        with pytest.raises(TokenValidationError, match="missing 'sub'"):
            await v.verify("tok")


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
