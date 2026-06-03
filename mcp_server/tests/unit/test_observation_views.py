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
    assert summary["Blood glucose"]["count"] == 2
    assert summary["Blood glucose"]["earliest"] == "2026-04-10T08:00:00Z"
    assert summary["Blood glucose"]["latest"] == "2026-04-15T08:00:00Z"
    assert summary["Heart rate"]["count"] == 1


@pytest.mark.asyncio
async def test_get_patient_observations_date_filter_client_side(auth, fake_client):
    # Backend ignores `date`; tool fetches all, filters by effective_at, paginates in process.
    fake_client.fhir_get.return_value = {
        "total": 3,
        "entry": [
            _entry("o1", "omh:blood-glucose:4.0", "Blood glucose", "2026-04-05T00:00:00Z", 90),
            _entry("o2", "omh:blood-glucose:4.0", "Blood glucose", "2026-04-20T00:00:00Z", 95),
            _entry("o3", "omh:blood-glucose:4.0", "Blood glucose", "2026-05-10T00:00:00Z", 99),
        ],
    }
    result = await get_patient_observations(
        patient_id="40006", start="2026-04-01", end="2026-04-30", limit=50, page=1, base_url="http://jhe"
    )
    assert result["total"] == 2  # filtered client-side, not the backend's 3
    assert result["returned"] == 2
    assert [o["observation_id"] for o in result["observations"]] == ["o1", "o2"]
    sent = fake_client.fhir_get.await_args.kwargs["params"]
    assert sent["_count"] == 1000  # full-fetch path, not the page-size path


@pytest.mark.asyncio
async def test_get_patient_observations_date_filter_paginates_past_page_1(auth, fake_client):
    # In-process pagination of the date-filtered set must be correct beyond page 1.
    fake_client.fhir_get.return_value = {
        "total": 3,
        "entry": [
            _entry("o1", "omh:blood-glucose:4.0", "Blood glucose", "2026-04-05T00:00:00Z", 90),
            _entry("o2", "omh:blood-glucose:4.0", "Blood glucose", "2026-04-10T00:00:00Z", 95),
            _entry("o3", "omh:blood-glucose:4.0", "Blood glucose", "2026-04-20T00:00:00Z", 99),
        ],
    }
    result = await get_patient_observations(
        patient_id="40006", start="2026-04-01", end="2026-04-30", limit=2, page=2, base_url="http://jhe"
    )
    assert result["total"] == 3
    assert result["page"] == 2
    assert result["returned"] == 1
    assert result["has_more"] is False
    assert [o["observation_id"] for o in result["observations"]] == ["o3"]


@pytest.mark.asyncio
async def test_summarize_respects_date_window(auth, fake_client):
    fake_client.fhir_get.return_value = {
        "total": 3,
        "entry": [
            _entry("o1", "omh:blood-glucose:4.0", "Blood glucose", "2026-04-05T00:00:00Z", 90),
            _entry("o2", "omh:blood-glucose:4.0", "Blood glucose", "2026-05-10T00:00:00Z", 95),
            _entry("o3", "omh:heart-rate:2.0", "Heart rate", "2026-04-12T00:00:00Z", 70),
        ],
    }
    summary = await summarize_patient_observations(
        patient_id="40006", start="2026-04-01", end="2026-04-30", base_url="http://jhe"
    )
    assert summary["Blood glucose"]["count"] == 1  # only the April record
    assert summary["Blood glucose"]["latest"] == "2026-04-05T00:00:00Z"
    assert summary["Heart rate"]["count"] == 1


@pytest.mark.asyncio
async def test_get_patient_date_range(auth, fake_client):
    fake_client.fhir_get.return_value = {
        "total": 3,
        "entry": [
            _entry("o1", "omh:blood-glucose:4.0", "Blood glucose", "2024-03-12T22:00:00Z", 90),
            _entry("o2", "omh:blood-glucose:4.0", "Blood glucose", "2023-01-05T08:00:00Z", 95),
            _entry("o3", "omh:heart-rate:2.0", "Heart rate", "2024-03-15T23:16:00Z", 70),
        ],
    }
    result = await get_patient_date_range(patient_id="40006", base_url="http://jhe")
    assert result["earliest"] == "2023-01-05T08:00:00Z"
    assert result["latest"] == "2024-03-15T23:16:00Z"
    assert result["count"] == 3


@pytest.mark.asyncio
async def test_get_patient_date_range_empty(auth, fake_client):
    fake_client.fhir_get.return_value = {"total": 0, "entry": []}
    result = await get_patient_date_range(patient_id="40099", base_url="http://jhe")
    assert result == {"earliest": None, "latest": None, "count": 0}
