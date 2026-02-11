import base64
import json

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import JheUser
from core.utils import generate_observation_value_attachment_data
from .utils import (
    Code,
    add_observations,
    add_patient_to_study,
    fetch_paginated,
)


@pytest.fixture
def get_observations(client, patient, hr_study):
    def _get_observations(**params):
        r = client.get(
            "/fhir/r5/Observation",
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


def test_observation_pagination(hr_study, patient, client, get_observations):
    n = 101
    per_page = 10
    add_observations(patient=patient, code=Code.HeartRate, n=n)
    with CaptureQueriesContext(connection) as ctx:
        page = get_observations(_count=per_page)
    assert len(ctx.captured_queries) < 8
    last_query = ctx.captured_queries[-1]["sql"]
    assert "LIMIT 10" in last_query
    assert "OFFSET" not in last_query
    assert page["type"] == "searchset"
    assert page["resourceType"] == "Bundle"
    assert page["total"] == n

    with CaptureQueriesContext(connection) as ctx:
        pages = fetch_paginated(
            client, "/fhir/r5/Observation", {"patient": patient.id, "_count": per_page}, return_pages=True
        )
    # try to make sure our offset/limit were applied

    assert len(pages) == 11
    assert len(pages[0]["entry"]) == per_page
    assert len(pages[-1]["entry"]) == n % per_page
    assert sum(len(page["entry"]) for page in pages) == n

    pagination = pages[0]["meta"]["pagination"]
    assert pagination["page"] == 1
    assert pagination["pageSize"] == per_page
    assert pagination["totalPages"] == len(pages)

    last_query = ctx.captured_queries[-1]["sql"]
    # try to make sure our offset/limit were applied
    assert "OFFSET 100" in last_query

    # no 'next' link on last page
    link_rels = [link["relation"] for link in pages[-1]["link"]]
    assert link_rels == ["previous"]


def test_observation_limit(hr_study, patient, client, get_observations):
    """Test a large query with lots of entries"""
    n = 10_100
    per_page = 1_000
    add_observations(patient=patient, code=Code.HeartRate, n=n)
    all_results = fetch_paginated(client, "/fhir/r5/Observation", {"patient": patient.id, "_count": per_page})
    assert len(all_results) == n


def test_observation_upload_bundle(client, user, device, hr_study, patient, get_observations):
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
    r = client.post("/fhir/r5/", data=request_payload, format="json")
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

    assert resource_in["subject"] == resource_out["subject"]
    value_attachment_in = json.loads(base64.b64decode(resource_in["valueAttachment"]["data"]).decode())
    value_attachment_out = json.loads(base64.b64decode(resource_out["valueAttachment"]["data"]).decode())
    assert value_attachment_out["body"] == value_attachment_in["body"]


def test_observation_upload(client, user, device, hr_study, patient, get_observations):
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
    r = client.post("/fhir/r5/Observation", data=resource, format="json")
    if r.status_code != 201:
        print(r)
    assert r.status_code == 201
    response = get_observations()
    results = response["entry"]
    assert len(results) == 1
    resource_out = results[0]["resource"]
    resource_in = resource

    assert resource_in["subject"] == resource_out["subject"]
    value_attachment_in = json.loads(base64.b64decode(resource_in["valueAttachment"]["data"]).decode())
    value_attachment_out = json.loads(base64.b64decode(resource_out["valueAttachment"]["data"]).decode())
    assert value_attachment_out["body"] == value_attachment_in["body"]


def test_get_observation_by_study(client, patient, hr_study):
    add_observations(patient=patient, code=Code.HeartRate, n=5)
    patient2 = JheUser.objects.create_user(
        email="test-patient-2@example.org",
        user_type="patient",
    ).patient
    add_patient_to_study(patient2, hr_study)
    add_observations(patient=patient2, code=Code.HeartRate, n=5)

    r = client.get(
        "/fhir/r5/Observation",
        {
            "patient._has:Group:member:_id": hr_study.id,
        },
    )
    if r.status_code != 200:
        assert r.status_code == 200, f"{r.status_code} != 200, {r.text}"
    observations = r.json()["entry"]
    assert len(observations) == 10
