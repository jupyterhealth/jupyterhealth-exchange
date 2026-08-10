from unittest.mock import AsyncMock

import pytest
from jhe_mcp.auth.context import AuthContext, set_current_auth
from jhe_mcp.fhir.models import StudyPatient
from jhe_mcp.tools.observation_counts import (
    count_patient_observations,
    count_study_observations,
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
    monkeypatch.setattr("jhe_mcp.tools.observation_counts.JheClient", lambda _base_url: client)
    return client


@pytest.mark.asyncio
async def test_count_patient_observations(auth, fake_client):
    fake_client.fhir_get.return_value = {"total": 57, "entry": []}
    n = await count_patient_observations(patient_id="40006", base_url="http://jhe")
    assert n == 57
    sent = fake_client.fhir_get.await_args.kwargs["params"]
    assert sent["patient"] == "40006" and sent["_summary"] == "count"


@pytest.mark.asyncio
async def test_count_study_observations_total(auth, fake_client):
    fake_client.fhir_get.return_value = {"total": 980, "entry": []}
    n = await count_study_observations(study_id="30006", base_url="http://jhe")
    assert n == 980
    sent = fake_client.fhir_get.await_args.kwargs["params"]
    assert sent["patient._has:_group:member:_id"] == "30006"
    assert sent["_summary"] == "count"


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
async def test_count_patient_observations_date_filter_is_server_side(auth, fake_client):
    fake_client.fhir_get.return_value = {"total": 2}
    n = await count_patient_observations(
        patient_id="40006", start="2026-04-01", end="2026-04-30", base_url="http://jhe"
    )
    assert n == 2  # server-filtered total, no record fetch
    sent = fake_client.fhir_get.await_args.kwargs["params"]
    assert sent["_summary"] == "count"
    assert sent["date"] == ["ge2026-04-01", "le2026-04-30"]
