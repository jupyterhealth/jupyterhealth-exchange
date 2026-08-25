import random
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone as dj_timezone

from core.management.commands.seed_rich_demo import (
    CGM_CODE,
    CGM_DATA_SOURCE,
    CGM_INTERVAL_MINUTES,
    CGM_SOURCE_NAME,
    CGM_WINDOW_DAYS,
    OMH,
    SEED,
    STUDY_NAME,
    WEARABLE_DATA_SOURCE,
    WEARABLE_MAX_DAYS,
    WEARABLE_MIN_DAYS,
    WEARABLE_SCOPES,
    WEARABLE_SOURCE_NAME,
    cgm_body,
    cgm_value,
    generate_wearable_day,
    risk_score,
)
from core.models import (
    CodeableConcept,
    DataSource,
    Observation,
    Patient,
    Study,
    StudyDataSource,
    StudyPatient,
    StudyPatientScopeConsent,
    StudyScopeRequest,
)

MINIMUM_PLAUSIBLE_AGE_DAYS = 365


def required_concepts():
    codes = [(OMH, CGM_CODE)] + [(system, code) for code, system, _label in WEARABLE_SCOPES]
    concepts = {}
    for system, code in codes:
        concept = CodeableConcept.objects.filter(coding_system=system, coding_code=code).first()
        if not concept:
            raise CommandError(f"Missing CodeableConcept '{code}' - run `seed` first.")
        concepts[code] = concept
    return concepts


def existing_demo_observations(patient):
    return Observation.objects.filter(
        subject_patient=patient,
        omh_data__header__acquisition_provenance__source_name__in=[CGM_SOURCE_NAME, WEARABLE_SOURCE_NAME],
    )


def build_observation(patient, concept, data_source, omh_data):
    observation = Observation(
        subject_patient=patient,
        codeable_concept=concept,
        data_source=data_source,
        status="final",
        omh_data=omh_data,
    )
    observation._sync_effective_time_frame()
    return observation


class Command(BaseCommand):
    help = "Attach synthetic demo data (CGM + Oura wearables) to an existing patient."

    def add_arguments(self, parser):
        parser.add_argument(
            "--patient-id",
            type=int,
            default=None,
            help="Attach demo data to this patient, by the id field of /api/v1/patients (not jheUserId).",
        )

    def handle(self, *args, **options):
        patient_id = options.get("patient_id")
        if not patient_id:
            raise CommandError("--patient-id is required.")

        patient = Patient.objects.filter(pk=patient_id).first()
        if not patient:
            raise CommandError(f"No Patient with id {patient_id}.")

        now = dj_timezone.localtime()
        today = now.date()
        if not patient.birth_date or (today - patient.birth_date).days < MINIMUM_PLAUSIBLE_AGE_DAYS:
            raise CommandError(f"Patient {patient_id} has no usable birth date ({patient.birth_date}).")

        organization = patient.organizations.order_by("id").first()
        if not organization:
            raise CommandError(f"Patient {patient_id} belongs to no Organization.")

        concepts = required_concepts()
        if existing_demo_observations(patient).exists():
            raise CommandError(f"Patient {patient_id} already has demo data.")

        cgm_source, _ = DataSource.objects.get_or_create(name=CGM_DATA_SOURCE, defaults={"type": "personal_device"})
        wearable_source, _ = DataSource.objects.get_or_create(
            name=WEARABLE_DATA_SOURCE, defaults={"type": "personal_device"}
        )

        rng = random.Random(f"{SEED}:{patient.id}")
        age = (
            today.year
            - patient.birth_date.year
            - ((today.month, today.day) < (patient.birth_date.month, patient.birth_date.day))
        )
        risk = risk_score(age)

        with transaction.atomic():
            study, _ = Study.objects.get_or_create(
                organization=organization,
                name=STUDY_NAME,
                defaults={"description": "Synthetic CGM + Oura wearable demo data.", "icon_url": None},
            )
            StudyDataSource.objects.get_or_create(study=study, data_source=cgm_source)
            StudyDataSource.objects.get_or_create(study=study, data_source=wearable_source)
            study_patient, _ = StudyPatient.objects.get_or_create(study=study, patient=patient)
            consented_time = now - timedelta(days=WEARABLE_MAX_DAYS + 3)
            for concept in concepts.values():
                StudyScopeRequest.objects.get_or_create(
                    study=study, scope_code=concept, defaults={"scope_actions": "rs"}
                )
                StudyPatientScopeConsent.objects.update_or_create(
                    study_patient=study_patient,
                    scope_code=concept,
                    defaults={"consented": True, "consented_time": consented_time, "scope_actions": "rs"},
                )

            observations = []
            cgm_start = now - timedelta(days=CGM_WINDOW_DAYS)
            for reading in range((CGM_WINDOW_DAYS * 24 * 60) // CGM_INTERVAL_MINUTES + 1):
                reading_time = cgm_start + timedelta(minutes=reading * CGM_INTERVAL_MINUTES)
                observations.append(
                    build_observation(
                        patient,
                        concepts[CGM_CODE],
                        cgm_source,
                        cgm_body(reading_time, cgm_value(reading_time, risk, rng)),
                    )
                )

            wearable_days = rng.randint(WEARABLE_MIN_DAYS, WEARABLE_MAX_DAYS)
            start_day = today - timedelta(days=wearable_days - 1)
            for offset in range(wearable_days):
                records = generate_wearable_day(start_day + timedelta(days=offset), offset, age, risk, rng)
                for code, _system, _label in WEARABLE_SCOPES:
                    observations.append(build_observation(patient, concepts[code], wearable_source, records[code]))

            Observation.objects.bulk_create(observations, batch_size=2000)

        self.stdout.write(
            self.style.SUCCESS(
                f"Patient demo seeded: patient {patient.id}, {len(observations)} observations "
                f"in study '{study.name}' under organization '{organization.name}' (id {organization.id})."
            )
        )
