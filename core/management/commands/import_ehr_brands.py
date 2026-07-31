"""
Import a SMART App Launch 2.2 "user-access Brands Bundle" (FHIR Bundle of
Organization brands + Organization care facilities + Endpoints) into the
EhrBrand / EhrBrandLocation tables that power the Patient Access hospital picker.

Epic publishes its bundle at https://open.epic.com/MyApps/Endpoints
(User-access Brands Bundle). That full file is multi-MB and is NOT committed;
download it and run:  python manage.py import_ehr_brands --file epic_brands.json
A small curated sample ships at core/data/ehr_brands.sample.json for seed/dev.

Vendor-neutral: any EHR's user-access Brands Bundle can be imported the same way.
"""

import json
import os

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import EhrBrand, EhrBrandLocation

DEFAULT_SAMPLE = os.path.join("core", "data", "ehr_brands.sample.json")


def _first_endpoint_address(org, endpoints):
    """Resolve a brand Organization's first Endpoint reference to its FHIR base URL."""
    for ref in org.get("endpoint", []) or []:
        reference = ref.get("reference", "")
        endpoint = endpoints.get(reference) or endpoints.get(reference.split("/")[-1])
        if endpoint and endpoint.get("address"):
            return endpoint["address"]
    return None


def _npi(org):
    for ident in org.get("identifier", []) or []:
        if "us-npi" in (ident.get("system") or ""):
            return ident.get("value")
    return None


def _address_parts(org):
    """Flatten the first address of an Organization into our location columns."""
    addresses = org.get("address", []) or []
    if not addresses:
        return {"address_text": "", "city": None, "state": None, "postal_code": None, "country": None}
    a = addresses[0]
    text = a.get("text")
    if not text:
        text = ", ".join(p for p in [*(a.get("line") or []), a.get("city"), a.get("state"), a.get("postalCode")] if p)
    return {
        "address_text": text or "",
        "city": a.get("city"),
        "state": a.get("state"),
        "postal_code": a.get("postalCode"),
        "country": a.get("country"),
    }


@transaction.atomic
def import_brands_bundle(bundle):
    """
    Upsert a Brands Bundle into EhrBrand / EhrBrandLocation. Idempotent: brands
    keyed on fhir_base_url, locations on (brand, name, address_text). Returns
    {"brands": n, "locations": m}.
    """
    resources = [e.get("resource", {}) for e in bundle.get("entry", []) or []]
    endpoints = {}
    for entry in bundle.get("entry", []) or []:
        res = entry.get("resource", {})
        if res.get("resourceType") == "Endpoint":
            endpoints[f"Endpoint/{res.get('id')}"] = res
            endpoints[res.get("id")] = res
            if entry.get("fullUrl"):
                endpoints[entry["fullUrl"]] = res

    orgs = [r for r in resources if r.get("resourceType") == "Organization"]
    brand_orgs = [o for o in orgs if o.get("endpoint")]
    facility_orgs = [o for o in orgs if o.get("partOf") and not o.get("endpoint")]

    brand_by_org_id = {}  # source Organization id -> EhrBrand
    brand_count = 0
    for org in brand_orgs:
        base_url = _first_endpoint_address(org, endpoints)
        if not base_url:
            continue
        brand, _ = EhrBrand.objects.update_or_create(
            fhir_base_url=base_url,
            defaults={"name": org.get("name") or base_url, "npi": _npi(org)},
        )
        brand_by_org_id[org.get("id")] = (brand, org)
        brand_count += 1

    location_count = 0
    brands_with_locations = set()
    for org in facility_orgs:
        ref = (org.get("partOf") or {}).get("reference", "")
        brand_entry = brand_by_org_id.get(ref.split("/")[-1])
        if not brand_entry:
            continue
        brand, _ = brand_entry
        parts = _address_parts(org)
        _, created = EhrBrandLocation.objects.get_or_create(
            brand=brand,
            name=org.get("name") or brand.name,
            address_text=parts["address_text"],
            defaults=parts,
        )
        brands_with_locations.add(brand.id)
        if created:
            location_count += 1

    # A brand with no care-facility children is still selectable: seed one
    # location from the brand itself so it shows up in the picker.
    for brand, org in brand_by_org_id.values():
        if brand.id in brands_with_locations:
            continue
        parts = _address_parts(org)
        _, created = EhrBrandLocation.objects.get_or_create(
            brand=brand, name=brand.name, address_text=parts["address_text"], defaults=parts
        )
        if created:
            location_count += 1

    return {"brands": brand_count, "locations": location_count}


class Command(BaseCommand):
    help = "Import a SMART user-access Brands Bundle JSON into EhrBrand/EhrBrandLocation."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            default=DEFAULT_SAMPLE,
            help=f"Path to a Brands Bundle JSON file (default: {DEFAULT_SAMPLE}).",
        )

    def handle(self, *args, **options):
        path = options["file"]
        with open(path, encoding="utf-8") as f:
            bundle = json.load(f)
        counts = import_brands_bundle(bundle)
        self.stdout.write(
            self.style.SUCCESS(f"Imported {counts['brands']} brand(s), {counts['locations']} location(s) from {path}")
        )
