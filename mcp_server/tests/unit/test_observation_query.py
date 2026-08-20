from unittest.mock import AsyncMock

import pytest
from jhe_mcp.fhir import observation_query as oq
from jhe_mcp.fhir.client import JheClientError
from jhe_mcp.fhir.observation_query import (
    build_observation_params,
    collect_observations,
    count_observations,
    fetch_observation_page,
    iter_all_observations,
)


def test_build_params_patient_and_code():
    params = build_observation_params(patient_id="7", data_type="blood-glucose")
    assert params["patient"] == "7"
    assert "omh:blood-glucose:4.0" in params["code"]
    assert "date" not in params  # no window given -> no date param


def test_build_params_emits_server_side_date_window():
    params = build_observation_params(patient_id="7", start="2026-04-01", end="2026-04-30")
    assert params["date"] == ["ge2026-04-01", "le2026-04-30"]


def test_build_params_open_ended_windows():
    assert build_observation_params(patient_id="7", start="2026-04-01")["date"] == ["ge2026-04-01"]
    assert build_observation_params(patient_id="7", end="2026-04-30")["date"] == ["le2026-04-30"]


def test_build_params_rejects_non_iso_window():
    with pytest.raises(ValueError, match="start must be an ISO date"):
        build_observation_params(patient_id="7", start="last week")
    with pytest.raises(ValueError, match="end must be an ISO date"):
        build_observation_params(patient_id="7", end="04/30/2026")
    # fromisoformat alone would accept these; the server would reject or
    # misread them, so they must fail client-side with the clear message.
    with pytest.raises(ValueError, match="start must be an ISO date"):
        build_observation_params(patient_id="7", start="20260401")
    with pytest.raises(ValueError, match="end must be an ISO date"):
        build_observation_params(patient_id="7", end="2026-W14-2")


def test_build_params_study_scope():
    params = build_observation_params(study_id="30006")
    assert params["patient._has:_group:member:_id"] == "30006"


def test_build_params_unknown_data_type_raises():
    with pytest.raises(ValueError, match="Unknown data_type"):
        build_observation_params(patient_id="7", data_type="not-a-type")


@pytest.mark.asyncio
async def test_count_observations_uses_summary_count():
    client = AsyncMock()
    client.fhir_get.return_value = {"resourceType": "Bundle", "total": 4242}
    n = await count_observations(client, {"patient": "7", "date": ["ge2026-04-01"]})
    assert n == 4242
    sent = client.fhir_get.await_args.kwargs["params"]
    assert sent["_summary"] == "count"
    assert sent["patient"] == "7"
    assert sent["date"] == ["ge2026-04-01"]  # filters ride along with the count


@pytest.mark.asyncio
async def test_fetch_page_returns_total_entries_has_more():
    client = AsyncMock()
    client.fhir_get.return_value = {"total": 130, "entry": [{"resource": {"id": "a"}}]}
    total, entries, has_more = await fetch_observation_page(client, {"patient": "7"}, page=1, page_size=50)
    assert total == 130
    assert len(entries) == 1
    assert has_more is True
    sent = client.fhir_get.await_args.kwargs["params"]
    assert sent["_count"] == 50 and sent["_page"] == 1
    assert "_sort" not in sent  # sort only sent when asked for


@pytest.mark.asyncio
async def test_fetch_page_passes_sort_through():
    client = AsyncMock()
    client.fhir_get.return_value = {"total": 1, "entry": [{"resource": {"id": "a"}}]}
    await fetch_observation_page(client, {"patient": "7"}, page=1, page_size=1, sort="-date")
    sent = client.fhir_get.await_args.kwargs["params"]
    assert sent["_sort"] == "-date"


@pytest.mark.asyncio
async def test_fetch_page_last_page_has_no_more():
    client = AsyncMock()
    client.fhir_get.return_value = {"total": 40, "entry": [{} for _ in range(40)]}
    total, entries, has_more = await fetch_observation_page(client, {"patient": "7"}, page=1, page_size=50)
    assert total == 40 and has_more is False


@pytest.mark.asyncio
async def test_iter_all_follows_pages():
    client = AsyncMock()
    client.fhir_get.side_effect = [
        {"total": 1500, "entry": [{"resource": {"id": str(i)}} for i in range(1000)]},
        {"total": 1500, "entry": [{"resource": {"id": str(i)}} for i in range(500)]},
    ]
    entries, truncated = await iter_all_observations(client, {"patient": "7"})
    assert len(entries) == 1500
    assert truncated is False
    assert client.fhir_get.await_count == 2


@pytest.mark.asyncio
async def test_collect_observations_parses_entries_no_client_side_filtering():
    # collect_observations no longer takes start/end: any date window is already
    # inside params (built by build_observation_params), applied by the server.
    client = AsyncMock()
    client.fhir_get.return_value = {"total": 1, "entry": [{"resource": {"id": "o1"}}]}
    observations, truncated = await collect_observations(client, {"patient": "7", "date": ["ge2026-04-01"]})
    assert [o.observation_id for o in observations] == ["o1"]
    assert truncated is False
    sent = client.fhir_get.await_args.kwargs["params"]
    assert sent["date"] == ["ge2026-04-01"]


@pytest.mark.asyncio
async def test_count_observations_rejects_non_bundle_body():
    client = AsyncMock()
    client.fhir_get.return_value = {"detail": "boom"}  # 200 but not a search Bundle
    with pytest.raises(JheClientError):
        await count_observations(client, {"patient": "7"})


@pytest.mark.asyncio
async def test_fetch_page_rejects_non_bundle_body():
    client = AsyncMock()
    client.fhir_get.return_value = {"detail": "boom"}
    with pytest.raises(JheClientError):
        await fetch_observation_page(client, {"patient": "7"}, page=1, page_size=50)


@pytest.mark.asyncio
async def test_has_more_false_when_page_exactly_consumes_total():
    client = AsyncMock()
    client.fhir_get.return_value = {"total": 100, "entry": [{} for _ in range(50)]}
    total, _, has_more = await fetch_observation_page(client, {"patient": "7"}, page=2, page_size=50)
    assert total == 100
    assert has_more is False


@pytest.mark.asyncio
async def test_iter_all_observations_caps_at_max_pages():
    client = AsyncMock()
    huge = oq.MAX_PAGE_SIZE * (oq.MAX_PAGES + 5)
    client.fhir_get.return_value = {"total": huge, "entry": [{} for _ in range(oq.MAX_PAGE_SIZE)]}
    out, truncated = await iter_all_observations(client, {"patient": "7"})
    assert client.fhir_get.await_count == oq.MAX_PAGES
    assert len(out) == oq.MAX_PAGE_SIZE * oq.MAX_PAGES
    assert truncated is True  # callers surface this instead of presenting a partial set as complete
