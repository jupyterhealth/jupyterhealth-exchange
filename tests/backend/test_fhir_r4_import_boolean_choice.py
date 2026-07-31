"""Regression: R4->R5 conversion must keep boolean choice/extension keys as the FHIR name
(`deceasedBoolean`, `valueBoolean`) -- not the Python builtin name (`deceasedBool`, `valueBool`).

Surfaced by the Patient Access pull (Patient.deceasedBoolean and a boolean-valued Observation
extension) once writes were routed through /fhir-import/R4/. Root cause: for primitive choice
elements the engine used `fhir.resources`' Python annotation name (`bool`) instead of the map
rule's declared FHIR type (`boolean`). Reported to Simon for the shared engine.
"""

from core.fhir.cross_version import transform_to_r5
from core.fhir.fhir_validation import validate_fhir_resource


def test_patient_deceased_boolean_keeps_fhir_key():
    out = transform_to_r5("Patient", {"resourceType": "Patient", "deceasedBoolean": True})
    assert "deceasedBool" not in out, "regression: Python builtin name leaked into the choice key"
    assert out.get("deceasedBoolean") is True
    validate_fhir_resource("Patient", out)  # raises on invalid R5


def test_boolean_valued_extension_keeps_fhir_key():
    r4 = {
        "resourceType": "Patient",
        "extension": [{"url": "http://example.org/flag", "valueBoolean": True}],
    }
    out = transform_to_r5("Patient", r4)
    ext = (out.get("extension") or [{}])[0]
    assert "valueBool" not in ext
    assert ext.get("valueBoolean") is True
    validate_fhir_resource("Patient", out)
