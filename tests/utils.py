"""
Test utilities for populating the test db state
"""

import uuid
from enum import Enum
from copy import deepcopy

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
