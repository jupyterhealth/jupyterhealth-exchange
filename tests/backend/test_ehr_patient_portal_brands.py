"""Tests for the EHR Patient Portal hospital-picker search API: GET /api/v1/ehr-patient-portal/brands."""

import pytest
from rest_framework.test import APIClient

from core.models import EhrBrand, EhrBrandLocation

URL = "/api/v1/ehr-patient-portal/brands"


@pytest.fixture
def patient_client(patient):
    client = APIClient()
    client.default_format = "json"
    client.force_authenticate(patient.jhe_user)
    return client


@pytest.fixture
def seeded_brands(db):
    sinai = EhrBrand.objects.create(name="Mount Sinai", fhir_base_url="https://sinai.example.org/FHIR/R4")
    EhrBrandLocation.objects.create(
        brand=sinai,
        name="Mount Sinai Hospital",
        address_text="1 Levy Pl, New York, NY",
        city="New York",
        state="NY",
        postal_code="10029",
    )
    EhrBrandLocation.objects.create(
        brand=sinai,
        name="Mount Sinai West",
        address_text="1000 10th Ave, New York, NY",
        city="New York",
        state="NY",
        postal_code="10019",
    )
    mercy = EhrBrand.objects.create(name="Mercy Health", fhir_base_url="https://mercy.example.org/FHIR/R4")
    EhrBrandLocation.objects.create(
        brand=mercy,
        name="Mercy Hospital St Louis",
        address_text="615 New Ballas Rd, St. Louis, MO",
        city="St. Louis",
        state="MO",
        postal_code="63141",
    )
    return {"sinai": sinai, "mercy": mercy}


def test_brands_requires_auth(db):
    resp = APIClient().get(URL)
    assert resp.status_code in (401, 403)


def test_brands_returns_facilities_with_brand_and_base_url(seeded_brands, patient_client):
    resp = patient_client.get(URL, {"q": "sinai"})
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 2
    row = results[0]
    assert row["brandName"] == "Mount Sinai"
    assert row["fhirBaseUrl"] == "https://sinai.example.org/FHIR/R4"
    assert "addressText" in row and "facilityName" in row


def test_brands_filter_by_query_matches_facility_name(seeded_brands, patient_client):
    resp = patient_client.get(URL, {"q": "Mercy"})
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["brandName"] == "Mercy Health"


def test_brands_filter_by_state(seeded_brands, patient_client):
    resp = patient_client.get(URL, {"state": "MO"})
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["state"] == "MO"


def test_brands_filter_by_postal(seeded_brands, patient_client):
    resp = patient_client.get(URL, {"postal": "10019"})
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["facilityName"] == "Mount Sinai West"


def test_brands_empty_query_returns_all(seeded_brands, patient_client):
    resp = patient_client.get(URL)
    assert len(resp.json()["results"]) == 3


def test_brands_limit_is_capped(seeded_brands, patient_client):
    resp = patient_client.get(URL, {"limit": "1"})
    assert len(resp.json()["results"]) == 1


def test_search_returns_the_location_id(patient_client, seeded_brands):
    # The client sends this back as FhirSource.ehr_brand_location; without it the picked facility
    # could not be recorded, since every location of a brand shares one fhir_base_url.
    results = patient_client.get(URL, {"q": "Mount Sinai"}).json()["results"]
    assert results
    ids = {row["id"] for row in results}
    assert ids == set(EhrBrandLocation.objects.filter(brand__name="Mount Sinai").values_list("id", flat=True))


@pytest.fixture
def epic_sandbox_location(db):
    # Epic's real sandbox address (Verona, WI) -- a facility a patient would plausibly search
    # for by state name/abbreviation or ZIP, none of which the free-text `q` matched before.
    epic = EhrBrand.objects.create(name="Epic Sandbox", fhir_base_url="https://epic.example.org/FHIR/R4")
    return EhrBrandLocation.objects.create(
        brand=epic,
        name="Epic Sandbox - Madison Campus",
        address_text="1979 Milky Way, Verona, WI",
        city="Verona",
        state="WI",
        postal_code="53593",
    )


@pytest.mark.parametrize("q", ["WI", "Wisconsin", "wisconsin", "53593", "Verona"])
def test_brands_free_text_query_matches_state_name_abbreviation_and_zip(q, epic_sandbox_location, patient_client):
    results = patient_client.get(URL, {"q": q}).json()["results"]
    assert [row["facilityName"] for row in results] == ["Epic Sandbox - Madison Campus"]


def test_brands_free_text_query_matches_no_unrelated_state(epic_sandbox_location, patient_client):
    results = patient_client.get(URL, {"q": "ZZ"}).json()["results"]
    assert results == []
