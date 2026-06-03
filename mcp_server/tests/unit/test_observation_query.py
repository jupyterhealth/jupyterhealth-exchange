from unittest.mock import AsyncMock

import pytest
from jhe_mcp.fhir import observation_query as oq
from jhe_mcp.fhir.client import JheClientError
from jhe_mcp.fhir.observation_query import (
    build_observation_params,
    count_observations,
    fetch_observation_page,
    in_date_range,
    iter_all_observations,
)


def test_build_params_patient_and_code():
    params = build_observation_params(patient_id="7", data_type="blood-glucose")
    assert params["patient"] == "7"
    assert "omh:blood-glucose:4.0" in params["code"]
    # date is filtered client-side, never sent to the backend
    assert "date" not in params


def test_in_date_range_inclusive_and_undated():
    assert in_date_range("2026-04-15T08:00:00Z", "2026-04-01", "2026-04-30") is True
    assert in_date_range("2026-04-01T00:00:00Z", "2026-04-01", "2026-04-30") is True  # start inclusive
    assert in_date_range("2026-04-30T23:59:00Z", "2026-04-01", "2026-04-30") is True  # end inclusive
    assert in_date_range("2026-05-01T00:00:00Z", "2026-04-01", "2026-04-30") is False
    assert in_date_range("2026-03-31T00:00:00Z", "2026-04-01", None) is False
    assert in_date_range(None, "2026-04-01", "2026-04-30") is False  # undated excluded
    assert in_date_range("2026-04-15T08:00:00Z", None, None) is True  # no window


def test_in_date_range_non_iso_excluded():
    # A non-ISO effective_at can't be placed in time, so it is excluded rather
    # than mis-filtered by a naive string slice/compare. Mirrors the undated
    # (effective_at=None) contract: unplaceable timestamps are out of range.
    assert in_date_range("04/15/2026", "2026-04-01", "2026-04-30") is False
    assert in_date_range("not-a-date", "2026-04-01", None) is False
    assert in_date_range("04/15/2026", None, None) is False


def test_require_iso_date_rejects_bad_window():
    oq._require_iso_date("2026-04-01", "start")  # valid: no raise
    oq._require_iso_date(None, "start")  # absent: no raise
    with pytest.raises(ValueError, match="start must be an ISO date"):
        oq._require_iso_date("last week", "start")


def test_build_params_study_scope():
    params = build_observation_params(study_id="30006")
    assert params["patient._has:_group:member:_id"] == "30006"


def test_build_params_unknown_data_type_raises():
    with pytest.raises(ValueError, match="Unknown data_type"):
        build_observation_params(patient_id="7", data_type="not-a-type")


@pytest.mark.asyncio
async def test_count_observations_reads_total_not_entries():
    client = AsyncMock()
    client.fhir_get.return_value = {"resourceType": "Bundle", "total": 4242, "entry": [{}]}
    n = await count_observations(client, {"patient": "7"})
    assert n == 4242
    sent = client.fhir_get.await_args.kwargs["params"]
    assert sent["_count"] == 1
    assert sent["patient"] == "7"


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
    entries = await iter_all_observations(client, {"patient": "7"})
    assert len(entries) == 1500
    assert client.fhir_get.await_count == 2


# --- #1: a non-Bundle 200 must raise, not be silently reported as 0/empty ---


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


# --- has_more boundary (previously untested) ---


@pytest.mark.asyncio
async def test_has_more_false_when_page_exactly_consumes_total():
    client = AsyncMock()
    client.fhir_get.return_value = {"total": 100, "entry": [{} for _ in range(50)]}
    total, _, has_more = await fetch_observation_page(client, {"patient": "7"}, page=2, page_size=50)
    assert total == 100
    assert has_more is False


# --- #4: iter_all_observations is bounded so a misbehaving server can't OOM us ---


@pytest.mark.asyncio
async def test_iter_all_observations_caps_at_max_pages():
    client = AsyncMock()
    huge = oq.MAX_PAGE_SIZE * (oq.MAX_PAGES + 5)
    client.fhir_get.return_value = {"total": huge, "entry": [{} for _ in range(oq.MAX_PAGE_SIZE)]}
    out = await iter_all_observations(client, {"patient": "7"})
    assert client.fhir_get.await_count == oq.MAX_PAGES
    assert len(out) == oq.MAX_PAGE_SIZE * oq.MAX_PAGES
