"""Tests for the EHR brands importer (SMART user-access Brands Bundle -> DB)."""

import json
from pathlib import Path

from django.core.management import call_command

from core.management.commands.import_ehr_brands import DEFAULT_SAMPLE, import_brands_bundle
from core.models import EhrBrand, EhrBrandLocation


def _bundle():
    """A minimal SMART user-access Brands Bundle: 1 brand, 1 endpoint, 2 facilities."""
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {
                "fullUrl": "https://example.org/Endpoint/ep1",
                "resource": {
                    "resourceType": "Endpoint",
                    "id": "ep1",
                    "address": "https://fhir.mountsinai.org/api/FHIR/R4",
                    "connectionType": {"code": "hl7-fhir-rest"},
                },
            },
            {
                "resource": {
                    "resourceType": "Organization",
                    "id": "brand-msinai",
                    "name": "Mount Sinai",
                    "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": "1234567890"}],
                    "endpoint": [{"reference": "Endpoint/ep1"}],
                },
            },
            {
                "resource": {
                    "resourceType": "Organization",
                    "id": "loc-1",
                    "name": "Mount Sinai Hospital",
                    "partOf": {"reference": "Organization/brand-msinai"},
                    "address": [
                        {
                            "text": "1 Gustave L. Levy Pl, New York, NY 10029",
                            "city": "New York",
                            "state": "NY",
                            "postalCode": "10029",
                        }
                    ],
                },
            },
            {
                "resource": {
                    "resourceType": "Organization",
                    "id": "loc-2",
                    "name": "Mount Sinai West",
                    "partOf": {"reference": "Organization/brand-msinai"},
                    "address": [{"city": "New York", "state": "NY", "postalCode": "10019"}],
                },
            },
        ],
    }


def test_imports_brand_and_locations(db):
    counts = import_brands_bundle(_bundle())

    brand = EhrBrand.objects.get(fhir_base_url="https://fhir.mountsinai.org/api/FHIR/R4")
    assert brand.name == "Mount Sinai"
    assert brand.npi == "1234567890"
    assert brand.locations.count() == 2
    assert counts["brands"] == 1
    assert counts["locations"] == 2


def test_import_is_idempotent(db):
    import_brands_bundle(_bundle())
    import_brands_bundle(_bundle())
    assert EhrBrand.objects.count() == 1
    assert EhrBrandLocation.objects.count() == 2


def test_brand_without_facility_gets_fallback_location(db):
    bundle = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Endpoint",
                    "id": "ep9",
                    "address": "https://solo.example.org/FHIR/R4",
                }
            },
            {
                "resource": {
                    "resourceType": "Organization",
                    "id": "brand-solo",
                    "name": "Solo Clinic",
                    "endpoint": [{"reference": "Endpoint/ep9"}],
                    "address": [{"text": "500 Main St", "state": "CA"}],
                }
            },
        ],
    }
    import_brands_bundle(bundle)
    brand = EhrBrand.objects.get(fhir_base_url="https://solo.example.org/FHIR/R4")
    assert brand.locations.count() == 1
    assert brand.locations.first().name == "Solo Clinic"


def test_call_command_reads_file(db, tmp_path):
    path = tmp_path / "brands.json"
    path.write_text(json.dumps(_bundle()))
    call_command("import_ehr_brands", "--file", str(path))
    assert EhrBrand.objects.count() == 1
    assert EhrBrandLocation.objects.count() == 2


def test_shipped_sample_fixture_is_valid_and_imports(db):
    bundle = json.loads(Path(DEFAULT_SAMPLE).read_text(encoding="utf-8"))
    counts = import_brands_bundle(bundle)
    assert counts["brands"] >= 1
    assert counts["locations"] >= 1
    # every seeded brand is selectable (has at least one location for the picker)
    assert all(b.locations.exists() for b in EhrBrand.objects.all())
