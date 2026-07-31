"""Tests for the unified FHIR resource endpoint (core/views/fhir.py).

Covers the reworked routing: a Django model backs only the JHE-system view of a resource and
everything else lands in FhirAuxResource (UUID id, linked to a FhirSource named by the
X-JHE-FHIR-Source-ID header or the body's meta.source); a search hits exactly one store, chosen by
the _source param (no union); writes for read/search-only mapped types fall through to aux; only
OMH Observations write to the Django model.
"""

import base64
import json
import uuid

import pytest
from rest_framework.test import APIClient

from core.models import (
    JHE_FHIR_SOURCE_BASE,
    JHE_NATIVE_SOURCE,
    FhirAuxResource,
    FhirSource,
    Observation,
    Organization,
    fhir_source_uri,
)
from core.utils import generate_observation_value_attachment_data

from .utils import Code, add_observations

_CLINICAL_STATUS = {
    "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]
}


def _condition(patient_id, **extra):
    # Condition.subject and Condition.clinicalStatus are required by FHIR R5.
    return {
        "resourceType": "Condition",
        "subject": {"reference": f"Patient/{patient_id}"},
        "clinicalStatus": _CLINICAL_STATUS,
        **extra,
    }


@pytest.fixture
def fhir_source(patient, device):
    return FhirSource.objects.create(
        patient=patient, data_source=device, label="Patient EHR", fhir_base_url="https://ehr.example/fhir"
    )


def _src(fhir_source):
    """Kwargs adding the X-JHE-FHIR-Source-ID header to a test-client request."""
    return {"HTTP_X_JHE_FHIR_SOURCE_ID": str(fhir_source.id)}


# ---------------------------------------------------------------------------
# Auxiliary resource CRUD + source header
# ---------------------------------------------------------------------------


def test_aux_create_and_read(api_client, patient, fhir_source):
    body = _condition(patient.id, id="cond-1", code={"text": "Hypertension"})
    r = api_client.post("/FHIR/R5/Condition", body, **_src(fhir_source))
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["resourceType"] == "Condition"
    assert created["code"] == {"text": "Hypertension"}

    # The FHIR id is a UUID (the FhirAuxResource pk); the row links the source and its patient.
    assert uuid.UUID(created["id"])
    aux = FhirAuxResource.objects.get(pk=created["id"])
    assert aux.resource_type == "Condition"
    assert aux.fhir_source_id == fhir_source.id
    assert aux.fhir_source.patient_id == patient.id
    # fhir_resource_id comes from the body id; patient_fhir_id from subject.reference.
    assert aux.fhir_resource_id == "cond-1"
    assert aux.patient_fhir_id == str(patient.id)

    r = api_client.get(f"/FHIR/R5/Condition/{created['id']}", **_src(fhir_source))
    assert r.status_code == 200, r.text
    assert r.json() == created


def test_aux_write_requires_source_header_400(api_client, patient, fhir_source):
    # A write requires the source header; a read does not.
    assert api_client.post("/FHIR/R5/Condition", _condition(patient.id)).status_code == 400
    assert api_client.get("/FHIR/R5/Condition").status_code == 200


def test_aux_read_without_header_shows_all_accessible(api_client, patient, fhir_source):
    # Create (a source is required), then read -> visible (practitioner's org patient). The source
    # header is write-only, so passing it on a read changes nothing (it is ignored, not resolved).
    api_client.post("/FHIR/R5/Condition", _condition(patient.id), **_src(fhir_source))
    assert api_client.get("/FHIR/R5/Condition").json()["total"] == 1
    assert api_client.get("/FHIR/R5/Condition", **_src(fhir_source)).json()["total"] == 1


def test_aux_unknown_source_400(api_client, patient):
    r = api_client.post("/FHIR/R5/Condition", _condition(patient.id), HTTP_X_JHE_FHIR_SOURCE_ID="999999")
    assert r.status_code == 400, r.text


def test_aux_source_for_unauthorized_patient_403(api_client, device):
    # A source whose patient the practitioner cannot access (different, unshared org).
    from core.models import JheUser

    other = Organization.objects.create(name="Other", type="other")
    stranger = JheUser.objects.create_user(email="stranger@example.org", user_type="patient").patient
    stranger.organizations.add(other)
    stranger_source = FhirSource.objects.create(
        patient=stranger, data_source=device, label="x", fhir_base_url="https://x/fhir"
    )

    r = api_client.post("/FHIR/R5/Condition", _condition(stranger.id), **_src(stranger_source))
    assert r.status_code == 403, r.text


def test_aux_create_invalid_fhir_400(api_client, fhir_source):
    # Condition.subject is required by FHIR R5; a body missing it is rejected.
    r = api_client.post("/FHIR/R5/Condition", {"resourceType": "Condition"}, **_src(fhir_source))
    assert r.status_code == 400, r.text


def test_aux_patient_fhir_id_for_patient_resource(api_client, patient, fhir_source):
    # For a Patient aux resource, patient_fhir_id is the resource's own id.
    r = api_client.post("/FHIR/R5/Patient", {"resourceType": "Patient", "id": "ext-pat-9"}, **_src(fhir_source))
    assert r.status_code == 201, r.text
    aux = FhirAuxResource.objects.get(pk=r.json()["id"])
    assert aux.patient_fhir_id == "ext-pat-9"
    assert aux.fhir_resource_id == "ext-pat-9"


def test_aux_patient_user_scoped_to_self(patient, fhir_source):
    client = APIClient()
    client.default_format = "json"
    client.force_authenticate(patient.jhe_user)

    # A patient user is scoped to themselves via the token; the source must be theirs.
    r = client.post("/FHIR/R5/Condition", _condition(patient.id, code={"text": "self"}), **_src(fhir_source))
    assert r.status_code == 201, r.text
    assert FhirAuxResource.objects.get(pk=r.json()["id"]).fhir_source.patient_id == patient.id

    r = client.get("/FHIR/R5/Condition", **_src(fhir_source))
    assert r.status_code == 200
    assert r.json()["total"] == 1


def test_aux_search_returns_searchset_bundle(api_client, patient, fhir_source):
    for i in range(3):
        assert (
            api_client.post(
                "/FHIR/R5/Condition", _condition(patient.id, code={"text": f"c{i}"}), **_src(fhir_source)
            ).status_code
            == 201
        )

    r = api_client.get("/FHIR/R5/Condition", **_src(fhir_source))
    assert r.status_code == 200, r.text
    bundle = r.json()
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "searchset"
    assert bundle["total"] == 3
    assert {e["resource"]["resourceType"] for e in bundle["entry"]} == {"Condition"}


def test_aux_search_is_scoped_by_resource_type(api_client, patient, fhir_source):
    api_client.post("/FHIR/R5/Condition", _condition(patient.id), **_src(fhir_source))
    r = api_client.get("/FHIR/R5/QuestionnaireResponse", **_src(fhir_source))
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 0


def test_questionnaire_response_search_by_patient(api_client, patient, fhir_source):
    # Matches the Bruno "List QuestionnaireResponses for Patient" example: a write via the
    # source header, then a read by the `patient` query param alone (no header needed).
    body = {
        "resourceType": "QuestionnaireResponse",
        "status": "completed",
        "questionnaire": "Questionnaire/weekly-symptom-severity-vas",
        "subject": {"reference": f"Patient/{patient.id}"},
        "authored": "2026-05-28T14:30:00Z",
    }
    r = api_client.post("/FHIR/R5/QuestionnaireResponse", body, **_src(fhir_source))
    assert r.status_code == 201, r.text

    r = api_client.get("/FHIR/R5/QuestionnaireResponse", {"patient": patient.id})
    assert r.status_code == 200, r.text
    bundle = r.json()
    assert bundle["total"] == 1
    assert bundle["entry"][0]["resource"]["resourceType"] == "QuestionnaireResponse"


def test_aux_put_replaces_and_patch_merges(api_client, patient, fhir_source):
    created = api_client.post(
        "/FHIR/R5/Condition",
        _condition(patient.id, code={"text": "old"}, recordedDate="2020-01-01"),
        **_src(fhir_source),
    ).json()
    cid = created["id"]

    # PUT replaces the whole body (id is a UUID -> routed to aux).
    r = api_client.put(f"/FHIR/R5/Condition/{cid}", _condition(patient.id, code={"text": "new"}), **_src(fhir_source))
    assert r.status_code == 200, r.text
    assert r.json()["code"] == {"text": "new"}
    assert "recordedDate" not in r.json()

    # PATCH merges at the top level.
    r = api_client.patch(f"/FHIR/R5/Condition/{cid}", {"recordedDate": "2021-02-02"}, **_src(fhir_source))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recordedDate"] == "2021-02-02"
    assert body["code"] == {"text": "new"}  # preserved from before


def test_aux_delete(api_client, patient, fhir_source):
    cid = api_client.post("/FHIR/R5/Condition", _condition(patient.id), **_src(fhir_source)).json()["id"]
    r = api_client.delete(f"/FHIR/R5/Condition/{cid}", **_src(fhir_source))
    assert r.status_code == 204, r.text
    assert not FhirAuxResource.objects.filter(pk=cid).exists()
    assert api_client.get(f"/FHIR/R5/Condition/{cid}", **_src(fhir_source)).status_code == 404


def test_aux_unsupported_resource_type_404(api_client):
    assert api_client.get("/FHIR/R5/Bogus").status_code == 404
    assert api_client.post("/FHIR/R5/Bogus", {"resourceType": "Bogus"}).status_code == 404


# ---------------------------------------------------------------------------
# Mapped resource read-by-id (integer id) vs aux (UUID id)
# ---------------------------------------------------------------------------


def test_observation_read_by_id(api_client, patient, hr_study):
    add_observations(patient=patient, code=Code.HeartRate, n=1)
    obs = Observation.objects.get(subject_patient=patient)
    r = api_client.get(f"/FHIR/R5/Observation/{obs.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resourceType"] == "Observation"
    assert body["id"] == str(obs.id)
    assert body["subject"]["reference"] == f"Patient/{patient.id}"
    # subject.identifier carries the patient's jheUserId, distinct from the record id (issue #602)
    assert body["subject"]["identifier"] == {
        "system": "https://jupyterhealth.org/fhir/identifier/jhe-user-id",
        "value": str(patient.jhe_user_id),
    }


def test_observation_read_by_id_not_found(api_client, patient, hr_study):
    assert api_client.get("/FHIR/R5/Observation/999999").status_code == 404


def test_patient_read_by_id(api_client, patient):
    r = api_client.get(f"/FHIR/R5/Patient/{patient.id}")
    assert r.status_code == 200, r.text
    assert r.json()["resourceType"] == "Patient"
    assert r.json()["id"] == str(patient.id)


def test_uuid_id_routes_to_aux_read(api_client, patient, fhir_source):
    cid = api_client.post("/FHIR/R5/Condition", _condition(patient.id), **_src(fhir_source)).json()["id"]
    # A UUID id is unambiguously a FhirAuxResource lookup.
    assert api_client.get(f"/FHIR/R5/Condition/{cid}", **_src(fhir_source)).status_code == 200
    assert api_client.get(f"/FHIR/R5/Condition/{uuid.uuid4()}", **_src(fhir_source)).status_code == 404


# ---------------------------------------------------------------------------
# Observation create routing (OMH -> Django model, otherwise -> aux)
# ---------------------------------------------------------------------------


def test_observation_omh_create_maps_columns(api_client, device, hr_study, patient):
    record = generate_observation_value_attachment_data(Code.HeartRate.value)
    resource = {
        "resourceType": "Observation",
        "status": "final",
        "code": {"coding": [{"system": Code.OpenMHealth.value, "code": Code.HeartRate.value}]},
        "subject": {"reference": f"Patient/{patient.id}"},
        "device": {"reference": f"Device/{device.id}"},
        "valueAttachment": {
            "contentType": "application/json",
            "data": base64.b64encode(json.dumps(record).encode()).decode(),
        },
    }
    r = api_client.post("/FHIR/R5/Observation", resource)
    assert r.status_code == 201, r.text
    # OMH code -> a Django Observation row (integer id), not aux. No source header needed.
    obs = Observation.objects.get(subject_patient=patient)
    assert obs.codeable_concept is not None
    assert obs.omh_data["body"] == record["body"]
    assert not FhirAuxResource.objects.filter(resource_type="Observation").exists()

    read = api_client.get(f"/FHIR/R5/Observation/{obs.id}").json()
    assert read["code"]["coding"][0]["system"] == Code.OpenMHealth.value
    # The __criteria annotation never leaks into the rendered resource.
    assert "__criteria" not in json.dumps(read)


def _non_omh_observation(patient_id, payload):
    return {
        "resourceType": "Observation",
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "85354-9", "display": "Blood pressure panel"}]},
        "subject": {"reference": f"Patient/{patient_id}"},
        "valueAttachment": {
            "contentType": "application/json",
            "data": base64.b64encode(json.dumps(payload).encode()).decode(),
        },
    }


def test_observation_non_omh_create_goes_to_aux(api_client, patient, fhir_source):
    # A non-OMH (LOINC) observation: no Django row, stored verbatim in FhirAuxResource.
    resource = _non_omh_observation(patient.id, {"systolic": 120, "diastolic": 80})
    r = api_client.post("/FHIR/R5/Observation", resource, **_src(fhir_source))
    assert r.status_code == 201, r.text
    assert not Observation.objects.filter(subject_patient=patient).exists()

    aux = FhirAuxResource.objects.get(resource_type="Observation")
    assert aux.fhir_source.patient_id == patient.id
    assert aux.fhir_source_id == fhir_source.id
    assert aux.patient_fhir_id == str(patient.id)
    assert aux.fhir_data["code"]["coding"][0]["system"] == "http://loinc.org"

    # Read by the aux UUID returns the body verbatim (value NOT double-encoded).
    read = api_client.get(f"/FHIR/R5/Observation/{aux.id}", **_src(fhir_source)).json()
    assert read["code"]["coding"][0]["code"] == "85354-9"
    assert read["valueAttachment"]["data"] == resource["valueAttachment"]["data"]


def test_observation_non_omh_skips_scope_consent(api_client, patient, fhir_source):
    # The patient has consented to no scopes, yet a non-OMH observation is accepted via aux.
    assert not patient.consolidated_consented_scopes().exists()
    r = api_client.post("/FHIR/R5/Observation", _non_omh_observation(patient.id, {"x": 1}), **_src(fhir_source))
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# Writes to read/search-only mapped types fall through to aux
# ---------------------------------------------------------------------------


def test_patient_create_goes_to_aux(api_client, patient, fhir_source):
    # Patient is read/search-only against the Django model; a FHIR create lands in aux.
    r = api_client.post(
        "/FHIR/R5/Patient", {"resourceType": "Patient", "name": [{"family": "Doe"}]}, **_src(fhir_source)
    )
    assert r.status_code == 201, r.text
    assert uuid.UUID(r.json()["id"])
    assert FhirAuxResource.objects.filter(resource_type="Patient", fhir_source__patient=patient).exists()


def test_group_create_goes_to_aux(api_client, patient, fhir_source):
    group = {"resourceType": "Group", "type": "person", "membership": "enumerated"}
    r = api_client.post("/FHIR/R5/Group", group, **_src(fhir_source))
    assert r.status_code == 201, r.text
    assert FhirAuxResource.objects.filter(resource_type="Group", fhir_source__patient=patient).exists()


def test_organization_create_goes_to_aux(api_client, patient, fhir_source):
    r = api_client.post(
        "/FHIR/R5/Organization", {"resourceType": "Organization", "name": "Aux Org"}, **_src(fhir_source)
    )
    assert r.status_code == 201, r.text
    assert FhirAuxResource.objects.filter(resource_type="Organization", fhir_source__patient=patient).exists()


# ---------------------------------------------------------------------------
# meta.source provenance stamping
# ---------------------------------------------------------------------------

_EXT_BASE = "https://jupyterhealth.org/fhir/StructureDefinition"


def test_aux_create_stamps_meta_source(api_client, patient, fhir_source):
    created = api_client.post("/FHIR/R5/Condition", _condition(patient.id), **_src(fhir_source)).json()
    aux = FhirAuxResource.objects.get(pk=created["id"])
    # meta.source names the FhirSource, on both the stored body and the response; the patient
    # attribution extension is carried alongside it.
    assert aux.fhir_data["meta"]["source"] == fhir_source_uri(fhir_source.id)
    assert created["meta"]["source"] == fhir_source_uri(fhir_source.id)
    assert f"{_EXT_BASE}/patient-id" in {e["url"] for e in aux.fhir_data.get("extension", [])}


def test_aux_create_via_meta_source_no_header(api_client, patient, fhir_source):
    # The preferred ingest path: no header, the body's meta.source names the FhirSource.
    body = _condition(patient.id, meta={"source": fhir_source_uri(fhir_source.id)})
    r = api_client.post("/FHIR/R5/Condition", body)
    assert r.status_code == 201, r.text
    assert FhirAuxResource.objects.get(pk=r.json()["id"]).fhir_source_id == fhir_source.id


def test_aux_write_header_wins_over_meta_source(api_client, patient, device, fhir_source):
    # Header and body name different sources -> the header is authoritative.
    other = FhirSource.objects.create(
        patient=patient, data_source=device, label="other", fhir_base_url="https://other/fhir"
    )
    body = _condition(patient.id, meta={"source": fhir_source_uri(other.id)})
    r = api_client.post("/FHIR/R5/Condition", body, **_src(fhir_source))
    assert r.status_code == 201, r.text
    aux = FhirAuxResource.objects.get(pk=r.json()["id"])
    assert aux.fhir_source_id == fhir_source.id  # the header, not the body's meta.source
    assert aux.fhir_data["meta"]["source"] == fhir_source_uri(fhir_source.id)


# ---------------------------------------------------------------------------
# Search hits exactly one store, chosen by _source (no union)
# ---------------------------------------------------------------------------


def test_search_default_returns_mapped_only(api_client, patient, organization, fhir_source):
    # A mapped type with no _source returns the mapped Django rows only -- the aux Organization
    # is NOT unioned in.
    api_client.post("/FHIR/R5/Organization", {"resourceType": "Organization", "name": "Aux Org"}, **_src(fhir_source))
    names = {e["resource"].get("name") for e in api_client.get("/FHIR/R5/Organization").json()["entry"]}
    assert organization.name in names
    assert "Aux Org" not in names


def test_search_source_jhe_returns_mapped_only(api_client, patient, organization, fhir_source):
    api_client.post("/FHIR/R5/Organization", {"resourceType": "Organization", "name": "Aux Org"}, **_src(fhir_source))
    bundle = api_client.get("/FHIR/R5/Organization", {"_source": JHE_NATIVE_SOURCE}).json()
    names = {e["resource"].get("name") for e in bundle["entry"]}
    assert organization.name in names
    assert "Aux Org" not in names


def test_search_source_one_fhir_source_returns_that_aux_only(api_client, patient, organization, fhir_source):
    # _source=<base>/<id> targets that one source's aux rows; the mapped Organization is excluded.
    api_client.post("/FHIR/R5/Organization", {"resourceType": "Organization", "name": "Aux Org"}, **_src(fhir_source))
    bundle = api_client.get("/FHIR/R5/Organization", {"_source": fhir_source_uri(fhir_source.id)}).json()
    assert {e["resource"].get("name") for e in bundle["entry"]} == {"Aux Org"}


def test_search_source_below_returns_all_imported(api_client, patient, device, fhir_source):
    # Two sources, one Condition each; :below on the fhir-source base returns both.
    other = FhirSource.objects.create(patient=patient, data_source=device, label="o2", fhir_base_url="https://o2/fhir")
    api_client.post("/FHIR/R5/Condition", _condition(patient.id, code={"text": "a"}), **_src(fhir_source))
    api_client.post("/FHIR/R5/Condition", _condition(patient.id, code={"text": "b"}), **_src(other))
    bundle = api_client.get("/FHIR/R5/Condition", {"_source:below": f"{JHE_FHIR_SOURCE_BASE}/"}).json()
    assert bundle["total"] == 2


def test_search_unrecognized_source_returns_empty(api_client, patient, fhir_source):
    api_client.post("/FHIR/R5/Condition", _condition(patient.id), **_src(fhir_source))
    bundle = api_client.get("/FHIR/R5/Condition", {"_source": "https://external.example/fhir"}).json()
    assert bundle["total"] == 0


def test_read_search_ignores_source_header(api_client, patient, fhir_source):
    # The source header is write-only: a read never resolves it, so even a bogus value is ignored
    # (a resolved bogus source would 400) and all accessible rows are returned.
    api_client.post("/FHIR/R5/Condition", _condition(patient.id), **_src(fhir_source))
    r = api_client.get("/FHIR/R5/Condition", HTTP_X_JHE_FHIR_SOURCE_ID="999999")
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1


# ---------------------------------------------------------------------------
# Update / delete routing on read-only mapped types
# ---------------------------------------------------------------------------


def test_update_delete_on_mapped_integer_id_returns_405(api_client, patient):
    # An integer id targets the read/search-only Django model -> update/delete are refused.
    assert api_client.put(f"/FHIR/R5/Patient/{patient.id}", {"resourceType": "Patient"}).status_code == 405
    assert api_client.patch(f"/FHIR/R5/Patient/{patient.id}", {"resourceType": "Patient"}).status_code == 405
    assert api_client.delete(f"/FHIR/R5/Patient/{patient.id}").status_code == 405


def test_read_only_mapped_resource_allows_read(api_client, patient):
    assert api_client.get(f"/FHIR/R5/Patient/{patient.id}").status_code == 200


# ---------------------------------------------------------------------------
# US Core search parameters -- auxiliary store (JSONB query builder)
# ---------------------------------------------------------------------------

_CATEGORY_PROBLEM = {
    "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-category", "code": "problem-list-item"}]
}
_CATEGORY_ENCOUNTER = {
    "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-category", "code": "encounter-diagnosis"}]
}


def _post_condition(api_client, fhir_source, patient, **extra):
    r = api_client.post("/FHIR/R5/Condition", _condition(patient.id, **extra), **_src(fhir_source))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _ids(bundle):
    return {entry["resource"]["id"] for entry in bundle.get("entry", [])}


def test_aux_token_filter_clinical_status(api_client, patient, fhir_source):
    active = _post_condition(api_client, fhir_source, patient)  # fixture clinicalStatus is 'active'
    inactive_status = {"coding": [{"system": _CLINICAL_STATUS["coding"][0]["system"], "code": "inactive"}]}
    _post_condition(api_client, fhir_source, patient, clinicalStatus=inactive_status)
    bundle = api_client.get("/FHIR/R5/Condition", {"clinical-status": "active"}).json()
    assert _ids(bundle) == {active}


def test_aux_token_filter_with_system(api_client, patient, fhir_source):
    problem = _post_condition(api_client, fhir_source, patient, category=[_CATEGORY_PROBLEM])
    _post_condition(api_client, fhir_source, patient, category=[_CATEGORY_ENCOUNTER])
    system = _CATEGORY_PROBLEM["coding"][0]["system"]
    # system|code matches; a wrong system does not.
    assert _ids(api_client.get("/FHIR/R5/Condition", {"category": f"{system}|problem-list-item"}).json()) == {problem}
    assert api_client.get("/FHIR/R5/Condition", {"category": "http://wrong|problem-list-item"}).json()["total"] == 0


def test_aux_token_comma_is_or_and_repeat_is_and(api_client, patient, fhir_source):
    problem = _post_condition(api_client, fhir_source, patient, category=[_CATEGORY_PROBLEM])
    encounter = _post_condition(api_client, fhir_source, patient, category=[_CATEGORY_ENCOUNTER])
    # Comma within one param ORs the values.
    both = api_client.get("/FHIR/R5/Condition", {"category": "problem-list-item,encounter-diagnosis"}).json()
    assert _ids(both) == {problem, encounter}
    # Repeated params AND: no single Condition has both categories, so the result is empty.
    r = api_client.get("/FHIR/R5/Condition?category=problem-list-item&category=encounter-diagnosis")
    assert r.json()["total"] == 0


def test_aux_reference_filter_encounter(api_client, patient, fhir_source):
    matched = _post_condition(api_client, fhir_source, patient, encounter={"reference": "Encounter/enc-1"})
    _post_condition(api_client, fhir_source, patient, encounter={"reference": "Encounter/enc-2"})
    # Full reference and bare id both resolve.
    assert _ids(api_client.get("/FHIR/R5/Condition", {"encounter": "Encounter/enc-1"}).json()) == {matched}
    assert _ids(api_client.get("/FHIR/R5/Condition", {"encounter": "enc-1"}).json()) == {matched}


def test_aux_date_filter_comparators(api_client, patient, fhir_source):
    a = _post_condition(api_client, fhir_source, patient, recordedDate="2020-01-01")
    b = _post_condition(api_client, fhir_source, patient, recordedDate="2021-06-15")
    c = _post_condition(api_client, fhir_source, patient, recordedDate="2022-12-31")
    assert _ids(api_client.get("/FHIR/R5/Condition", {"recorded-date": "ge2021-01-01"}).json()) == {b, c}
    assert _ids(api_client.get("/FHIR/R5/Condition", {"recorded-date": "le2021-12-31"}).json()) == {a, b}
    assert _ids(api_client.get("/FHIR/R5/Condition", {"recorded-date": "2021-06-15"}).json()) == {b}
    # A range is expressed as two AND-ed comparators.
    ranged = api_client.get("/FHIR/R5/Condition?recorded-date=ge2021-01-01&recorded-date=le2021-12-31").json()
    assert _ids(ranged) == {b}


def test_aux_sort_by_date(api_client, patient, fhir_source):
    a = _post_condition(api_client, fhir_source, patient, recordedDate="2021-06-15")
    b = _post_condition(api_client, fhir_source, patient, recordedDate="2020-01-01")
    c = _post_condition(api_client, fhir_source, patient, recordedDate="2022-12-31")
    order = [e["resource"]["id"] for e in api_client.get("/FHIR/R5/Condition", {"_sort": "date"}).json()["entry"]]
    assert order == [b, a, c]
    order_desc = [e["resource"]["id"] for e in api_client.get("/FHIR/R5/Condition", {"_sort": "-date"}).json()["entry"]]
    assert order_desc == [c, a, b]


def test_aux_summary_count(api_client, patient, fhir_source):
    for _ in range(3):
        _post_condition(api_client, fhir_source, patient)
    bundle = api_client.get("/FHIR/R5/Condition", {"_summary": "count"}).json()
    assert bundle["type"] == "searchset"
    assert bundle["total"] == 3
    assert bundle["entry"] == []


def test_aux_string_filter_starts_with(api_client, patient, fhir_source):
    # Location is a pure-aux resource with a string 'name' search param.
    def post_location(name):
        r = api_client.post("/FHIR/R5/Location", {"resourceType": "Location", "name": name}, **_src(fhir_source))
        assert r.status_code == 201, r.text
        return r.json()["id"]

    north = post_location("North Clinic")
    post_location("South Clinic")
    # Case-insensitive starts-with.
    assert _ids(api_client.get("/FHIR/R5/Location", {"name": "north"}).json()) == {north}
    # A non-prefix substring does not match.
    assert api_client.get("/FHIR/R5/Location", {"name": "Clinic"}).json()["total"] == 0


def test_aux_code_param_filters_scalar_code(api_client, patient, fhir_source):
    # CarePlan.status is a plain FHIR code (a scalar), exercised by the 'status' code-type param.
    def post_careplan(status):
        body = {
            "resourceType": "CarePlan",
            "status": status,
            "intent": "plan",
            "subject": {"reference": f"Patient/{patient.id}"},
        }
        r = api_client.post("/FHIR/R5/CarePlan", body, **_src(fhir_source))
        assert r.status_code == 201, r.text
        return r.json()["id"]

    active = post_careplan("active")
    post_careplan("completed")
    assert _ids(api_client.get("/FHIR/R5/CarePlan", {"status": "active"}).json()) == {active}


def test_aux_identifier_param_matches_value_and_system(api_client, patient, fhir_source):
    def post_patient(system, value):
        body = {"resourceType": "Patient", "identifier": [{"system": system, "value": value}]}
        r = api_client.post("/FHIR/R5/Patient", body, **_src(fhir_source))
        assert r.status_code == 201, r.text
        return r.json()["id"]

    mrn = post_patient("http://hospital.example/mrn", "A123")
    post_patient("http://hospital.example/mrn", "B456")
    src = {"_source": fhir_source_uri(fhir_source.id)}  # Patient is mapped; _source targets aux rows
    assert _ids(api_client.get("/FHIR/R5/Patient", {**src, "identifier": "A123"}).json()) == {mrn}
    assert _ids(
        api_client.get("/FHIR/R5/Patient", {**src, "identifier": "http://hospital.example/mrn|A123"}).json()
    ) == {mrn}
    assert api_client.get("/FHIR/R5/Patient", {**src, "identifier": "http://wrong|A123"}).json()["total"] == 0


def test_aux_search_combines_with_source_filter(api_client, patient, device, fhir_source):
    other = FhirSource.objects.create(
        patient=patient, data_source=device, label="Other", fhir_base_url="https://other.example/fhir"
    )
    here = _post_condition(api_client, fhir_source, patient, recordedDate="2021-01-01")
    api_client.post("/FHIR/R5/Condition", _condition(patient.id, recordedDate="2021-01-01"), **_src(other))
    # _source selects one source's rows; the JSONB filter narrows within it.
    bundle = api_client.get(
        "/FHIR/R5/Condition", {"_source": fhir_source_uri(fhir_source.id), "recorded-date": "2021-01-01"}
    ).json()
    assert _ids(bundle) == {here}


# ---------------------------------------------------------------------------
# US Core search parameters -- mapped store (Django ORM)
# ---------------------------------------------------------------------------


def test_mapped_patient_string_and_date_filters(api_client, patient):
    patient.name_family = "Smith"
    patient.name_given = "Jane"
    patient.birth_date = "1990-05-15"
    patient.save()
    # family / given are case-insensitive starts-with; name matches either.
    assert str(patient.id) in {
        e["resource"]["id"] for e in api_client.get("/FHIR/R5/Patient", {"family": "smi"}).json()["entry"]
    }
    assert api_client.get("/FHIR/R5/Patient", {"family": "xyz"}).json()["total"] == 0
    assert api_client.get("/FHIR/R5/Patient", {"name": "jan"}).json()["total"] == 1
    # birthdate is a date param.
    assert api_client.get("/FHIR/R5/Patient", {"birthdate": "1990-05-15"}).json()["total"] == 1
    assert api_client.get("/FHIR/R5/Patient", {"birthdate": "ge1991-01-01"}).json()["total"] == 0


def test_mapped_observation_status_const_and_date(api_client, patient, hr_study):
    add_observations(patient=patient, code=Code.HeartRate, n=2)
    Observation.objects.filter(subject_patient=patient).update(effective_date_time="2021-06-15T00:00:00Z")
    # status is rendered as the constant 'final': the matching value returns rows, others none.
    assert api_client.get("/FHIR/R5/Observation", {"status": "final"}).json()["total"] == 2
    assert api_client.get("/FHIR/R5/Observation", {"status": "amended"}).json()["total"] == 0
    # date filters on effective[x].
    assert api_client.get("/FHIR/R5/Observation", {"date": "ge2021-01-01"}).json()["total"] == 2
    assert api_client.get("/FHIR/R5/Observation", {"date": "ge2022-01-01"}).json()["total"] == 0
