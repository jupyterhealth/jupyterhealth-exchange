"""Tests for the unified FHIR resource endpoint (core/views/fhir.py).

Covers the reworked routing: a Django model backs only the JHE-system view of a resource and
everything else lands in FhirAuxResource (UUID id, linked to a FhirSource named by the
X-JHE-FHIR-Source-ID header); search is a union of mapped + aux rows; writes for read/search-only
mapped types fall through to aux; only OMH Observations write to the Django model.
"""

import base64
import json
import uuid

import pytest
from rest_framework.test import APIClient

from core.models import FhirAuxResource, FhirSource, Observation, Organization
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
    # Create (header required), then read without a header -> still visible (practitioner's
    # org patient), and reading with the header scopes to that source's patient too.
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
# Search is a union of mapped Django rows + aux rows
# ---------------------------------------------------------------------------


def test_search_unions_mapped_and_aux(api_client, patient, organization, fhir_source):
    # Mapped: the practitioner's own organization. Aux: an Organization stored in FhirAuxResource.
    api_client.post("/FHIR/R5/Organization", {"resourceType": "Organization", "name": "Aux Org"}, **_src(fhir_source))
    # The source header is required to include the aux portion of a mapped+aux search.
    r = api_client.get("/FHIR/R5/Organization", **_src(fhir_source))
    assert r.status_code == 200, r.text
    bundle = r.json()
    assert bundle["total"] >= 2
    names = {e["resource"].get("name") for e in bundle["entry"]}
    assert "Aux Org" in names  # the aux row
    assert organization.name in names  # the mapped row
    assert {e["resource"]["resourceType"] for e in bundle["entry"]} == {"Organization"}


def test_union_search_without_header_includes_mapped_and_accessible_aux(api_client, hr_study, patient, fhir_source):
    # Without a source header a read returns the union of mapped studies and every accessible
    # aux Group (here the one just stored, for an org patient of the practitioner).
    aux_id = api_client.post(
        "/FHIR/R5/Group", {"resourceType": "Group", "type": "person", "membership": "enumerated"}, **_src(fhir_source)
    ).json()["id"]
    bundle = api_client.get("/FHIR/R5/Group").json()
    ids = {e["resource"]["id"] for e in bundle["entry"]}
    assert str(hr_study.id) in ids  # mapped study
    assert aux_id in ids  # accessible aux Group
    assert {e["resource"]["resourceType"] for e in bundle["entry"]} == {"Group"}


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
# Backward-compatible lowercase /fhir/r5/ alias
# ---------------------------------------------------------------------------


def test_legacy_lowercase_path_alias_still_works(api_client, patient, fhir_source):
    cid = api_client.post("/FHIR/R5/Condition", _condition(patient.id, code={"text": "x"}), **_src(fhir_source)).json()[
        "id"
    ]
    r = api_client.get(f"/fhir/r5/Condition/{cid}", **_src(fhir_source))
    assert r.status_code == 200, r.text
    assert r.json()["id"] == str(cid)
