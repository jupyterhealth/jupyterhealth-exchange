import base64
import json
from unittest.mock import AsyncMock

import pytest
from jhe_mcp.auth.context import AuthContext, set_current_auth
from jhe_mcp.fhir.models import StudyPatient
from jhe_mcp.tools.observation_counts import (
    count_patient_observations,
    count_study_observations,
)


def _entry_at(obs_id: str, when: str) -> dict:
    payload = {"body": {"heart_rate": {"value": 70, "unit": "beats/min"}, "effective_time_frame": {"date_time": when}}}
    return {
        "resource": {
            "resourceType": "Observation",
            "id": obs_id,
            "code": {"coding": [{"system": "https://w3id.org/openmhealth", "code": "omh:heart-rate:2.0"}]},
            "subject": {"reference": "Patient/40006"},
            "valueAttachment": {"data": base64.b64encode(json.dumps(payload).encode()).decode()},
        }
    }


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
    monkeypatch.setattr("jhe_mcp.tools.observation_counts.JheClient", lambda _base_url: client)
    return client


@pytest.mark.asyncio
async def test_count_patient_observations(auth, fake_client):
    fake_client.fhir_get.return_value = {"total": 57, "entry": []}
    n = await count_patient_observations(patient_id="40006", base_url="http://jhe")
    assert n == 57
    sent = fake_client.fhir_get.await_args.kwargs["params"]
    assert sent["patient"] == "40006" and sent["_count"] == 1


@pytest.mark.asyncio
async def test_count_study_observations_total(auth, fake_client):
    fake_client.fhir_get.return_value = {"total": 980, "entry": []}
    n = await count_study_observations(study_id="30006", base_url="http://jhe")
    assert n == 980
    sent = fake_client.fhir_get.await_args.kwargs["params"]
    assert sent["patient._has:_group:member:_id"] == "30006"


@pytest.mark.asyncio
async def test_count_study_observations_by_patient(auth, fake_client, monkeypatch):
    async def fake_list(*, study_id, base_url):
        return [
            StudyPatient(patient_id="40006", given_name="May", family_name="Nguyen"),
            StudyPatient(patient_id="40007", given_name="Al", family_name="Roe"),
        ]

    monkeypatch.setattr("jhe_mcp.tools.observation_counts.list_study_patients", fake_list)
    fake_client.fhir_get.side_effect = [
        {"total": 600, "entry": []},
        {"total": 380, "entry": []},
    ]
    result = await count_study_observations(study_id="30006", by_patient=True, base_url="http://jhe")
    assert result == {"40006": 600, "40007": 380}


@pytest.mark.asyncio
async def test_count_patient_observations_date_filter_is_client_side(auth, fake_client):
    # Backend ignores `date`, so a date window triggers a full fetch + in-process filter.
    fake_client.fhir_get.return_value = {
        "total": 3,
        "entry": [
            _entry_at("o1", "2026-04-05T00:00:00Z"),
            _entry_at("o2", "2026-04-20T00:00:00Z"),
            _entry_at("o3", "2026-05-10T00:00:00Z"),
        ],
    }
    n = await count_patient_observations(
        patient_id="40006", start="2026-04-01", end="2026-04-30", base_url="http://jhe"
    )
    assert n == 2  # o1, o2 in window; o3 (May) excluded
    sent = fake_client.fhir_get.await_args.kwargs["params"]
    assert sent["_count"] == 1000  # full-fetch page, not the cheap _count=1 path
