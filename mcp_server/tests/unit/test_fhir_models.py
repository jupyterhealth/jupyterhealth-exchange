import base64
import json

from jhe_mcp.fhir.models import Demographics, Observation, StudyMeta


def test_study_meta_from_admin_payload():
    payload = {
        "id": 1,
        "name": "Test Study",
        "description": "demo",
        "organization": {"id": 42, "name": "Acme"},
    }
    sm = StudyMeta.from_admin(payload)
    assert sm.study_id == "1"
    assert sm.name == "Test Study"
    assert sm.organization_id == "42"
    assert sm.organization_name == "Acme"


def test_demographics_from_admin_patient():
    # JHE Admin API returns Patient in camelCase via djangorestframework-camel-case.
    # Field names verified during 2026-05-19 spike.
    payload = {
        "id": 7,
        "nameGiven": "Jane",
        "nameFamily": "Doe",
        "birthDate": "1990-04-12",
        "telecomEmail": "jane@example.com",
    }
    d = Demographics.from_admin(payload)
    assert d.patient_id == "7"
    assert d.given_name == "Jane"
    assert d.family_name == "Doe"
    assert d.birth_date == "1990-04-12"


def _encode_omh(payload: dict) -> str:
    return base64.b64encode(json.dumps(payload).encode()).decode()


def test_observation_from_fhir_entry_with_omh_body():
    omh_payload = {
        "body": {
            "systolic_blood_pressure": {"unit": "mmHg", "value": 120},
            "diastolic_blood_pressure": {"unit": "mmHg", "value": 80},
            "effective_time_frame": {"date_time": "2026-04-15T08:00:00Z"},
        },
        "header": {"schema_id": {"name": "blood-pressure", "version": "4.0"}},
    }
    entry = {
        "resource": {
            "resourceType": "Observation",
            "id": "obs-1",
            "code": {"coding": [{"system": "https://w3id.org/openmhealth", "code": "omh:blood-pressure:4.0"}]},
            "subject": {"reference": "Patient/7"},
            "valueAttachment": {
                "data": _encode_omh(omh_payload),
                "contentType": "application/json",
            },
        }
    }
    o = Observation.from_fhir_entry(entry)
    assert o.observation_id == "obs-1"
    assert o.code == "omh:blood-pressure:4.0"
    assert o.code_system == "https://w3id.org/openmhealth"
    assert o.effective_at == "2026-04-15T08:00:00Z"
    assert o.patient_id == "7"
    assert o.omh_body["systolic_blood_pressure"]["value"] == 120
    assert o.omh_body["diastolic_blood_pressure"]["value"] == 80


def test_observation_from_fhir_entry_interval_time_frame():
    """Wearable records use effective_time_frame.time_interval (not date_time);
    effective_at must come from the interval's start_date_time."""
    omh_payload = {
        "body": {
            "total_sleep_time": {"unit": "sec", "value": 25513},
            "effective_time_frame": {
                "time_interval": {
                    "start_date_time": "2015-06-10T23:30:00Z",
                    "end_date_time": "2015-06-11T07:00:00Z",
                }
            },
        },
    }
    entry = {
        "resource": {
            "resourceType": "Observation",
            "id": "obs-iv",
            "code": {"coding": [{"system": "https://w3id.org/openmhealth", "code": "omh:sleep-duration:2.0"}]},
            "subject": {"reference": "Patient/40006"},
            "valueAttachment": {"data": _encode_omh(omh_payload)},
        }
    }
    o = Observation.from_fhir_entry(entry)
    assert o.effective_at == "2015-06-10T23:30:00Z"


def test_observation_from_fhir_entry_interval_end_only_fallback():
    """If the interval has no start_date_time, fall back to end_date_time."""
    omh_payload = {
        "body": {
            "physical_activity": {"unit": "m", "value": 4757.1},
            "effective_time_frame": {
                "time_interval": {"end_date_time": "2015-06-11T07:00:00Z", "duration": {"value": 30, "unit": "min"}}
            },
        },
    }
    entry = {
        "resource": {
            "resourceType": "Observation",
            "id": "obs-iv2",
            "code": {"coding": [{"code": "omh:physical-activity:1.0"}]},
            "subject": {"reference": "Patient/40006"},
            "valueAttachment": {"data": _encode_omh(omh_payload)},
        }
    }
    o = Observation.from_fhir_entry(entry)
    assert o.effective_at == "2015-06-11T07:00:00Z"


def test_observation_from_fhir_entry_without_attachment():
    entry = {
        "resource": {
            "resourceType": "Observation",
            "id": "obs-2",
            "code": {"coding": [{"system": "http://loinc.org", "code": "2339-0"}]},
            "subject": {"reference": "Patient/7"},
        }
    }
    o = Observation.from_fhir_entry(entry)
    assert o.observation_id == "obs-2"
    assert o.omh_body is None
    assert o.effective_at is None


def test_observation_from_fhir_entry_malformed_attachment_data():
    """A non-base64 valueAttachment.data must not raise; omh_body is None, other
    fields are preserved."""
    entry = {
        "resource": {
            "resourceType": "Observation",
            "id": "obs-bad",
            "code": {"coding": [{"system": "http://loinc.org", "code": "2339-0"}]},
            "subject": {"reference": "Patient/42"},
            "valueAttachment": {
                "data": "!!!notbase64!!!",
                "contentType": "application/json",
            },
        }
    }
    o = Observation.from_fhir_entry(entry)
    assert o.observation_id == "obs-bad"
    assert o.patient_id == "42"
    assert o.omh_body is None
    assert o.effective_at is None


def test_observation_from_fhir_entry_non_json_attachment_data():
    """Base64-valid but non-JSON content must not raise; omh_body is None."""
    import base64

    entry = {
        "resource": {
            "resourceType": "Observation",
            "id": "obs-badjson",
            "code": {"coding": [{}]},
            "subject": {"reference": "Patient/5"},
            "valueAttachment": {
                "data": base64.b64encode(b"not json at all").decode(),
                "contentType": "application/json",
            },
        }
    }
    o = Observation.from_fhir_entry(entry)
    assert o.observation_id == "obs-badjson"
    assert o.omh_body is None
