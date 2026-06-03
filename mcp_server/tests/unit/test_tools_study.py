from unittest.mock import AsyncMock

import pytest
from jhe_mcp.auth.context import AuthContext, set_current_auth
from jhe_mcp.fhir.client import JheClientError
from jhe_mcp.tools.study import (
    get_patient_demographics,
    get_study_count,
    get_study_metadata,
    list_studies,
    list_study_patients,
)


@pytest.fixture
def auth():
    token = set_current_auth(AuthContext(bearer_token="t", subject="u", expires_at=0))
    yield
    from jhe_mcp.auth.context import _current

    _current.reset(token)


@pytest.fixture
def fake_client(monkeypatch):
    """Replace JheClient with an AsyncMock acting as its own async context.

    `__aenter__.return_value = client` makes `async with JheClient(...) as c`
    bind `c` to the same mock the test asserts against.
    """
    client = AsyncMock()
    client.__aenter__.return_value = client
    monkeypatch.setattr("jhe_mcp.tools.study.JheClient", lambda _base_url: client)
    return client


@pytest.mark.asyncio
async def test_get_study_count(auth, fake_client):
    fake_client.admin_get.return_value = {"count": 7, "results": []}
    result = await get_study_count(base_url="http://jhe")
    assert result == 7
    fake_client.admin_get.assert_awaited_once_with("studies", params={"page_size": 1})


@pytest.mark.asyncio
async def test_get_study_count_rejects_non_paginated_response(auth, fake_client):
    # A 200 with an unexpected shape must raise, not be reported as 0 studies.
    fake_client.admin_get.return_value = {"detail": "boom"}
    with pytest.raises(JheClientError):
        await get_study_count(base_url="http://jhe")


@pytest.mark.asyncio
async def test_list_studies(auth, fake_client):
    fake_client.admin_get.return_value = {
        "results": [
            {"id": 1, "name": "A", "organization": {"id": 10, "name": "O1"}},
            {"id": 2, "name": "B", "organization": {"id": 11, "name": "O2"}},
        ],
        "next": None,
    }
    studies = await list_studies(base_url="http://jhe")
    assert len(studies) == 2
    assert studies[0].name == "A"
    assert studies[1].organization_name == "O2"


@pytest.mark.asyncio
async def test_list_studies_paginated(auth, fake_client):
    fake_client.admin_get.side_effect = [
        {
            "results": [{"id": 1, "name": "A", "organization": {"id": 10, "name": "O1"}}],
            "next": "http://jhe/api/v1/studies?page=2",
        },
        {
            "results": [{"id": 2, "name": "B", "organization": {"id": 11, "name": "O2"}}],
            "next": None,
        },
    ]
    studies = await list_studies(base_url="http://jhe")
    assert len(studies) == 2
    assert studies[0].name == "A"
    assert studies[1].name == "B"
    assert fake_client.admin_get.await_count == 2


@pytest.mark.asyncio
async def test_list_studies_cursor_pagination(auth, fake_client):
    """Non-`page` pagination params (e.g. cursor) must not raise KeyError and must
    be forwarded as-is to the next admin_get call."""
    fake_client.admin_get.side_effect = [
        {
            "results": [{"id": 1, "name": "A", "organization": {"id": 10, "name": "O1"}}],
            "next": "http://jhe/api/v1/studies?cursor=abc123",
        },
        {
            "results": [{"id": 2, "name": "B", "organization": {"id": 11, "name": "O2"}}],
            "next": None,
        },
    ]
    studies = await list_studies(base_url="http://jhe")
    assert len(studies) == 2
    assert studies[0].name == "A"
    assert studies[1].name == "B"
    # The second call must have forwarded the cursor param.
    second_call_params = (
        fake_client.admin_get.call_args_list[1].kwargs.get("params") or fake_client.admin_get.call_args_list[1].args[1]
        if len(fake_client.admin_get.call_args_list[1].args) > 1
        else fake_client.admin_get.call_args_list[1].kwargs.get("params")
    )
    assert second_call_params is not None
    assert second_call_params.get("cursor") == "abc123"


@pytest.mark.asyncio
async def test_list_studies_offset_pagination(auth, fake_client):
    """Offset+limit pagination (?offset=20&limit=20) must not raise and must forward
    both params to the next request."""
    fake_client.admin_get.side_effect = [
        {
            "results": [{"id": 1, "name": "A", "organization": {}}],
            "next": "http://jhe/api/v1/studies?offset=20&limit=20",
        },
        {
            "results": [{"id": 2, "name": "B", "organization": {}}],
            "next": None,
        },
    ]
    studies = await list_studies(base_url="http://jhe")
    assert len(studies) == 2
    second_params = fake_client.admin_get.call_args_list[1].kwargs.get("params")
    assert second_params is not None
    assert second_params.get("offset") == "20"
    assert second_params.get("limit") == "20"


@pytest.mark.asyncio
async def test_list_study_patients(auth, fake_client):
    fake_client.admin_get.return_value = [
        {"id": 1, "nameGiven": "Pat", "nameFamily": "Jones", "telecomEmail": "pat@ex.com"},
        {"id": 2, "nameGiven": "Sam", "nameFamily": "Smith", "telecomEmail": "sam@ex.com"},
    ]
    patients = await list_study_patients(study_id="5", base_url="http://jhe")
    assert len(patients) == 2
    assert patients[0].patient_id == "1"
    assert patients[0].email == "pat@ex.com"
    assert patients[1].family_name == "Smith"
    fake_client.admin_get.assert_awaited_once_with("studies/5/patients")


@pytest.mark.asyncio
async def test_get_study_metadata_found(auth, fake_client):
    fake_client.admin_get.return_value = {
        "id": 5,
        "name": "Glucose Study",
        "description": "demo",
        "organization": {"id": 9, "name": "Duke"},
    }
    meta = await get_study_metadata(study_id="5", base_url="http://jhe")
    assert meta is not None
    assert meta.study_id == "5"
    assert meta.organization_name == "Duke"


@pytest.mark.asyncio
async def test_get_study_metadata_404(auth, fake_client):
    fake_client.admin_get.return_value = None
    meta = await get_study_metadata(study_id="999", base_url="http://jhe")
    assert meta is None


@pytest.mark.asyncio
async def test_get_patient_demographics(auth, fake_client):
    fake_client.admin_get.return_value = {
        "id": 7,
        "nameGiven": "Sam",
        "nameFamily": "Smith",
        "birthDate": "1985-01-02",
    }
    d = await get_patient_demographics(patient_id="7", base_url="http://jhe")
    assert d is not None
    assert d.given_name == "Sam"
    fake_client.admin_get.assert_awaited_once_with("patients/7", treat_404_as_none=True)
