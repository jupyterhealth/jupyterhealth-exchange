import pytest
import respx
from httpx import Response
from jhe_mcp.auth.context import AuthContext, set_current_auth
from jhe_mcp.fhir.client import JheClient, JheClientError


@pytest.fixture
def auth():
    ctx = AuthContext(bearer_token="tok", subject="u", expires_at=0)
    token = set_current_auth(ctx)
    yield ctx
    from jhe_mcp.auth.context import _current

    _current.reset(token)


@pytest.mark.asyncio
async def test_admin_get_includes_bearer(auth):
    with respx.mock(assert_all_called=True) as router:
        route = router.get("http://jhe/api/v1/studies").mock(return_value=Response(200, json={"results": []}))
        async with JheClient("http://jhe") as client:
            data = await client.admin_get("studies")
            assert data == {"results": []}
            assert route.calls[0].request.headers["authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_fhir_get_with_params(auth):
    with respx.mock(assert_all_called=True) as router:
        route = router.get("http://jhe/fhir/r5/Observation").mock(
            return_value=Response(200, json={"resourceType": "Bundle", "entry": []})
        )
        async with JheClient("http://jhe") as client:
            await client.fhir_get("Observation", params={"patient": "1"})
            assert route.calls[0].request.url.query == b"patient=1"


@pytest.mark.asyncio
async def test_404_returns_none_for_single(auth):
    with respx.mock() as router:
        router.get("http://jhe/api/v1/studies/999").mock(return_value=Response(404))
        async with JheClient("http://jhe") as client:
            assert await client.admin_get("studies/999", treat_404_as_none=True) is None


@pytest.mark.asyncio
async def test_403_raises(auth):
    with respx.mock() as router:
        router.get("http://jhe/api/v1/studies/1").mock(return_value=Response(403, json={"detail": "no perms"}))
        async with JheClient("http://jhe") as client:
            with pytest.raises(JheClientError) as exc:
                await client.admin_get("studies/1")
            assert exc.value.status == 403
            assert "no perms" in str(exc.value)


@pytest.mark.asyncio
async def test_500_retried_once_then_raises(auth):
    with respx.mock() as router:
        route = router.get("http://jhe/api/v1/studies").mock(side_effect=[Response(500), Response(500)])
        async with JheClient("http://jhe") as client:
            with pytest.raises(JheClientError):
                await client.admin_get("studies")
            assert route.call_count == 2


@pytest.mark.asyncio
async def test_429_retried_then_succeeds(auth):
    with respx.mock(assert_all_called=True) as router:
        route = router.get("http://jhe/fhir/r5/Observation").mock(
            side_effect=[Response(429), Response(200, json={"resourceType": "Bundle", "total": 0, "entry": []})]
        )
        async with JheClient("http://jhe") as client:
            data = await client.fhir_get("Observation")
            assert data["total"] == 0
            assert route.call_count == 2


@pytest.mark.asyncio
async def test_audit_log_emitted_on_success(auth, caplog):
    # FIX B: every JHE data access emits a structured audit line carrying
    # WHO (subject), WHAT (method + path), and RESULT (status).
    import logging

    with caplog.at_level(logging.INFO, logger="jhe_mcp.audit"):
        with respx.mock(assert_all_called=True) as router:
            router.get("http://jhe/api/v1/studies/30002/patients").mock(
                return_value=Response(200, json={"results": []})
            )
            async with JheClient("http://jhe") as client:
                await client.admin_get("studies/30002/patients")

    records = [r for r in caplog.records if r.name == "jhe_mcp.audit"]
    assert len(records) == 1
    rec = records[0]
    assert rec.subject == "u"
    assert rec.path == "/api/v1/studies/30002/patients"
    assert rec.method == "GET"
    assert rec.status == 200


@pytest.mark.asyncio
async def test_audit_log_emitted_on_error(auth, caplog):
    # The audit line must also be emitted on the 4xx/5xx raise path.
    import logging

    with caplog.at_level(logging.INFO, logger="jhe_mcp.audit"):
        with respx.mock() as router:
            router.get("http://jhe/api/v1/studies/1").mock(return_value=Response(403, json={"detail": "no perms"}))
            async with JheClient("http://jhe") as client:
                with pytest.raises(JheClientError):
                    await client.admin_get("studies/1")

    records = [r for r in caplog.records if r.name == "jhe_mcp.audit"]
    assert len(records) == 1
    assert records[0].status == 403
    assert records[0].path == "/api/v1/studies/1"
