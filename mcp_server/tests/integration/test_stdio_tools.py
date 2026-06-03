import pytest
from jhe_mcp.tools.observation_views import get_patient_observations
from jhe_mcp.tools.study import (
    get_patient_demographics,
    get_study_count,
    get_study_metadata,
    list_studies,
)


@pytest.mark.asyncio
async def test_get_study_count_returns_int(jhe_base_url):
    count = await get_study_count(base_url=jhe_base_url)
    assert isinstance(count, int)
    assert count >= 2  # seed gives mary access to at least 2 studies


@pytest.mark.asyncio
async def test_list_studies_returns_seed(jhe_base_url):
    studies = await list_studies(base_url=jhe_base_url)
    assert isinstance(studies, list)
    assert len(studies) >= 2
    study_ids = {s.study_id for s in studies}
    assert "30001" in study_ids or "30002" in study_ids


@pytest.mark.asyncio
async def test_get_study_metadata_first_study(jhe_base_url):
    studies = await list_studies(base_url=jhe_base_url)
    sid = studies[0].study_id
    meta = await get_study_metadata(study_id=sid, base_url=jhe_base_url)
    assert meta is not None
    assert meta.study_id == sid
    assert meta.name


@pytest.mark.asyncio
async def test_get_patient_demographics_40001(jhe_base_url):
    d = await get_patient_demographics(patient_id="40001", base_url=jhe_base_url)
    assert d is not None
    assert d.patient_id == "40001"
    assert d.given_name == "Peter"
    assert d.family_name == "Farrell"
    assert d.birth_date == "1982-01-14"


@pytest.mark.asyncio
async def test_get_patient_observations_40001(jhe_base_url):
    result = await get_patient_observations(patient_id="40001", base_url=jhe_base_url)
    assert isinstance(result, dict)
    assert result["total"] >= 2  # seed gives patient 40001 at least 2 observations
    assert "observations" in result
