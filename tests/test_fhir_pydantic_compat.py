"""
Tests for FHIR / pydantic v2 compatibility fixes.

Covers:
- valueAttachment.data handling when fhir.resources returns bytes vs base64 str
- Resource(id=...) type coercion in bundle response entries
"""

import base64
import json
from unittest.mock import patch

import pytest

from core.models import Observation
from core.utils import generate_observation_value_attachment_data
from core.views.fhir_base import FHIRBase

from .utils import Code, add_patient_to_study, create_study


# ---------------------------------------------------------------------------
# Unit tests for FHIRBase.bundle_create_response_entry
# ---------------------------------------------------------------------------


class TestBundleCreateResponseEntry:
    """Test the static helper that builds FHIR Bundle response entries."""

    def test_status_only(self):
        entry = FHIRBase.bundle_create_response_entry(201)
        assert entry["response"]["status"] == "201 Created"
        assert "resource" not in entry

    def test_with_outcome(self):
        outcome = {"issue": [{"severity": "error", "code": "processing", "diagnostics": "bad input"}]}
        entry = FHIRBase.bundle_create_response_entry(400, outcome=outcome)
        assert entry["response"]["status"] == "400 Bad Request"
        assert entry["response"]["outcome"] == outcome
        assert "resource" not in entry

    def test_with_obj_integer_id(self):
        """Resource(id=...) must accept integer PKs (Django default) without raising."""

        class _FakeObj:
            id = 42

        entry = FHIRBase.bundle_create_response_entry(201, obj=_FakeObj())
        assert "resource" in entry
        # The id field should be a string after the fix
        assert entry["resource"].id == "42"

    def test_with_obj_string_id(self):
        class _FakeObj:
            id = "some-uuid"

        entry = FHIRBase.bundle_create_response_entry(201, obj=_FakeObj())
        assert entry["resource"].id == "some-uuid"

    def test_with_obj_and_outcome(self):
        class _FakeObj:
            id = 99

        outcome = FHIRBase.error_outcome("duplicate")
        entry = FHIRBase.bundle_create_response_entry(409, outcome=outcome, obj=_FakeObj())
        assert "resource" in entry
        assert "outcome" in entry["response"]


# ---------------------------------------------------------------------------
# Unit tests for Observation.fhir_create – valueAttachment.data decoding
# ---------------------------------------------------------------------------


@pytest.fixture
def bp_study(organization, user, patient):
    study = create_study(
        organization=organization,
        codes=[Code.BloodPressure],
    )
    add_patient_to_study(patient=patient, study=study)
    return study


def _build_fhir_payload(patient, device, data_field):
    """Build a minimal FHIR Observation payload with a custom data field."""
    return {
        "resourceType": "Observation",
        "status": "final",
        "code": {
            "coding": [
                {
                    "system": Code.OpenMHealth.value,
                    "code": Code.BloodPressure.value,
                }
            ],
        },
        "subject": {"reference": f"Patient/{patient.id}"},
        "device": {"reference": f"Device/{device.id}"},
        "valueAttachment": {
            "contentType": "application/json",
            "data": data_field,
        },
    }


class TestFhirCreateValueAttachmentDecoding:
    """Test the three code paths in Observation.fhir_create for valueAttachment.data."""

    def test_base64_encoded_string(self, bp_study, patient, device, user):
        """Classic path: data arrives as a base64-encoded string."""
        record = generate_observation_value_attachment_data(Code.BloodPressure.value)
        b64 = base64.b64encode(json.dumps(record).encode()).decode()
        payload = _build_fhir_payload(patient, device, b64)

        obs = Observation.fhir_create(payload, user)
        assert obs is not None
        assert obs.codeable_concept.coding_code == Code.BloodPressure.value

    def test_raw_json_string(self, bp_study, patient, device, user):
        """New path: fhir.resources pre-decodes the base64 and returns a plain JSON string."""
        record = generate_observation_value_attachment_data(Code.BloodPressure.value)
        raw_json = json.dumps(record)
        payload = _build_fhir_payload(patient, device, raw_json)

        # Patch parse_obj so that valueAttachment.data is the raw JSON string
        # (simulating fhir.resources pre-decoding behaviour)
        original_parse_obj = None

        from fhir.resources.observation import Observation as FHIRObservation

        original_parse_obj = FHIRObservation.parse_obj

        def mock_parse_obj(data):
            result = original_parse_obj(data)
            result.valueAttachment.data = raw_json
            return result

        with patch.object(FHIRObservation, "parse_obj", side_effect=mock_parse_obj):
            obs = Observation.fhir_create(payload, user)

        assert obs is not None
        assert obs.value_attachment_data == record

    def test_bytes_value(self, bp_study, patient, device, user):
        """New path: fhir.resources returns data as bytes (pydantic v2 behaviour)."""
        record = generate_observation_value_attachment_data(Code.BloodPressure.value)
        raw_bytes = json.dumps(record).encode("utf-8")
        payload = _build_fhir_payload(patient, device, base64.b64encode(raw_bytes).decode())

        from fhir.resources.observation import Observation as FHIRObservation

        original_parse_obj = FHIRObservation.parse_obj

        def mock_parse_obj(data):
            result = original_parse_obj(data)
            # Simulate fhir.resources returning bytes instead of str
            result.valueAttachment.data = raw_bytes
            return result

        with patch.object(FHIRObservation, "parse_obj", side_effect=mock_parse_obj):
            obs = Observation.fhir_create(payload, user)

        assert obs is not None
        assert obs.value_attachment_data == record

    def test_invalid_data_raises(self, bp_study, patient, device, user):
        """Garbage in valueAttachment.data must raise BadRequest."""
        payload = _build_fhir_payload(patient, device, "not-valid-json-or-base64!!!")

        from fhir.resources.observation import Observation as FHIRObservation

        original_parse_obj = FHIRObservation.parse_obj

        def mock_parse_obj(data):
            result = original_parse_obj(data)
            result.valueAttachment.data = "not-valid-json-or-base64!!!"
            return result

        from django.core.exceptions import BadRequest

        with patch.object(FHIRObservation, "parse_obj", side_effect=mock_parse_obj):
            with pytest.raises(BadRequest, match="valueAttachment.data must be Base 64"):
                Observation.fhir_create(payload, user)


# ---------------------------------------------------------------------------
# Integration test: bundle upload returns Resource with string id
# ---------------------------------------------------------------------------


def test_bundle_response_contains_string_resource_id(
    api_client, device, patient, hr_study
):
    """After uploading via bundle, each response entry must have a Resource with a string id."""
    record = generate_observation_value_attachment_data(Code.HeartRate.value)
    payload = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
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
        ],
    }
    r = api_client.post("/fhir/r5/", data=payload)
    assert r.status_code == 200
    entries = r.json()["entry"]
    assert len(entries) == 1
    assert "201" in entries[0]["response"]["status"]
    # The resource id must be a string (pydantic v2 compat fix)
    resource_id = entries[0]["resource"]["id"]
    assert isinstance(resource_id, str)
    assert resource_id.isdigit()
