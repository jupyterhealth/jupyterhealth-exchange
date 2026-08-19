import pytest
import respx
from jhe_mcp.fhir import capabilities as caps_mod
from jhe_mcp.fhir.capabilities import (
    ServerCapabilities,
    check_search_support,
    get_capabilities,
    preflight_observation_dates,
)


def _statement():
    return {
        "resourceType": "CapabilityStatement",
        "fhirVersion": "5.0.0",
        "rest": [
            {
                "mode": "server",
                "resource": [
                    {
                        "type": "Patient",
                        "interaction": [
                            {"code": "read"},
                            {"code": "search-type", "extension": [{"url": "x", "valueCode": "SHALL"}]},
                        ],
                        "searchParam": [
                            {"name": "birthdate", "type": "date"},
                            {"name": "family", "type": "string"},
                        ],
                    },
                    {
                        "type": "Observation",
                        "interaction": [{"code": "create"}],
                        "searchParam": [{"name": "date", "type": "date"}],
                    },
                ],
            }
        ],
    }


@pytest.fixture(autouse=True)
def clear_cache():
    caps_mod._cache.clear()
    yield
    caps_mod._cache.clear()


def test_parse_normalizes_interactions_and_ignores_extensions():
    caps = ServerCapabilities.from_capability_statement(_statement())
    assert caps.fhir_version == "5.0.0"
    assert caps.resources["Patient"].interactions == {"read", "search"}
    assert caps.resources["Patient"].search_params == {"birthdate": "date", "family": "string"}
    assert caps.resources["Observation"].search_params == {"date": "date"}


def test_parse_rejects_non_capability_statement():
    assert ServerCapabilities.from_capability_statement({"resourceType": "Bundle"}) is None
    assert ServerCapabilities.from_capability_statement("nope") is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_is_unauthenticated_and_parses():
    route = respx.get("http://jhe/FHIR/R5/metadata").respond(200, json=_statement())
    caps = await caps_mod.fetch_capabilities("http://jhe")
    assert caps is not None and "Patient" in caps.resources
    assert "authorization" not in route.calls.last.request.headers


@pytest.mark.asyncio
@respx.mock
async def test_fetch_absent_endpoint_yields_none():
    respx.get("http://jhe/FHIR/R5/metadata").respond(404)
    assert await caps_mod.fetch_capabilities("http://jhe") is None


@pytest.mark.asyncio
async def test_get_capabilities_caches_including_none(monkeypatch):
    calls = []

    async def fake_fetch(base_url):
        calls.append(base_url)
        return None

    monkeypatch.setattr(caps_mod, "fetch_capabilities", fake_fetch)
    now = {"t": 0.0}
    monkeypatch.setattr(caps_mod.time, "monotonic", lambda: now["t"])
    assert await get_capabilities("http://jhe") is None
    assert await get_capabilities("http://jhe") is None
    assert len(calls) == 1  # the miss is cached — no re-probe per tool call
    now["t"] = caps_mod.CACHE_TTL_SECONDS + 1
    await get_capabilities("http://jhe")
    assert len(calls) == 2  # expired -> refetched


def test_check_search_support_fails_open_when_unknown():
    check_search_support(None, "Patient", ["birthdate"])  # no raise


def test_check_search_support_passes_and_blocks():
    caps = ServerCapabilities.from_capability_statement(_statement())
    check_search_support(caps, "Patient", ["birthdate", "family"])  # supported -> no raise
    with pytest.raises(ValueError, match="given"):
        check_search_support(caps, "Patient", ["family", "given"])
    with pytest.raises(ValueError, match="does not serve Condition"):
        check_search_support(caps, "Condition", ["code"])


@pytest.mark.asyncio
async def test_preflight_observation_dates(monkeypatch):
    async def fake_get(base_url):
        return ServerCapabilities.from_capability_statement(_statement())

    monkeypatch.setattr(caps_mod, "get_capabilities", fake_get)
    await preflight_observation_dates("http://jhe", "2026-01-01", None)  # date supported -> ok

    async def fake_get_no_date(base_url):
        statement = _statement()
        statement["rest"][0]["resource"][1]["searchParam"] = []
        return ServerCapabilities.from_capability_statement(statement)

    monkeypatch.setattr(caps_mod, "get_capabilities", fake_get_no_date)
    with pytest.raises(ValueError, match="date"):
        await preflight_observation_dates("http://jhe", "2026-01-01", None)


@pytest.mark.asyncio
async def test_preflight_skips_fetch_without_window(monkeypatch):
    async def boom(base_url):
        raise AssertionError("must not fetch when no window is given")

    monkeypatch.setattr(caps_mod, "get_capabilities", boom)
    await preflight_observation_dates("http://jhe", None, None)
