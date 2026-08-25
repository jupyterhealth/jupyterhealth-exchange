from datetime import date

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone as dj_timezone

from core.management.commands import seed_patient_demo as command
from core.management.commands import seed_rich_demo as generator
from core.management.commands.seed import Command as SeedCommand
from core.models import (
    CodeableConcept,
    JheUser,
    Observation,
    Study,
    StudyPatient,
    StudyPatientScopeConsent,
    StudyScopeRequest,
)


def create_patient(email, organization=None, birth_date=date(1970, 5, 4)):
    user = JheUser.objects.create_user(email=email, password="unused", user_type="patient")
    patient = user.patient_profile
    patient.birth_date = birth_date
    patient.save()
    if organization:
        patient.organizations.add(organization)
    return patient


def generated_codes():
    return [generator.CGM_CODE] + [code for code, _system, _label in generator.WEARABLE_SCOPES]


@pytest.fixture
def demo_patient(organization, db):
    SeedCommand.seed_codeable_concepts()
    return create_patient("demo.patient@example.com", organization)


@pytest.fixture
def seeded_patient(demo_patient, monkeypatch):
    monkeypatch.setattr(command, "CGM_WINDOW_DAYS", 1)
    monkeypatch.setattr(command, "WEARABLE_MIN_DAYS", 2)
    monkeypatch.setattr(command, "WEARABLE_MAX_DAYS", 2)
    call_command("seed_patient_demo", f"--patient-id={demo_patient.id}")
    return demo_patient


@pytest.mark.django_db
def test_missing_patient_id_raises():
    with pytest.raises(CommandError, match="--patient-id"):
        call_command("seed_patient_demo")


@pytest.mark.django_db
def test_unknown_patient_id_raises():
    with pytest.raises(CommandError, match="No Patient"):
        call_command("seed_patient_demo", "--patient-id=999999")


@pytest.mark.django_db
def test_patient_without_organization_raises(db):
    SeedCommand.seed_codeable_concepts()
    patient = create_patient("orphan.patient@example.com")
    with pytest.raises(CommandError, match="Organization"):
        call_command("seed_patient_demo", f"--patient-id={patient.id}")


@pytest.mark.django_db
def test_unusable_birth_date_raises(organization, db):
    SeedCommand.seed_codeable_concepts()
    patient = create_patient("newborn.patient@example.com", organization, birth_date=dj_timezone.localdate())
    with pytest.raises(CommandError, match="birth date"):
        call_command("seed_patient_demo", f"--patient-id={patient.id}")


@pytest.mark.django_db
def test_missing_codeable_concepts_raises(organization):
    patient = create_patient("unseeded.patient@example.com", organization)
    with pytest.raises(CommandError, match="seed"):
        call_command("seed_patient_demo", f"--patient-id={patient.id}")


@pytest.mark.django_db
def test_enrolls_the_patient_with_consent_for_every_scope(seeded_patient):
    study = Study.objects.get(name=generator.STUDY_NAME)
    assert study.organization_id == seeded_patient.organizations.first().id
    assert StudyPatient.objects.filter(study=study, patient=seeded_patient).exists()
    for code in generated_codes():
        assert StudyScopeRequest.objects.filter(study=study, scope_code__coding_code=code).exists(), code
        assert StudyPatientScopeConsent.objects.filter(
            study_patient__study=study,
            study_patient__patient=seeded_patient,
            scope_code__coding_code=code,
            consented=True,
        ).exists(), code


@pytest.mark.django_db
def test_creates_observations_for_every_scope(seeded_patient):
    cgm = CodeableConcept.objects.get(coding_system=generator.OMH, coding_code=generator.CGM_CODE)
    expected_cgm = (command.CGM_WINDOW_DAYS * 24 * 60) // command.CGM_INTERVAL_MINUTES + 1
    assert Observation.objects.filter(subject_patient=seeded_patient, codeable_concept=cgm).count() == expected_cgm
    for code, system, _label in generator.WEARABLE_SCOPES:
        concept = CodeableConcept.objects.get(coding_system=system, coding_code=code)
        assert Observation.objects.filter(subject_patient=seeded_patient, codeable_concept=concept).exists(), code


@pytest.mark.django_db
def test_observations_are_time_queryable(seeded_patient):
    untimed = Observation.objects.filter(
        subject_patient=seeded_patient,
        effective_date_time__isnull=True,
        effective_period_start__isnull=True,
        effective_period_end__isnull=True,
    )
    assert untimed.count() == 0


@pytest.mark.django_db
def test_latest_cgm_reading_is_dated_today(seeded_patient):
    cgm = CodeableConcept.objects.get(coding_system=generator.OMH, coding_code=generator.CGM_CODE)
    latest = (
        Observation.objects.filter(subject_patient=seeded_patient, codeable_concept=cgm)
        .order_by("-omh_data__header__source_creation_date_time")
        .first()
    )
    assert latest.omh_data["header"]["source_creation_date_time"][:10] == dj_timezone.localdate().isoformat()


@pytest.mark.django_db
def test_observations_are_visible_to_a_practitioner_under_the_study_filter(user, seeded_patient):
    study = Study.objects.get(name=generator.STUDY_NAME)
    visible = Observation.for_practitioner_organization_study_patient(
        user.id,
        study_id=study.id,
        patient_id=seeded_patient.id,
    )
    assert visible.count() > 0


@pytest.mark.django_db
def test_second_run_refuses(seeded_patient):
    before = Observation.objects.filter(subject_patient=seeded_patient).count()
    with pytest.raises(CommandError):
        call_command("seed_patient_demo", f"--patient-id={seeded_patient.id}")
    assert Observation.objects.filter(subject_patient=seeded_patient).count() == before


@pytest.mark.django_db
def test_does_not_touch_other_patients(organization, demo_patient, monkeypatch):
    monkeypatch.setattr(command, "CGM_WINDOW_DAYS", 1)
    monkeypatch.setattr(command, "WEARABLE_MIN_DAYS", 2)
    monkeypatch.setattr(command, "WEARABLE_MAX_DAYS", 2)
    bystander = create_patient("bystander.patient@example.com", organization)

    call_command("seed_patient_demo", f"--patient-id={demo_patient.id}")

    assert Observation.objects.filter(subject_patient=bystander).count() == 0
    assert Observation.objects.filter(subject_patient=demo_patient).count() > 0
