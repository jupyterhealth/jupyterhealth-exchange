from unittest.mock import AsyncMock

import pytest
from jhe_mcp.auth.context import AuthContext, set_current_auth
from jhe_mcp.fhir.models import PatientSearchResult
from jhe_mcp.tools.patients import build_patient_params, search_patients


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
    monkeypatch.setattr("jhe_mcp.tools.patients.JheClient", lambda _base_url: client)
    return client


@pytest.fixture(autouse=True)
def unknown_capabilities(monkeypatch):
    # Default: capabilities unknown -> preflight fails open, tools behave as before.
    async def _none(base_url):
        return None

    monkeypatch.setattr("jhe_mcp.tools.patients.get_capabilities", _none)


def _patient_entry(pid: str, family: str, given: str, birth: str) -> dict:
    return {
        "resource": {
            "resourceType": "Patient",
            "id": pid,
            "name": [{"family": family, "given": [given]}],
            "birthDate": birth,
            "telecom": [
                {"system": "phone", "value": "555-0100", "use": "mobile"},
                {"system": "email", "value": f"{given.lower()}@example.org", "use": "home"},
            ],
        }
    }


def test_build_patient_params_each_arg():
    assert build_patient_params(name="smi") == {"name": "smi"}
    assert build_patient_params(family="Nguyen") == {"family": "Nguyen"}
    assert build_patient_params(given="May") == {"given": "May"}
    assert build_patient_params(birthdate="1980-01-31") == {"birthdate": "1980-01-31"}


def test_build_patient_params_birthdate_prefix_passthrough():
    assert build_patient_params(birthdate="ge1980-01-01") == {"birthdate": "ge1980-01-01"}
    assert build_patient_params(birthdate="le2000-12-31") == {"birthdate": "le2000-12-31"}
    assert build_patient_params(birthdate="gt1980-01-01") == {"birthdate": "gt1980-01-01"}
    assert build_patient_params(birthdate="lt2000-12-31") == {"birthdate": "lt2000-12-31"}


def test_build_patient_params_requires_at_least_one():
    with pytest.raises(ValueError, match="at least one"):
        build_patient_params()


def test_build_patient_params_strips_and_rejects_whitespace():
    # Whitespace-only criteria must not slip past the guard: the server skips
    # blank filters, which would turn "  " into an unfiltered patient listing.
    assert build_patient_params(family=" Nguyen ") == {"family": "Nguyen"}
    with pytest.raises(ValueError, match="at least one"):
        build_patient_params(name="   ")


def test_build_patient_params_rejects_bad_birthdate():
    with pytest.raises(ValueError, match="birthdate"):
        build_patient_params(birthdate="Jan 1 1980")
    with pytest.raises(ValueError, match="birthdate"):
        build_patient_params(birthdate="xx1980-01-01")
    # fromisoformat alone accepts these; the server rejects or misreads them.
    with pytest.raises(ValueError, match="birthdate"):
        build_patient_params(birthdate="19800101")
    with pytest.raises(ValueError, match="birthdate"):
        build_patient_params(birthdate="ge2026-W14-2")


def test_patient_search_result_rejects_entry_without_id():
    from jhe_mcp.fhir.client import JheClientError

    with pytest.raises(JheClientError, match="Patient entry"):
        PatientSearchResult.from_fhir_entry({"resource": {"resourceType": "Patient"}})


def test_patient_search_result_from_fhir_entry():
    p = PatientSearchResult.from_fhir_entry(_patient_entry("40006", "Nguyen", "May", "1980-01-31"))
    assert p.patient_id == "40006"
    assert p.family_name == "Nguyen"
    assert p.given_name == "May"
    assert p.birth_date == "1980-01-31"
    assert p.phone == "555-0100"
    assert p.email == "may@example.org"


def test_patient_search_result_tolerates_missing_fields():
    p = PatientSearchResult.from_fhir_entry({"resource": {"resourceType": "Patient", "id": "1"}})
    assert p.patient_id == "1"
    assert p.family_name is None and p.given_name is None
    assert p.birth_date is None and p.phone is None and p.email is None


@pytest.mark.asyncio
async def test_search_patients_envelope_and_params(auth, fake_client):
    fake_client.fhir_get.return_value = {
        "total": 120,
        "entry": [_patient_entry("40006", "Nguyen", "May", "1980-01-31")],
    }
    result = await search_patients(family="ngu", limit=50, page=1, base_url="http://jhe")
    assert result["total"] == 120
    assert result["page"] == 1 and result["page_size"] == 50
    assert result["returned"] == 1 and result["has_more"] is True
    assert result["patients"][0]["patient_id"] == "40006"
    args, kwargs = fake_client.fhir_get.await_args
    assert args[0] == "Patient"
    sent = kwargs["params"]
    assert sent["family"] == "ngu"
    assert sent["_count"] == 50 and sent["_page"] == 1


@pytest.mark.asyncio
async def test_search_patients_no_args_raises_before_any_request(auth, fake_client):
    with pytest.raises(ValueError, match="at least one"):
        await search_patients(base_url="http://jhe")
    fake_client.fhir_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_patients_clamps_limit_and_page(auth, fake_client):
    fake_client.fhir_get.return_value = {"total": 0}  # entry key absent is tolerated
    result = await search_patients(family="x", limit=5000, page=0, base_url="http://jhe")
    sent = fake_client.fhir_get.await_args.kwargs["params"]
    assert sent["_count"] == 1000 and sent["_page"] == 1
    assert result["page_size"] == 1000 and result["page"] == 1
    assert result["returned"] == 0 and result["patients"] == []
    assert result["has_more"] is False


@pytest.mark.asyncio
async def test_search_patients_blocks_known_unsupported_param(auth, fake_client, monkeypatch):
    from jhe_mcp.fhir.capabilities import ServerCapabilities

    caps = ServerCapabilities.from_capability_statement(
        {
            "resourceType": "CapabilityStatement",
            "rest": [{"resource": [{"type": "Patient", "searchParam": [{"name": "family", "type": "string"}]}]}],
        }
    )

    async def _caps(base_url):
        return caps

    monkeypatch.setattr("jhe_mcp.tools.patients.get_capabilities", _caps)
    with pytest.raises(ValueError, match="birthdate"):
        await search_patients(birthdate="1980-01-01", base_url="http://jhe")
    fake_client.fhir_get.assert_not_awaited()
    # A supported param still goes through.
    fake_client.fhir_get.return_value = {"total": 0}
    await search_patients(family="ngu", base_url="http://jhe")


@pytest.mark.asyncio
async def test_search_patients_has_more_false_when_page_consumes_total(auth, fake_client):
    fake_client.fhir_get.return_value = {
        "total": 2,
        "entry": [
            _patient_entry("40006", "Nguyen", "May", "1980-01-31"),
            _patient_entry("40007", "Roe", "Al", "1975-06-02"),
        ],
    }
    result = await search_patients(family="n", limit=2, page=1, base_url="http://jhe")
    assert result["returned"] == 2
    assert result["has_more"] is False
