"""Tests for the EHR brand / location model (hospital branding + selection)."""

import pytest
from django.db import IntegrityError

from core.models import EhrBrand, EhrBrandLocation


def test_create_brand_with_locations(db):
    brand = EhrBrand.objects.create(
        name="Mount Sinai",
        vendor="epic",
        fhir_base_url="https://fhir.mountsinai.org/api/FHIR/R4",
    )
    EhrBrandLocation.objects.create(
        brand=brand,
        name="Mount Sinai Hospital",
        address_text="1 Gustave L. Levy Pl, New York, NY 10029",
        city="New York",
        state="NY",
        postal_code="10029",
    )
    EhrBrandLocation.objects.create(
        brand=brand,
        name="Mount Sinai West",
        address_text="1000 10th Ave, New York, NY 10019",
        city="New York",
        state="NY",
        postal_code="10019",
    )
    assert brand.locations.count() == 2
    assert brand.locations.first().brand_id == brand.id


def test_fhir_base_url_is_unique(db):
    EhrBrand.objects.create(name="A", fhir_base_url="https://ehr.example.org/FHIR/R4")
    with pytest.raises(IntegrityError):
        EhrBrand.objects.create(name="B", fhir_base_url="https://ehr.example.org/FHIR/R4")


def test_vendor_defaults_to_epic(db):
    brand = EhrBrand.objects.create(name="Default Vendor", fhir_base_url="https://x.example.org/FHIR/R4")
    assert brand.vendor == "epic"


def test_deleting_brand_cascades_to_locations(db):
    brand = EhrBrand.objects.create(name="Cascade", fhir_base_url="https://c.example.org/FHIR/R4")
    EhrBrandLocation.objects.create(brand=brand, name="Site 1", address_text="123 St")
    brand.delete()
    assert EhrBrandLocation.objects.count() == 0
