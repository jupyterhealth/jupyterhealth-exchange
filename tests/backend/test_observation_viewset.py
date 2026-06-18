import base64
import json

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import JheUser, Organization
from core.utils import generate_observation_value_attachment_data

from .utils import (
    Code,
    add_observations,
    add_patient_to_study,
    assert_valid_fhir_bundle,
    create_study,
    fetch_paginated,
)


@pytest.fixture
def get_observations(api_client, patient, hr_study):
    def _get_observations(**params):
        r = api_client.get(
            "/FHIR/R5/Observation",
            {
                "patient": patient.id,
                "patient._has:Group:member:_id": hr_study.id,
                **params,
            },
        )
        if r.status_code != 200:
            assert r.status_code == 200, f"{r.status_code} != 200, {r.text}"
        return r.json()

    return _get_observations


def test_observation_pagination(hr_study, patient, api_client, get_observations):
    n = 101
    per_page = 10
    add_observations(patient=patient, code=Code.HeartRate, n=n)
    with CaptureQueriesContext(connection) as ctx:
        page = get_observations(_count=per_page)
    # A FHIR search is the union of the mapped Observation rows and any FhirAuxResource
    # Observation rows, so a small constant of extra queries (the aux source's auth + count)
    # is expected on top of the mapped query; still bounded (no per-row N+1).
    assert len(ctx.captured_queries) < 12
    # The main observation query is paginated at the DB level (LIMIT, no OFFSET on page 1).
    # identifiers are prefetched in a separate bounded query, so locate the paginated
    # query rather than assuming it is the last one captured.
    paginated_queries = [q["sql"] for q in ctx.captured_queries if "LIMIT 10" in q["sql"]]
    assert len(paginated_queries) == 1
    assert "OFFSET" not in paginated_queries[0]
    assert page["type"] == "searchset"
    assert page["resourceType"] == "Bundle"
    assert page["total"] == n
    # the search Bundle envelope (and its nested Observation resources) is valid FHIR
    assert_valid_fhir_bundle(page)

    with CaptureQueriesContext(connection) as ctx:
        pages = fetch_paginated(
            api_client, "/FHIR/R5/Observation", {"patient": patient.id, "_count": per_page}, return_pages=True
        )
    # try to make sure our offset/limit were applied

    assert len(pages) == 11
    assert len(pages[0]["entry"]) == per_page
    assert len(pages[-1]["entry"]) == n % per_page
    assert sum(len(page["entry"]) for page in pages) == n
    for bundle in pages:
        assert_valid_fhir_bundle(bundle)

    # try to make sure our offset/limit were applied (the final page's paginated query)
    assert any("OFFSET 100" in q["sql"] for q in ctx.captured_queries)

    # no 'next' link on last page
    link_rels = [link["relation"] for link in pages[-1]["link"]]
    assert link_rels == ["self", "previous"]


def test_observation_list_includes_user_id(api_client, patient, hr_study):
    # The REST observations list exposes the patient's JHE User ID (jheUserId) so it can be
    # shown in the jhe-admin and Django admin Observations displays (issue #525).
    add_observations(patient=patient, code=Code.HeartRate, n=3)
    results = fetch_paginated(api_client, "/api/v1/observations", {"patient_id": patient.id, "pageSize": 10})
    assert results
    for row in results:
        assert row["jheUserId"] == patient.jhe_user_id


def test_observation_limit(hr_study, patient, api_client, get_observations):
    """Test a large query with lots of entries"""
    n = 10_100
    per_page = 1_000
    add_observations(patient=patient, code=Code.HeartRate, n=n)
    all_results = fetch_paginated(api_client, "/FHIR/R5/Observation", {"patient": patient.id, "_count": per_page})
    assert len(all_results) == n
    all_results = fetch_paginated(api_client, "/api/v1/observations", {"patient_id": patient.id, "pageSize": per_page})
    assert len(all_results) == n


def test_observation_upload_bundle(api_client, device, hr_study, patient, get_observations):
    entries = []
    for i in range(10):
        record = generate_observation_value_attachment_data(Code.HeartRate.value)

        entry = {
            "resource": {
                "resourceType": "Observation",
                "status": "final",
                "code": {
                    "coding": [
                        {
                            "system": Code.OpenMHealth.value,
                            "code": Code.HeartRate.value,
                        }
                    ],
                },
                "subject": {"reference": f"Patient/{patient.id}"},
                "device": {"reference": f"Device/{device.id}"},
                "valueAttachment": {
                    "contentType": "application/json",
                    "data": base64.b64encode(json.dumps(record).encode()).decode(),
                },
            },
            "request": {"method": "POST", "url": "Observation"},
        }
        entries.append(entry)
    request_payload = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": entries,
    }
    r = api_client.post("/FHIR/R5/", data=request_payload)
    for entry in r.json()["entry"]:
        if "outcome" in entry["response"]:
            for issue in entry["response"]["outcome"]["issue"]:
                print(issue["diagnostics"])
            raise ValueError("error!")
    if r.status_code != 200:
        print(r)
    assert r.status_code == 200
    response = get_observations()
    results = response["entry"]
    assert len(results) == 10
    resource_out = results[0]["resource"]
    resource_in = entries[0]["resource"]

    # subject.reference round-trips; the output also carries subject.identifier (jheUserId, issue #602)
    assert resource_out["subject"]["reference"] == resource_in["subject"]["reference"]
    value_attachment_in = json.loads(base64.b64decode(resource_in["valueAttachment"]["data"]).decode())
    value_attachment_out = json.loads(base64.b64decode(resource_out["valueAttachment"]["data"]).decode())
    assert value_attachment_out["body"] == value_attachment_in["body"]


def test_observation_upload_bundle_without_trailing_slash(api_client, device, hr_study, patient, get_observations):
    """The batch base accepts POST with or without the trailing slash (POST /FHIR/R5)."""
    record = generate_observation_value_attachment_data(Code.HeartRate.value)
    request_payload = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "resource": {
                    "resourceType": "Observation",
                    "status": "final",
                    "code": {"coding": [{"system": Code.OpenMHealth.value, "code": Code.HeartRate.value}]},
                    "subject": {"reference": f"Patient/{patient.id}"},
                    "device": {"reference": f"Device/{device.id}"},
                    "valueAttachment": {
                        "contentType": "application/json",
                        "data": base64.b64encode(json.dumps(record).encode()).decode(),
                    },
                },
                "request": {"method": "POST", "url": "Observation"},
            }
        ],
    }
    r = api_client.post("/FHIR/R5", data=request_payload)
    assert r.status_code == 200, r.text
    assert r.json()["type"] == "batch-response"
    assert len(get_observations()["entry"]) == 1


def test_observation_upload(api_client, device, hr_study, patient, get_observations):
    record = generate_observation_value_attachment_data(Code.HeartRate.value)

    resource = {
        "resourceType": "Observation",
        "status": "final",
        "code": {
            "coding": [
                {
                    "system": Code.OpenMHealth.value,
                    "code": Code.HeartRate.value,
                }
            ],
        },
        "subject": {"reference": f"Patient/{patient.id}"},
        "device": {"reference": f"Device/{device.id}"},
        "valueAttachment": {
            "contentType": "application/json",
            "data": base64.b64encode(json.dumps(record).encode()).decode(),
            # "data": record,
        },
    }
    r = api_client.post("/FHIR/R5/Observation", data=resource)
    if r.status_code != 201:
        print(r)
    assert r.status_code == 201
    response = get_observations()
    results = response["entry"]
    assert len(results) == 1
    resource_out = results[0]["resource"]
    resource_in = resource

    # subject.reference round-trips; the output also carries subject.identifier (jheUserId, issue #602)
    assert resource_out["subject"]["reference"] == resource_in["subject"]["reference"]
    value_attachment_in = json.loads(base64.b64decode(resource_in["valueAttachment"]["data"]).decode())
    value_attachment_out = json.loads(base64.b64decode(resource_out["valueAttachment"]["data"]).decode())
    assert value_attachment_out["body"] == value_attachment_in["body"]


def test_get_observation_by_study(api_client, patient, hr_study):
    add_observations(patient=patient, code=Code.HeartRate, n=5)
    patient2 = JheUser.objects.create_user(
        email="test-patient-2@example.org",
        user_type="patient",
    ).patient
    add_patient_to_study(patient2, hr_study)
    add_observations(patient=patient2, code=Code.HeartRate, n=5)

    r = api_client.get(
        "/FHIR/R5/Observation",
        {
            "patient._has:Group:member:_id": hr_study.id,
        },
    )
    if r.status_code != 200:
        assert r.status_code == 200, f"{r.status_code} != 200, {r.text}"
    observations = r.json()["entry"]
    assert len(observations) == 10


def test_get_observation_one_patient_two_studies(api_client, patient, hr_study):
    org2 = Organization.objects.create(
        name="org2",
        type="other",
    )
    bp_study = create_study(organization=org2, codes=[Code.BloodPressure])
    add_observations(patient=patient, code=Code.HeartRate, n=6)
    add_patient_to_study(patient, bp_study)
    add_observations(patient=patient, code=Code.BloodPressure, n=5)

    r = api_client.get(
        "/FHIR/R5/Observation",
        {
            "patient._has:Group:member:_id": hr_study.id,
        },
    )
    if r.status_code != 200:
        assert r.status_code == 200, f"{r.status_code} != 200, {r.text}"
    observations = r.json()["entry"]
    assert len(observations) == 6


def test_get_observation_access(api_client, patient, hr_study):
    add_observations(patient=patient, code=Code.HeartRate, n=6)
    org2 = Organization.objects.create(
        name="org2",
        type="other",
    )
    bp_study = create_study(organization=org2, codes=[Code.BloodPressure])
    patient2 = JheUser.objects.create_user(
        email="test-patient-2@example.org",
        user_type="patient",
    ).patient
    add_patient_to_study(patient2, bp_study)
    add_observations(patient=patient2, code=Code.BloodPressure, n=5)

    r = api_client.get(
        "/FHIR/R5/Observation",
        {
            "patient._has:Group:member:_id": hr_study.id,
        },
    )
    if r.status_code != 200:
        assert r.status_code == 200, f"{r.status_code} != 200, {r.text}"
    observations = r.json()["entry"]
    assert len(observations) == 6
