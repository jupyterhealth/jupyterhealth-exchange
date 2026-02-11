import pytest

from core.models import CodeableConcept

from .utils import fetch_paginated, Code


def test_list_data_sources(api_client, device):
    data_sources = fetch_paginated(api_client, "/api/v1/data_sources")
    assert len(data_sources) == 1
    assert data_sources[0]["id"] == device.id


def test_get_data_source(api_client, device):
    r = api_client.get(f"/api/v1/data_sources/{device.id}")
    assert r.status_code == 200, r.text
    data_source = r.json()
    assert data_source["id"] == device.id
    # TODO: check more of the model


def test_create_delete_data_source(api_client, superuser):
    data_sources = fetch_paginated(api_client, "/api/v1/data_sources")
    existing = len(data_sources)
    r = api_client.post("/api/v1/data_sources", {})
    assert r.status_code == 403, r.text
    # regular users don't have permission to create
    api_client.force_authenticate(superuser)
    r = api_client.post("/api/v1/data_sources", {"name": "Device!"})
    assert r.status_code == 201, r.text
    ds = r.json()
    assert ds["name"] == "Device!"
    data_sources = fetch_paginated(api_client, "/api/v1/data_sources")
    assert len(data_sources) == existing + 1
    r = api_client.delete(f"/api/v1/data_sources/{ds['id']}")
    assert r.status_code == 204, r.text
    r = api_client.get(f"/api/v1/data_sources/{ds['id']}")
    assert r.status_code == 404
    data_sources = fetch_paginated(api_client, "/api/v1/data_sources")
    assert len(data_sources) == existing


@pytest.mark.xfail(reason="fails to validate")
def test_create_invalid_data_source(api_client, superuser):
    api_client.force_authenticate(superuser)
    r = api_client.post("/api/v1/data_sources", {})
    assert r.status_code == 400, r.text


def test_update_data_source(api_client, superuser, device):
    api_client.force_authenticate(superuser)
    ds_url = f"/api/v1/data_sources/{device.id}"
    before = api_client.get(ds_url).json()
    r = api_client.patch(ds_url, {"name": "name2"})
    assert r.status_code == 200, r.text
    after = api_client.get(ds_url).json()
    device.refresh_from_db()
    assert device.name == "name2"
    assert before["id"] == after["id"]
    # the same after updating name
    before["name"] = "name2"
    assert before == after


def test_get_all_scopes(api_client, device, hr_study):
    r = api_client.get("/api/v1/data_sources/all_scopes")
    assert r.status_code == 200, r.text
    scopes = r.json()
    assert len(scopes) == 1
    assert scopes[0]["codingCode"] == Code.HeartRate.value


def test_supported_scopes(api_client, superuser, device):
    api_client.force_authenticate(superuser)
    bp_code = CodeableConcept.objects.create(
        coding_system=Code.OpenMHealth.value,
        coding_code=Code.BloodPressure.value,
        text="BP",
    )
    scopes_url = f"/api/v1/data_sources/{device.id}/supported_scopes"
    r = api_client.get(scopes_url)
    assert r.status_code == 200, r.text
    scopes = r.json()
    assert len(scopes) == 0
    r = api_client.post(scopes_url, {"scope_code_id": bp_code.id})
    assert r.status_code == 200, r.text
    r = api_client.get(scopes_url)
    assert r.status_code == 200, r.text
    scopes = r.json()
    assert len(scopes) == 1
    assert scopes[0]["scopeCode"]["codingCode"] == bp_code.coding_code
    r = api_client.delete(scopes_url, {"scope_code_id": bp_code.id})
    assert r.status_code == 200, r.text
    r = api_client.get(scopes_url)
    assert r.status_code == 200, r.text
    scopes = r.json()
    assert len(scopes) == 0


@pytest.mark.xfail(reason="missing argument validation")
def test_supported_scopes_invalid(api_client, superuser, device):
    api_client.force_authenticate(superuser)
    scopes_url = f"/api/v1/data_sources/{device.id}/supported_scopes"
    r = api_client.post(scopes_url, {})
    assert r.status_code == 400, r.text
