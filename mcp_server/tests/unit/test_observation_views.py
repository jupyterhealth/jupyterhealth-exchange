import base64
import json
from unittest.mock import AsyncMock

import pytest
from jhe_mcp.auth.context import AuthContext, set_current_auth
from jhe_mcp.tools.observation_views import (
    get_patient_date_range,
    get_patient_observations,
    summarize_patient_observations,
)


@pytest.fixture
def auth():
    token = set_current_auth(AuthContext(bearer_token="t", subject="u", expires_at=0))
    yield
    from jhe_mcp.auth.context import _current

    _current.reset(token)


@pytest.fixture
def fake_client(monkeypatch):
    client = AsyncMock()
    client.__aenter__.return_value = client
    monkeypatch.setattr("jhe_mcp.tools.observation_views.JheClient", lambda _base_url: client)
    return client


def _entry(obs_id: str, code: str, display: str, when: str, value: int) -> dict:
    payload = {
        "body": {
            "blood_glucose": {"unit": "mg/dL", "value": value},
            "effective_time_frame": {"date_time": when},
        }
    }
    return {
        "resource": {
            "resourceType": "Observation",
            "id": obs_id,
            "code": {"coding": [{"system": "https://w3id.org/openmhealth", "code": code, "display": display}]},
            "subject": {"reference": "Patient/40006"},
            "valueAttachment": {"data": base64.b64encode(json.dumps(payload).encode()).decode()},
        }
    }


@pytest.mark.asyncio
async def test_get_patient_observations_slim_envelope(auth, fake_client):
    fake_client.fhir_get.return_value = {
        "total": 130,
        "entry": [_entry("o1", "omh:blood-glucose:4.0", "Blood glucose", "2026-04-15T08:00:00Z", 92)],
    }
    result = await get_patient_observations(patient_id="40006", limit=50, page=1, base_url="http://jhe")
    assert result["total"] == 130
    assert result["page"] == 1 and result["page_size"] == 50
    assert result["returned"] == 1 and result["has_more"] is True
    rec = result["observations"][0]
    assert rec["observation_id"] == "o1"
    assert rec["value"] == 92 and rec["unit"] == "mg/dL"
    assert "omh_body" not in rec
    sent = fake_client.fhir_get.await_args.kwargs["params"]
    assert sent["_count"] == 50 and sent["_page"] == 1
    assert sent["_sort"] == "-date"  # default order is newest-first


@pytest.mark.asyncio
async def test_get_patient_observations_full_includes_body(auth, fake_client):
    fake_client.fhir_get.return_value = {
        "total": 1,
        "entry": [_entry("o1", "omh:blood-glucose:4.0", "Blood glucose", "2026-04-15T08:00:00Z", 92)],
    }
    result = await get_patient_observations(patient_id="40006", verbosity="full", base_url="http://jhe")
    rec = result["observations"][0]
    assert rec["omh_body"]["blood_glucose"]["value"] == 92


@pytest.mark.asyncio
async def test_summarize_groups_by_type_with_date_range(auth, fake_client):
    fake_client.fhir_get.return_value = {
        "total": 3,
        "entry": [
            _entry("o1", "omh:blood-glucose:4.0", "Blood glucose", "2026-04-15T08:00:00Z", 90),
            _entry("o2", "omh:blood-glucose:4.0", "Blood glucose", "2026-04-10T08:00:00Z", 95),
            _entry("o3", "omh:heart-rate:2.0", "Heart rate", "2026-04-12T08:00:00Z", 70),
        ],
    }
    summary = await summarize_patient_observations(patient_id="40006", base_url="http://jhe")
    assert summary["truncated"] is False
    types = summary["types"]
    assert types["Blood glucose"]["count"] == 2
    assert types["Blood glucose"]["earliest"] == "2026-04-10T08:00:00Z"
    assert types["Blood glucose"]["latest"] == "2026-04-15T08:00:00Z"
    assert types["Heart rate"]["count"] == 1


@pytest.mark.asyncio
async def test_get_patient_observations_date_filter_server_side(auth, fake_client):
    # The date window is sent to the backend; the tool pages the windowed set directly.
    fake_client.fhir_get.return_value = {
        "total": 2,
        "entry": [
            _entry("o1", "omh:blood-glucose:4.0", "Blood glucose", "2026-04-05T00:00:00Z", 90),
            _entry("o2", "omh:blood-glucose:4.0", "Blood glucose", "2026-04-20T00:00:00Z", 95),
        ],
    }
    result = await get_patient_observations(
        patient_id="40006", start="2026-04-01", end="2026-04-30", limit=50, page=1, base_url="http://jhe"
    )
    assert result["total"] == 2
    assert result["returned"] == 2
    sent = fake_client.fhir_get.await_args.kwargs["params"]
    assert sent["date"] == ["ge2026-04-01", "le2026-04-30"]
    assert sent["_count"] == 50  # normal paging, not a full fetch


@pytest.mark.asyncio
async def test_get_patient_observations_order_oldest(auth, fake_client):
    fake_client.fhir_get.return_value = {
        "total": 1,
        "entry": [
            _entry("o1", "omh:blood-glucose:4.0", "Blood glucose", "2026-04-05T00:00:00Z", 90),
        ],
    }
    await get_patient_observations(patient_id="40006", order="oldest", base_url="http://jhe")
    sent = fake_client.fhir_get.await_args.kwargs["params"]
    assert sent["_sort"] == "date"


@pytest.mark.asyncio
async def test_get_patient_observations_rejects_bad_order(auth, fake_client):
    with pytest.raises(ValueError, match="order must be"):
        await get_patient_observations(patient_id="40006", order="sideways", base_url="http://jhe")


@pytest.mark.asyncio
async def test_summarize_passes_date_window_to_server(auth, fake_client):
    fake_client.fhir_get.return_value = {
        "total": 2,
        "entry": [
            _entry("o1", "omh:blood-glucose:4.0", "Blood glucose", "2026-04-05T00:00:00Z", 90),
            _entry("o3", "omh:heart-rate:2.0", "Heart rate", "2026-04-12T00:00:00Z", 70),
        ],
    }
    summary = await summarize_patient_observations(
        patient_id="40006", start="2026-04-01", end="2026-04-30", base_url="http://jhe"
    )
    assert summary["types"]["Blood glucose"]["count"] == 1
    assert summary["types"]["Heart rate"]["count"] == 1
    sent = fake_client.fhir_get.await_args.kwargs["params"]
    assert sent["date"] == ["ge2026-04-01", "le2026-04-30"]


@pytest.mark.asyncio
async def test_summarize_surfaces_truncation(auth, fake_client, monkeypatch):
    # A bounded fetch that could not cover everything must not present its
    # partial counts as complete.
    async def fake_collect(client, params):
        return [], True

    monkeypatch.setattr("jhe_mcp.tools.observation_views.collect_observations", fake_collect)
    summary = await summarize_patient_observations(patient_id="40006", base_url="http://jhe")
    assert summary == {"truncated": True, "types": {}}


def _undated_entry(obs_id: str) -> dict:
    # An OMH body with no effective_time_frame: effective_at parses to None.
    payload = {"body": {"blood_glucose": {"unit": "mg/dL", "value": 90}}}
    return {
        "resource": {
            "resourceType": "Observation",
            "id": obs_id,
            "code": {"coding": [{"system": "https://w3id.org/openmhealth", "code": "omh:blood-glucose:4.0"}]},
            "subject": {"reference": "Patient/40006"},
            "valueAttachment": {"data": base64.b64encode(json.dumps(payload).encode()).decode()},
        }
    }


def _sorted_responses(total: int, asc_entries: list[dict], desc_entries: list[dict]):
    # Route the mocked fhir_get by the emitted _sort so the test doesn't couple
    # to the order the tool issues its requests in.
    def _get(path, params=None, **kwargs):
        assert params.get("_summary") is None  # count comes from the sorted fetch, not a third call
        if params.get("_sort") == "date":
            assert "date" not in params  # ascending probe stays unfiltered so total counts undated rows
            return {"total": total, "entry": asc_entries}
        if params.get("_sort") == "-date":
            # The descending probe excludes undated rows server-side (NULLS FIRST).
            assert params.get("date") == ["ge0001-01-01"]
            return {"total": total, "entry": desc_entries}
        raise AssertionError(f"unexpected params: {params}")

    return _get


@pytest.mark.asyncio
async def test_get_patient_date_range_uses_sorted_probes(auth, fake_client):
    fake_client.fhir_get.side_effect = _sorted_responses(
        3,
        asc_entries=[_entry("o2", "omh:blood-glucose:4.0", "Blood glucose", "2023-01-05T08:00:00Z", 95)],
        desc_entries=[_entry("o3", "omh:heart-rate:2.0", "Heart rate", "2024-03-15T23:16:00Z", 70)],
    )
    result = await get_patient_date_range(patient_id="40006", base_url="http://jhe")
    assert result == {"earliest": "2023-01-05T08:00:00Z", "latest": "2024-03-15T23:16:00Z", "count": 3}
    assert fake_client.fhir_get.await_count == 2  # ascending probe (carries total) + descending probe


@pytest.mark.asyncio
async def test_get_patient_date_range_skips_undated_boundary(auth, fake_client):
    # The server's descending date sort places undated records first (NULLS
    # FIRST); the probe must skip them to the newest record with a timestamp.
    fake_client.fhir_get.side_effect = _sorted_responses(
        3,
        asc_entries=[_entry("o1", "omh:blood-glucose:4.0", "Blood glucose", "2023-01-05T08:00:00Z", 90)],
        desc_entries=[
            _undated_entry("o9"),
            _entry("o3", "omh:blood-glucose:4.0", "Blood glucose", "2024-03-15T23:16:00Z", 99),
        ],
    )
    result = await get_patient_date_range(patient_id="40006", base_url="http://jhe")
    assert result["latest"] == "2024-03-15T23:16:00Z"
    assert result["earliest"] == "2023-01-05T08:00:00Z"


@pytest.mark.asyncio
async def test_get_patient_date_range_empty(auth, fake_client):
    fake_client.fhir_get.return_value = {"total": 0}
    result = await get_patient_date_range(patient_id="40099", base_url="http://jhe")
    assert result == {"earliest": None, "latest": None, "count": 0}
    assert fake_client.fhir_get.await_count == 1  # zero total short-circuits the descending probe


@pytest.mark.asyncio
async def test_get_patient_observations_order_and_window_combined(auth, fake_client):
    fake_client.fhir_get.return_value = {"total": 0}
    await get_patient_observations(
        patient_id="40006", start="2026-04-01", end="2026-04-30", order="oldest", base_url="http://jhe"
    )
    sent = fake_client.fhir_get.await_args.kwargs["params"]
    assert sent["date"] == ["ge2026-04-01", "le2026-04-30"]
    assert sent["_sort"] == "date"
