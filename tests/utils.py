"""
Test utilities for populating the test db state
"""

import uuid
from copy import deepcopy
from enum import Enum
from functools import partial
from operator import itemgetter

from django.utils import timezone

from core.models import (
    CodeableConcept,
    Observation,
    Organization,
    Patient,
    Study,
    StudyPatient,
    StudyPatientScopeConsent,
    StudyScopeRequest,
)
from core.utils import generate_observation_value_attachment_data
from core.fhir_pagination import FHIRBundlePagination
from core.admin_pagination import CustomPageNumberPagination


class Code(Enum):
    HeartRate = "omh:heart-rate:2.0"
    BloodPressure = "omh:blood-pressure:4.0"
    BloodGlucose = "omh:blood-glucose:4.0"
    OpenMHealth = "https://w3id.org/openmhealth"


def create_study(
    name="study", description="desc", *, organization: Organization, codes: list[str] | None = None
) -> Study:
    """Create a study with scopes attached

    Any missing CodeableConcepts will be defined
    """
    study = Study.objects.create(name=name, description=description, organization=organization)
    for code in codes or []:
        if isinstance(code, Code):
            code = code.value
        scope_code, _ = CodeableConcept.objects.update_or_create(
            coding_system=Code.OpenMHealth.value,
            coding_code=code,
            text=code,
        )
        StudyScopeRequest.objects.create(study=study, scope_code=scope_code)
    return study


def add_patient_to_study(patient: Patient, study: Study) -> None:
    """Add a patient to a study, including consent for the scopes requested by the study"""
    patient.organizations.add(study.organization)
    study_patient = StudyPatient.objects.create(study=study, patient=patient)
    for scope_request in StudyScopeRequest.objects.filter(study=study):
        scope_code = scope_request.scope_code
        StudyPatientScopeConsent.objects.create(
            study_patient=study_patient,
            scope_code=scope_code,
            consented=True,
            consented_time=timezone.now(),
        )


def add_observations(patient: Patient, code: Code | str, n: int) -> None:
    """Generate random observations"""
    if isinstance(code, Code):
        code = code.value

    scope_code, _ = CodeableConcept.objects.update_or_create(
        coding_system="https://w3id.org/openmhealth",
        coding_code=code,
        text=code,
    )
    observations = []

    starting_attachment = generate_observation_value_attachment_data(code)
    for i in range(n):
        attachment = deepcopy(starting_attachment)
        attachment["header"]["uuid"] = str(uuid.uuid4())
        observations.append(
            Observation(
                subject_patient=patient,
                codeable_concept=scope_code,
                value_attachment_data=attachment,
            )
        )
    Observation.objects.bulk_create(observations, batch_size=100)


def get_link(bundle: dict, rel: str) -> str | None:
    """Get link from FHIR Bundle list"""
    for link in bundle["link"]:
        if link["relation"] == rel:
            return link["url"]
    return None


def fetch_paginated(client, path, params=None, *, return_pages=False):
    params = params or {}
    if "/fhir/" in path:
        Pagination = FHIRBundlePagination
        result_key = "entry"
        total_key = "total"
        get_next = partial(get_link, rel="next")
        page_size_param = "_count"
    else:
        Pagination = CustomPageNumberPagination
        result_key = "results"
        total_key = "count"
        page_size_param = "pageSize"
        get_next = itemgetter("next")

    per_page = int(params.get(page_size_param, Pagination.page_size))
    r = client.get(path, params)
    assert r.status_code == 200, f"{r.status_code} != 200: {r.text}"
    page = r.json()
    pages = [page]
    all_results = []
    page_results = page[result_key]
    all_results.extend(page_results)
    next_url = get_next(page)
    if next_url:
        assert len(page[result_key]) == per_page, f"{len(page[result_key])} != {per_page}"

    visited = {path}
    while next_url:
        assert next_url not in visited, f"repeated {next_url} in {visited}"
        visited.add(next_url)
        r = client.get(next_url)
        assert r.status_code == 200, f"{r.status_code} != 200: {r.text}"
        page = r.json()
        pages.append(page)
        next_url = get_next(page)
        page_results = page[result_key]
        if next_url:
            assert len(page_results) == per_page, f"{len(page_results)} != {per_page}"
        assert page_results
        all_results.extend(page_results)

    assert len(all_results) == pages[0][total_key], f"{len(all_results)} != {pages[0][total_key]}"
    if return_pages:
        return pages
    else:
        return all_results
