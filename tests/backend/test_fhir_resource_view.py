"""Tests for the unified FHIR resource endpoint (core/views/fhir.py).

Covers the reworked routing: a Django model backs only the JHE-system view of a resource and
everything else lands in FhirAuxResource (UUID id, linked to a FhirSource named by the
X-JHE-FHIR-Source-ID header or the body's meta.source); a search hits exactly one store, chosen by
the _source param (no union); writes for read/search-only mapped types fall through to aux; only
OMH / IEEE 1752 Observations write to the Django model.
"""

import base64
import json
import uuid

import pytest
from rest_framework.test import APIClient

from core.models import (
    JHE_FHIR_SOURCE_BASE,
    JHE_NATIVE_SOURCE,
    EhrBrand,
    EhrBrandLocation,
    FhirAuxResource,
    FhirSource,
    Observation,
    Organization,
    fhir_source_uri,
)
from core.utils import generate_observation_value_attachment_data

from .utils import Code, add_observations, add_patient_to_study, create_study

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
    return FhirSource.objects.create(patient=patient, data_source=device, label="Patient EHR")


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


def _patient_client(patient):
    client = APIClient()
    client.default_format = "json"
    client.force_authenticate(patient.jhe_user)
    return client


def test_fhir_source_registration_always_creates(patient, device):
    # A source stores no endpoint and is identified by its pk, so there is nothing to match a
    # previous registration against — and nothing needs to be. Every Connect gets its own source,
    # which is its own identifier namespace, so upstream ids never collide across runs.
    client = _patient_client(patient)
    body = {"label": "Epic / EHR Patient Portal", "data_source": device.id}
    first = client.post("/api/v1/fhir_sources", body)
    again = client.post("/api/v1/fhir_sources", body)
    assert first.status_code == 201 and again.status_code == 201
    assert again.json()["id"] != first.json()["id"]
    assert FhirSource.objects.filter(patient=patient).count() == 2
    # The endpoint is not a field any more; a client that sends one is simply ignored.
    assert "fhir_base_url" not in first.json()


def test_aux_create_moves_upstream_id_into_a_source_namespaced_identifier(api_client, patient, fhir_source):
    # On a FHIR create the logical id is the server's to assign, so the EHR's own id moves into
    # an identifier namespaced by the FhirSource — the same URI meta.source carries.
    first = api_client.post("/FHIR/R5/Condition", _condition(patient.id, id="cond-9"), **_src(fhir_source))
    assert first.status_code == 201, first.text
    body = first.json()
    assert {"system": fhir_source_uri(fhir_source.pk), "value": "cond-9"} in body["identifier"]
    # The JHE UUID is the resource's id; the upstream id is kept on the row for uniqueness.
    assert body["id"] != "cond-9"
    assert FhirAuxResource.objects.get(pk=body["id"]).fhir_resource_id == "cond-9"


def _provenance(patient_id, resource_id=None):
    body = {
        "resourceType": "Provenance",
        "target": [{"reference": f"Patient/{patient_id}"}],
        "recorded": "2026-01-01T00:00:00Z",
        "agent": [{"who": {"reference": "Practitioner/1"}}],
    }
    if resource_id:
        body["id"] = resource_id
    return body


def test_aux_create_for_a_type_with_no_identifier_element(api_client, patient, fhir_source):
    # Provenance has no `identifier` element at all, and fhir.resources forbids extra fields, so
    # the upstream id has nowhere to go in the body. It must still leave the body (the stored id
    # is the JHE UUID) and still key uniqueness through the fhir_resource_id column.
    first = api_client.post("/FHIR/R5/Provenance", _provenance(patient.id, "prov-1"), **_src(fhir_source))
    assert first.status_code == 201, first.text
    assert "identifier" not in first.json()
    row = FhirAuxResource.objects.get(pk=first.json()["id"])
    assert row.fhir_resource_id == "prov-1"
    assert row.fhir_data["id"] == str(row.pk)

    again = api_client.post("/FHIR/R5/Provenance", _provenance(patient.id, "prov-1"), **_src(fhir_source))
    assert again.status_code == 409, again.text
    assert again.json()["issue"][0]["code"] == "duplicate"
    assert FhirAuxResource.objects.filter(resource_type="Provenance", fhir_resource_id="prov-1").count() == 1


def test_aux_create_over_64_char_id_is_accepted_via_the_identifier(api_client, patient, fhir_source):
    # Epic "Unconstrained FHIR IDs" exceed FHIR's 64-char id limit, which R5 validation would
    # reject in the body. In an identifier there is no length limit, and neither has the
    # fhir_resource_id column the uniqueness constraint is enforced on.
    long_id = "e" + "x" * 70
    first = api_client.post("/FHIR/R5/Condition", _condition(patient.id, id=long_id), **_src(fhir_source))
    assert first.status_code == 201, first.text
    assert FhirAuxResource.objects.get(pk=first.json()["id"]).fhir_resource_id == long_id
    assert {"system": fhir_source_uri(fhir_source.pk), "value": long_id} in first.json()["identifier"]

    again = api_client.post("/FHIR/R5/Condition", _condition(patient.id, id=long_id), **_src(fhir_source))
    assert again.status_code == 409, again.text
    assert FhirAuxResource.objects.filter(resource_type="Condition", fhir_resource_id=long_id).count() == 1


def test_aux_create_refuses_a_record_already_stored_under_the_source(api_client, patient, fhir_source):
    # A create never replaces an existing record. Re-posting one already stored under this
    # source is a 409 naming the record it collided with, so the client can decide what to do.
    first = api_client.post(
        "/FHIR/R5/Condition", _condition(patient.id, id="cond-9", code={"text": "old"}), **_src(fhir_source)
    )
    again = api_client.post(
        "/FHIR/R5/Condition", _condition(patient.id, id="cond-9", code={"text": "new"}), **_src(fhir_source)
    )
    assert first.status_code == 201
    assert again.status_code == 409, again.text
    outcome = again.json()
    assert outcome["resourceType"] == "OperationOutcome"
    issue = outcome["issue"][0]
    assert issue["severity"] == "error" and issue["code"] == "duplicate"
    assert first.json()["id"] in issue["diagnostics"]
    # The stored record is untouched: a refused create wrote nothing.
    rows = FhirAuxResource.objects.filter(resource_type="Condition", fhir_resource_id="cond-9")
    assert rows.count() == 1
    assert rows.get().fhir_data["code"] == {"text": "old"}


def test_aux_update_onto_a_taken_upstream_id_is_refused(api_client, patient, fhir_source):
    # Uniqueness is a property of the source, not of the create path: moving a row onto an
    # upstream id a sibling already holds is refused the same way, and neither row changes.
    first = api_client.post("/FHIR/R5/Condition", _condition(patient.id, id="cond-1"), **_src(fhir_source))
    second = api_client.post("/FHIR/R5/Condition", _condition(patient.id, id="cond-2"), **_src(fhir_source))
    assert first.status_code == 201 and second.status_code == 201

    clash = api_client.put(
        f"/FHIR/R5/Condition/{second.json()['id']}",
        _condition(patient.id, id="cond-1", code={"text": "moved"}),
        **_src(fhir_source),
    )
    assert clash.status_code == 409, clash.text
    issue = clash.json()["issue"][0]
    assert issue["code"] == "duplicate"
    assert first.json()["id"] in issue["diagnostics"]
    assert FhirAuxResource.objects.get(pk=second.json()["id"]).fhir_resource_id == "cond-2"


def test_aux_create_same_upstream_id_different_source_stays_separate(api_client, patient, device, fhir_source):
    # The same EHR id from two different sources is two records (two hospitals can both
    # have a "cond-9").
    other = FhirSource.objects.create(patient=patient, data_source=device, label="o2")
    api_client.post("/FHIR/R5/Condition", _condition(patient.id, id="cond-9"), **_src(fhir_source))
    api_client.post("/FHIR/R5/Condition", _condition(patient.id, id="cond-9"), **_src(other))
    assert FhirAuxResource.objects.filter(resource_type="Condition", fhir_resource_id="cond-9").count() == 2


def test_aux_create_without_upstream_id_always_creates(api_client, patient, fhir_source):
    # No upstream id -> nothing to be a duplicate of -> both POSTs create.
    for _ in range(2):
        assert api_client.post("/FHIR/R5/Condition", _condition(patient.id), **_src(fhir_source)).status_code == 201
    assert FhirAuxResource.objects.filter(resource_type="Condition").count() == 2


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
    stranger_source = FhirSource.objects.create(patient=stranger, data_source=device, label="x")

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
# Observation create routing (OMH / IEEE 1752 -> Django model, otherwise -> aux)
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


def test_observation_ieee_create_maps_columns(api_client, device, organization, patient):
    # IEEE 1752 is the balloted form of an OMH schema, so JHE treats the two coding systems
    # interchangeably: an IEEE-coded Observation takes the same mapped path as an OMH one.
    study = create_study(name="sleep", organization=organization, codes=[Code.TimeInBed])
    add_patient_to_study(patient=patient, study=study)
    record = {
        "header": {
            "uuid": str(uuid.uuid4()),
            "schema_id": {"namespace": "ieee", "name": "time-in-bed", "version": "1.0"},
            "source_creation_date_time": "2026-01-02T08:00:00Z",
            "modality": "sensed",
        },
        "body": {
            "time_in_bed": {"value": 27000, "unit": "sec"},
            "effective_time_frame": {
                "time_interval": {
                    "start_date_time": "2026-01-01T23:00:00Z",
                    "end_date_time": "2026-01-02T06:30:00Z",
                }
            },
        },
    }
    resource = {
        "resourceType": "Observation",
        "status": "final",
        "code": {"coding": [{"system": Code.IEEE1752.value, "code": Code.TimeInBed.value}]},
        "subject": {"reference": f"Patient/{patient.id}"},
        "device": {"reference": f"Device/{device.id}"},
        "valueAttachment": {
            "contentType": "application/json",
            "data": base64.b64encode(json.dumps(record).encode()).decode(),
        },
    }
    r = api_client.post("/FHIR/R5/Observation", resource)
    assert r.status_code == 201, r.text
    # IEEE code -> a Django Observation row (integer id), not aux. No source header needed.
    obs = Observation.objects.get(subject_patient=patient)
    assert obs.codeable_concept.coding_system == Code.IEEE1752.value
    assert obs.omh_data["body"] == record["body"]
    assert not FhirAuxResource.objects.filter(resource_type="Observation").exists()

    read = api_client.get(f"/FHIR/R5/Observation/{obs.id}").json()
    assert read["code"]["coding"][0]["system"] == Code.IEEE1752.value


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
    other = FhirSource.objects.create(patient=patient, data_source=device, label="other")
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
    other = FhirSource.objects.create(patient=patient, data_source=device, label="o2")
    api_client.post("/FHIR/R5/Condition", _condition(patient.id, code={"text": "a"}), **_src(fhir_source))
    api_client.post("/FHIR/R5/Condition", _condition(patient.id, code={"text": "b"}), **_src(other))
    bundle = api_client.get("/FHIR/R5/Condition", {"_source:below": f"{JHE_FHIR_SOURCE_BASE}/"}).json()
    assert bundle["total"] == 2


def _lab_observation(patient_id, category=None):
    body = {
        "resourceType": "Observation",
        "status": "final",
        "code": {"text": "glucose"},
        "subject": {"reference": f"Patient/{patient_id}"},
    }
    if category:
        body["category"] = [
            {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": category}]}
        ]
    return body


def test_external_source_view_returns_imported_observation_and_plain_search_does_not(api_client, patient, fhir_source):
    # The exact query the browser's Observation + Source=External selection sends: _source:below
    # scoped to the fhir-source base, and nothing else. The imported Observation must be there —
    # and absent from the no-_source search, which serves the JHE-native (mapped) store only.
    r = api_client.post("/FHIR/R5/Observation", _lab_observation(patient.id), **_src(fhir_source))
    assert r.status_code == 201, r.text
    imported_id = r.json()["id"]

    external = api_client.get("/FHIR/R5/Observation", {"_source:below": f"{JHE_FHIR_SOURCE_BASE}/"}).json()
    assert [e["resource"]["id"] for e in external["entry"]] == [imported_id]

    native = api_client.get("/FHIR/R5/Observation").json()
    assert imported_id not in [e["resource"]["id"] for e in native.get("entry", [])]


def test_ad_hoc_url_param_narrows_the_browser_view(api_client, patient, fhir_source):
    # The FHIR browser passes through every URL param that is not a "~" JHE system param, so a
    # filter typed straight into the address bar reaches the server even though no control on
    # the page produces it. This is that request: the Source=External selection's _source:below
    # plus a hand-added &category=laboratory.
    lab = api_client.post("/FHIR/R5/Observation", _lab_observation(patient.id, "laboratory"), **_src(fhir_source))
    vitals = api_client.post("/FHIR/R5/Observation", _lab_observation(patient.id, "vital-signs"), **_src(fhir_source))
    assert lab.status_code == 201 and vitals.status_code == 201

    view = {"_source:below": f"{JHE_FHIR_SOURCE_BASE}/"}
    assert api_client.get("/FHIR/R5/Observation", view).json()["total"] == 2

    narrowed = api_client.get("/FHIR/R5/Observation", {**view, "category": "laboratory"}).json()
    assert [e["resource"]["id"] for e in narrowed["entry"]] == [lab.json()["id"]]


def test_external_source_view_returns_aux_rows_for_native_mapped_types(api_client, organization, patient, fhir_source):
    # The same Source=External selection on a type whose native rows come from a Django model
    # (Organization; Group, Patient, Device and Practitioner share the shape): _source:below
    # returns the imported row and excludes the native one, and vice versa for Source=None.
    api_client.post("/FHIR/R5/Organization", {"resourceType": "Organization", "name": "Aux Org"}, **_src(fhir_source))
    imported = api_client.get("/FHIR/R5/Organization", {"_source:below": f"{JHE_FHIR_SOURCE_BASE}/"}).json()
    assert {e["resource"].get("name") for e in imported["entry"]} == {"Aux Org"}
    native = api_client.get("/FHIR/R5/Organization").json()
    assert "Aux Org" not in {e["resource"].get("name") for e in native["entry"]}


def test_external_source_view_narrows_to_one_patient(api_client, patient, device, fhir_source):
    # The Patient ID box sends the canonical `patient` search param -- the same integer the aux
    # body carries as the .../StructureDefinition/patient-id extension's valueInteger.
    from core.models import JheUser

    other_patient = JheUser.objects.create_user(email="other-pt@example.org", user_type="patient").patient
    other_patient.organizations.add(*patient.organizations.all())
    other_source = FhirSource.objects.create(patient=other_patient, data_source=device, label="o2")
    mine = api_client.post("/FHIR/R5/Observation", _lab_observation(patient.id), **_src(fhir_source)).json()["id"]
    api_client.post("/FHIR/R5/Observation", _lab_observation(other_patient.id), **_src(other_source))

    both = api_client.get("/FHIR/R5/Observation", {"_source:below": f"{JHE_FHIR_SOURCE_BASE}/"}).json()
    assert both["total"] == 2

    narrowed = api_client.get(
        "/FHIR/R5/Observation", {"_source:below": f"{JHE_FHIR_SOURCE_BASE}/", "patient": patient.id}
    ).json()
    assert [e["resource"]["id"] for e in narrowed["entry"]] == [mine]

    # The extension the box is named for carries that same patient id on the stored body.
    extensions = narrowed["entry"][0]["resource"]["extension"]
    assert {
        "url": "https://jupyterhealth.org/fhir/StructureDefinition/patient-id",
        "valueInteger": patient.id,
    } in extensions


_REMEMBER = {"HTTP_X_JHE_REMEMBER_VIEW": "1"}  # the header only the jhe-admin browser sends


def test_search_remembers_every_browser_control(api_client, user, patient, fhir_source):
    # The admin UI restores its controls from these settings: one key per control, written
    # through Practitioner.remember_settings like the studies/observations pages.
    api_client.get(
        "/FHIR/R5/Observation",
        {"_source:below": f"{JHE_FHIR_SOURCE_BASE}/", "patient": patient.id, "_page": 2, "_count": 100},
        **_REMEMBER,
    )
    user.refresh_from_db()
    settings = user.practitioner_profile.settings
    assert settings["current_fhir_resource"] == "Observation"
    assert settings["current_fhir_source"] == f"{JHE_FHIR_SOURCE_BASE}/"
    assert settings["current_fhir_jhe_patient_id"] == str(patient.id)
    # Paging is deliberately not sticky.
    assert "current_fhir_page" not in settings and "current_fhir_count" not in settings


def test_search_forgets_cleared_source_and_patient_controls(api_client, user, patient, fhir_source):
    # A cleared Source ("None (JHE System Data)") or Patient ID box sends no param at all;
    # the stale value must be dropped, not left to come back on the next visit.
    api_client.get(
        "/FHIR/R5/Observation",
        {"_source:below": f"{JHE_FHIR_SOURCE_BASE}/", "patient": patient.id},
        **_REMEMBER,
    )
    api_client.get("/FHIR/R5/Condition", **_REMEMBER)
    user.refresh_from_db()
    settings = user.practitioner_profile.settings
    assert settings["current_fhir_resource"] == "Condition"
    assert "current_fhir_source" not in settings
    assert "current_fhir_jhe_patient_id" not in settings


def test_remembered_settings_reach_the_client_camelcased(api_client, user, patient, fhir_source):
    # The browser reads these off the profile as currentFhirResource / currentFhirSource /
    # currentFhirJhePatientId; pin the serializer spelling the client depends on.
    api_client.get(
        "/FHIR/R5/Observation",
        {"_source:below": f"{JHE_FHIR_SOURCE_BASE}/", "patient": patient.id},
        **_REMEMBER,
    )
    profile = api_client.get("/api/v1/users/profile").json()
    assert profile["settings"]["currentFhirResource"] == "Observation"
    assert profile["settings"]["currentFhirSource"] == f"{JHE_FHIR_SOURCE_BASE}/"
    assert profile["settings"]["currentFhirJhePatientId"] == str(patient.id)


def test_search_without_remember_header_never_touches_the_sticky_view(api_client, user, patient, fhir_source):
    # MCP-server and API-script searches don't send the header; they must not clobber the
    # practitioner's remembered controls.
    api_client.get("/FHIR/R5/Observation", {"_source:below": f"{JHE_FHIR_SOURCE_BASE}/"}, **_REMEMBER)
    api_client.get("/FHIR/R5/Condition", {"code": "http://loinc.org|4548-4"})
    user.refresh_from_db()
    settings = user.practitioner_profile.settings
    assert settings["current_fhir_resource"] == "Observation"
    assert settings["current_fhir_source"] == f"{JHE_FHIR_SOURCE_BASE}/"


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
    other = FhirSource.objects.create(patient=patient, data_source=device, label="Other")
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


def test_mapped_observation_sort_keeps_undated_rows_last(api_client, patient, hr_study):
    # A row with no effective time (extract yields all-None) must not surface as the
    # "newest" record: Postgres sorts NULLs first on DESC unless told otherwise.
    add_observations(patient=patient, code=Code.HeartRate, n=3)
    older, newer, undated = Observation.objects.filter(subject_patient=patient).order_by("id")
    Observation.objects.filter(pk=older.pk).update(effective_date_time="2021-06-15T00:00:00Z")
    Observation.objects.filter(pk=newer.pk).update(effective_date_time="2022-06-15T00:00:00Z")
    Observation.objects.filter(pk=undated.pk).update(
        effective_date_time=None, effective_period_start=None, effective_period_end=None
    )
    asc = [e["resource"]["id"] for e in api_client.get("/FHIR/R5/Observation", {"_sort": "date"}).json()["entry"]]
    assert asc == [str(older.pk), str(newer.pk), str(undated.pk)]
    desc = [e["resource"]["id"] for e in api_client.get("/FHIR/R5/Observation", {"_sort": "-date"}).json()["entry"]]
    assert desc == [str(newer.pk), str(older.pk), str(undated.pk)]


def test_mapped_observation_sort_pages_are_stable_on_ties(api_client, patient, hr_study):
    # Identical sort keys need a tiebreaker for LIMIT/OFFSET paging to be a
    # partition: without one, rows sharing a timestamp can repeat or vanish
    # across pages.
    add_observations(patient=patient, code=Code.HeartRate, n=4)
    Observation.objects.filter(subject_patient=patient).update(effective_date_time="2021-06-15T00:00:00Z")
    seen = []
    for page in (1, 2):
        bundle = api_client.get("/FHIR/R5/Observation", {"_sort": "date", "_count": 2, "_page": page}).json()
        seen += [e["resource"]["id"] for e in bundle["entry"]]
    assert len(seen) == 4
    assert len(set(seen)) == 4


def test_migration_0047_folds_the_dropped_base_url_into_the_label():
    # The endpoint column is dropped, so deployed rows keep a human-readable trace of where they
    # came from in the label -- the source's only human-facing handle once the URL is gone.
    import importlib

    folded_label = importlib.import_module("core.migrations.0047_drop_fhir_source_base_url").folded_label

    assert (
        folded_label("Epic / EHR Patient Portal", "https://e/FHIR/R4")
        == "Epic / EHR Patient Portal — https://e/FHIR/R4"
    )
    assert folded_label("", "https://e/FHIR/R4") == "https://e/FHIR/R4"
    # Already named (the client writes it into the label itself) -- do not append it twice.
    assert folded_label("Epic — https://e/FHIR/R4", "https://e/FHIR/R4") == "Epic — https://e/FHIR/R4"
    assert folded_label("Momentum App", "") == "Momentum App"


def test_fhir_source_records_the_picked_ehr_brand_location(patient, device, db):
    # The facility the patient picked is recorded on the source. It is descriptive: every
    # location of a brand shares one fhir_base_url, so the connection cannot tell them apart.
    brand = EhrBrand.objects.create(name="Mount Sinai", fhir_base_url="https://sinai.example.org/FHIR/R4")
    location = EhrBrandLocation.objects.create(brand=brand, name="Mount Sinai West")
    client = _patient_client(patient)

    created = client.post(
        "/api/v1/fhir_sources",
        {"label": "Epic / EHR Patient Portal", "data_source": device.id, "ehr_brand_location": location.id},
    )
    assert created.status_code == 201, created.content
    source = FhirSource.objects.get(pk=created.json()["id"])
    assert source.ehr_brand_location_id == location.id

    # Null is the honest value for a source that is not a supported EHR at a supported location.
    plain = client.post("/api/v1/fhir_sources", {"label": "One-off import", "data_source": device.id})
    assert FhirSource.objects.get(pk=plain.json()["id"]).ehr_brand_location_id is None


def test_deleting_a_brand_keeps_the_fhir_source(patient, device, db):
    # Deleting a brand cascades to its locations; the source (and every aux row under it) must
    # survive, losing only the descriptive link.
    brand = EhrBrand.objects.create(name="Mercy", fhir_base_url="https://mercy.example.org/FHIR/R4")
    location = EhrBrandLocation.objects.create(brand=brand, name="Mercy STL")
    source = FhirSource.objects.create(patient=patient, data_source=device, label="Mercy", ehr_brand_location=location)

    brand.delete()

    source.refresh_from_db()
    assert source.ehr_brand_location_id is None
    assert FhirSource.objects.filter(pk=source.pk).exists()


# ---------------------------------------------------------------------------
# Legacy lowercase base path
# ---------------------------------------------------------------------------


def test_legacy_lowercase_base_serves_reads_and_writes(api_client, patient, fhir_source):
    # Clients written before the base was cased FHIR/R5/ still POST and GET against fhir/r5/.
    # The alias serves the same views (not a redirect, which would drop a POST body).
    created = api_client.post("/fhir/r5/Condition", _condition(patient.id, id="legacy-1"), **_src(fhir_source))
    assert created.status_code == 201, created.text
    row = FhirAuxResource.objects.get(pk=created.json()["id"])
    assert row.fhir_resource_id == "legacy-1"

    bundle = api_client.get("/fhir/r5/Condition", **_src(fhir_source)).json()
    assert bundle["resourceType"] == "Bundle"
    assert created.json()["id"] in [entry["resource"]["id"] for entry in bundle.get("entry") or []]

    # Same row through the canonical path -- one store, two spellings.
    assert api_client.get(f"/FHIR/R5/Condition/{created.json()['id']}").status_code == 200


def test_legacy_lowercase_base_covers_the_batch_route_with_and_without_a_slash():
    # The batch base is where a Bundle POST lands; both spellings must resolve to it, since
    # APPEND_SLASH would 301 and drop the body.
    from django.urls import resolve

    # Each mount calls as_view() separately, so compare the view class, not the closure.
    canonical = resolve("/FHIR/R5").func.cls
    for path in ("/fhir/r5", "/fhir/r5/"):
        match = resolve(path)
        assert match.func.cls is canonical
        assert match.url_name.endswith("-legacy")


def test_legacy_lowercase_base_serves_discovery_and_points_at_the_canonical_base(api_client):
    # A legacy client doing discovery gets the document, and it advertises the current base so
    # the client has somewhere to migrate to.
    metadata = api_client.get("/fhir/r5/metadata")
    assert metadata.status_code == 200, metadata.text
    assert metadata.json()["implementation"]["url"].endswith("/FHIR/R5/")
    assert api_client.get("/fhir/r5/.well-known/smart-configuration").status_code == 200


def test_fhir_source_list_reports_resource_counts_and_facility(patient, device, fhir_source):
    for resource_type in ("Observation", "Observation", "Patient"):
        FhirAuxResource.objects.create(fhir_source=fhir_source, resource_type=resource_type)
    brand = EhrBrand.objects.create(name="Epic Sandbox", fhir_base_url="https://epic.example.org/FHIR/R4")
    location = EhrBrandLocation.objects.create(
        brand=brand,
        name="Epic Sandbox - Madison Campus",
        address_text="",
        city="Verona",
        state="WI",
        postal_code="53593",
    )
    located = FhirSource.objects.create(
        patient=patient, data_source=device, label="ignored", ehr_brand_location=location
    )
    client = APIClient()
    client.force_authenticate(patient.jhe_user)

    rows = {row["id"]: row for row in client.get("/api/v1/fhir_sources").json()["results"]}

    assert rows[fhir_source.id]["resourceCounts"] == {"Observation": 2, "Patient": 1}
    assert rows[fhir_source.id]["facility"] == ""
    assert rows[located.id]["resourceCounts"] == {}
    assert rows[located.id]["facility"] == "Epic Sandbox - Madison Campus"
