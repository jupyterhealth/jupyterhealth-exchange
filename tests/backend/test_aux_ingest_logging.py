"""REQ-SYNC-07: each aux ingest emits a server-side audit log line."""

import logging

import pytest

from core.models import FhirSource
from core.views.fhir import create_aux_resource


@pytest.fixture
def fhir_source(patient, device):
    return FhirSource.objects.create(patient=patient, data_source=device, label="src")


def test_aux_create_logs_ingest(fhir_source, caplog):
    with caplog.at_level(logging.INFO, logger="core.views.fhir"):
        aux = create_aux_resource("Condition", {"resourceType": "Condition", "id": "c-1"}, fhir_source)

    messages = [r.getMessage() for r in caplog.records]
    assert any(f"aux ingest: Condition/{aux.pk}" in m and f"patient={fhir_source.patient_id}" in m for m in messages)
