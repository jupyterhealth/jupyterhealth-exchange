"""OMH body-schema validation tests for the spirometry schemas (FVC, FEV1).

Covers the two schemas added from openmhealth/schemas (forced-vital-capacity-1.0,
forced-expiratory-volume-1-second-1.0). Each $refs the volume-unit-value-1.x and
temporal-relationship-to-physical-activity-1.x utility schemas, which must resolve
from the local registry so non-compliant values (e.g. a non-liter unit) are rejected
with a ValidationError rather than silently accepted or failing to resolve the $ref.
"""

import json

import pytest
from django.conf import settings
from jsonschema import ValidationError

from core.utils import validate_with_registry

FVC_BODY_SCHEMA = json.loads(
    (settings.DATA_DIR_PATH.schemas_data / "schema-omh_forced-vital-capacity_1-0.json").read_text()
)
FEV1_BODY_SCHEMA = json.loads(
    (settings.DATA_DIR_PATH.schemas_data / "schema-omh_forced-expiratory-volume-1-second_1-0.json").read_text()
)


def _fvc_body(**overrides):
    body = {
        "forced_vital_capacity": {"unit": "L", "value": 4.2},
        "effective_time_frame": {"date_time": "2025-01-01T00:00:00Z"},
    }
    body.update(overrides)
    return body


def _fev1_body(**overrides):
    body = {
        "forced_expiratory_volume_1_second": {"unit": "L", "value": 3.1},
        "effective_time_frame": {"date_time": "2025-01-01T00:00:00Z"},
    }
    body.update(overrides)
    return body


def test_fvc_minimal_compliant_body_passes():
    validate_with_registry(instance=_fvc_body(), schema=FVC_BODY_SCHEMA)


def test_fev1_minimal_compliant_body_passes():
    validate_with_registry(instance=_fev1_body(), schema=FEV1_BODY_SCHEMA)


def test_fvc_invalid_unit_rejected():
    # OMH constrains the spirometry value to liters ("L"); "mL" is non-compliant.
    body = _fvc_body()
    body["forced_vital_capacity"]["unit"] = "mL"
    with pytest.raises(ValidationError):
        validate_with_registry(instance=body, schema=FVC_BODY_SCHEMA)


def test_fev1_invalid_unit_rejected():
    body = _fev1_body()
    body["forced_expiratory_volume_1_second"]["unit"] = "mL"
    with pytest.raises(ValidationError):
        validate_with_registry(instance=body, schema=FEV1_BODY_SCHEMA)


@pytest.mark.parametrize("value", ["at rest", "active", "before exercise", "after exercise", "during exercise"])
def test_fvc_valid_temporal_relationship_to_physical_activity_passes(value):
    validate_with_registry(instance=_fvc_body(temporal_relationship_to_physical_activity=value), schema=FVC_BODY_SCHEMA)


def test_fvc_invalid_temporal_relationship_to_physical_activity_rejected():
    with pytest.raises(ValidationError):
        validate_with_registry(
            instance=_fvc_body(temporal_relationship_to_physical_activity="unknown"), schema=FVC_BODY_SCHEMA
        )
