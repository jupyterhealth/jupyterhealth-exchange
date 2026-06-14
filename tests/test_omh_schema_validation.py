"""OMH/IEEE-1752 body-schema validation tests for blood glucose (CGM).

Covers the validation gaps reported in jupyterhealth/jupyterhealth-exchange#235:
the blood-glucose-4.0 body schema $refs specimen-source, temporal-relationship-to-meal
and temporal-relationship-to-sleep utility schemas. These must resolve from the local
registry so non-compliant values (e.g. the reported ``"unknown"`` meal relationship or
``"MGDL"`` unit) are rejected with a ValidationError rather than silently accepted or
failing to resolve the $ref.
"""

import pytest
from jsonschema import ValidationError

from core.utils import code_to_schema, validate_with_registry


def _body(**overrides):
    body = {
        "blood_glucose": {"unit": "mg/dL", "value": 129},
        "effective_time_frame": {"date_time": "2024-01-01T00:00:00Z"},
    }
    body.update(overrides)
    return body


def _validate(body):
    validate_with_registry(instance=body, schema=code_to_schema("omh:blood-glucose:4.0"))


def test_minimal_compliant_body_passes():
    _validate(_body())


@pytest.mark.parametrize("value", ["fasting", "not fasting", "before meal", "after meal", "2 hours postprandial"])
def test_valid_temporal_relationship_to_meal_passes(value):
    _validate(_body(temporal_relationship_to_meal=value))


def test_invalid_temporal_relationship_to_meal_rejected():
    # "unknown" is the non-compliant value reported in issue #235
    with pytest.raises(ValidationError):
        _validate(_body(temporal_relationship_to_meal="unknown"))


def test_valid_specimen_source_passes():
    _validate(_body(specimen_source="interstitial fluid"))


def test_invalid_specimen_source_rejected():
    with pytest.raises(ValidationError):
        _validate(_body(specimen_source="blood"))


def test_invalid_unit_rejected():
    # "MGDL" is the non-compliant unit reported in issue #235; OMH requires "mg/dL"
    body = _body()
    body["blood_glucose"]["unit"] = "MGDL"
    with pytest.raises(ValidationError):
        _validate(body)
