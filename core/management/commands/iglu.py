import copy
import csv
from datetime import datetime, timedelta
from django.utils import timezone
from pathlib import Path
from uuid import uuid4

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.crypto import get_random_string

from core.models import (
    JheUser,
    Organization,
    StudyPatient,
    CodeableConcept,
    Observation,
    Study,
    StudyPatientScopeConsent,
    StudyScopeRequest,
    StudyDataSource,
    DataSource,
)

OMH_BLOOD_GLUCOSE_TEMPLATE = {
    "header": {
        "modality": "sensed",
        "uuid": "aaaa1234-1a2b-3c4d-5e6f-000000000001",
        "schema_id": {"name": "blood-glucose", "version": "4.0", "namespace": "omh"},
        "external_datasheets": [{"datasheet_type": "manufacturer", "datasheet_reference": "Dexcom"}],
        "source_creation_date_time": None,
    },
    "body": {
        "blood_glucose": {"unit": "mg/dL", "value": None},
        "effective_time_frame": {"date_time": None},
    },
}
MOCK_PATIENTS = [
    {
        "name_family": "Nguyen",
        "name_given": "May",
        "birth_date": "1984-07-11",
        "telecom_phone": "265-642-0143",
        "email": "may.nguyen@example.com",
    },
    {
        "name_family": "Smith",
        "name_given": "Olivia",
        "birth_date": "1976-03-23",
        "telecom_phone": "187-554-0198",
        "email": "olivia.smith@example.com",
    },
    {
        "name_family": "Chen",
        "name_given": "Liang",
        "birth_date": "1948-11-30",
        "telecom_phone": "997-576-0102",
        "email": "liang.chen@example.com",
    },
    {
        "name_family": "Patel",
        "name_given": "Anika",
        "birth_date": "1989-01-17",
        "telecom_phone": "345-233-0170",
        "email": "anika.patel@example.com",
    },
    {
        "name_family": "Garcia",
        "name_given": "Carlos",
        "birth_date": "1955-05-04",
        "telecom_phone": "609-442-0186",
        "email": "carlos.garcia@example.com",
    },
    {
        "name_family": "Okafor",
        "name_given": "Chinelo",
        "birth_date": "1962-08-19",
        "telecom_phone": "435-287-0116",
        "email": "chinelo.okafor@example.com",
    },
    {
        "name_family": "Kowalski",
        "name_given": "Zofia",
        "birth_date": "1945-02-14",
        "telecom_phone": "399-765-0124",
        "email": "zofia.kowalski@example.com",
    },
    {
        "name_family": "Tanaka",
        "name_given": "Hiroshi",
        "birth_date": "1958-10-01",
        "telecom_phone": "298-443-0131",
        "email": "hiroshi.tanaka@example.com",
    },
    {
        "name_family": "Abdullah",
        "name_given": "Layla",
        "birth_date": "1973-12-25",
        "telecom_phone": "198-619-0149",
        "email": "layla.abdullah@example.com",
    },
    {
        "name_family": "Dubois",
        "name_given": "Emile",
        "birth_date": "1981-06-03",
        "telecom_phone": "400-870-0162",
        "email": "emile.dubois@example.com",
    },
    {
        "name_family": "Singh",
        "name_given": "Raj",
        "birth_date": "1992-09-12",
        "telecom_phone": "398-112-0181",
        "email": "raj.singh@example.com",
    },
    {
        "name_family": "Martinez",
        "name_given": "Sofia",
        "birth_date": "1967-07-27",
        "telecom_phone": "229-998-0108",
        "email": "sofia.martinez@example.com",
    },
    {
        "name_family": "Kim",
        "name_given": "Jisoo",
        "birth_date": "1950-04-20",
        "telecom_phone": "988-889-0157",
        "email": "jisoo.kim@example.com",
    },
    {
        "name_family": "Ivanov",
        "name_given": "Dmitri",
        "birth_date": "1983-02-05",
        "telecom_phone": "799-443-0129",
        "email": "dmitri.ivanov@example.com",
    },
    {
        "name_family": "Mbatha",
        "name_given": "Sipho",
        "birth_date": "1979-11-08",
        "telecom_phone": "762-112-0140",
        "email": "sipho.mbatha@example.com",
    },
    {
        "name_family": "Rossi",
        "name_given": "Giulia",
        "birth_date": "1960-05-30",
        "telecom_phone": "772-981-0169",
        "email": "giulia.rossi@example.com",
    },
    {
        "name_family": "Hernandez",
        "name_given": "Luis",
        "birth_date": "1952-03-14",
        "telecom_phone": "118-112-0194",
        "email": "luis.hernandez@example.com",
    },
    {
        "name_family": "Yilmaz",
        "name_given": "Aylin",
        "birth_date": "1972-01-01",
        "telecom_phone": "388-887-0175",
        "email": "aylin.yilmaz@example.com",
    },
    {
        "name_family": "Andersson",
        "name_given": "Lars",
        "birth_date": "1988-10-10",
        "telecom_phone": "334-874-0111",
        "email": "lars.andersson@example.com",
    },
    {
        "name_family": "Ali",
        "name_given": "Zara",
        "birth_date": "1947-06-06",
        "telecom_phone": "202-555-0188",
        "email": "zara.ali@example.com",
    },
]


class Command(BaseCommand):
    help = (
        "Import de-identified test observations from an IGLU CSV via Django ORM, "
        "auto-creating missing patients from a mock list."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv-file",
            type=Path,
            default=Path.cwd() / "../data/iglu/iglu_example_data_hall.csv",
            help="Path to the IGLU CSV of test measurements",
        )

    def handle(self, *args, **options):
        csv_path: Path = options["csv_file"]
        if not csv_path.exists():
            self.stderr.write(self.style.ERROR(f"File not found: {csv_path}"))
            return

        # Resolve scope code for OMH blood glucose 4.0
        schema = OMH_BLOOD_GLUCOSE_TEMPLATE["header"]["schema_id"]
        coding_code = f"{schema['namespace']}:{schema['name']}:{schema['version']}"
        try:
            bg_scope_code = CodeableConcept.objects.get(coding_code=coding_code)
        except CodeableConcept.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Missing CodeableConcept '{coding_code}'"))
            return

        # Find an org by name fragment
        organization = Organization.objects.filter(name__icontains="Example Research Institute").first()
        if not organization:
            self.stderr.write(self.style.ERROR("Missing Organization with name containing 'Example Research Institute'"))
            return

        # Data source by name
        try:
            data_source = DataSource.objects.get(name="Dexcom")
        except DataSource.DoesNotExist:
            self.stderr.write(self.style.ERROR("Missing DataSource with name 'Dexcom'"))
            return

        # Create/get a study
        iglu_study, _ = Study.objects.get_or_create(
            organization=organization,
            name="Iglu CGM Test Data",
            defaults={
                "description": "Test data from iglu project: https://github.com/irinagain/iglu",
                "icon_url": None,
            },
        )

        # Ensure required scope and device
        study_scope_request, _ = StudyScopeRequest.objects.get_or_create(
            study=iglu_study,
            scope_code=bg_scope_code,
            defaults={"scope_actions": "rs"},
        )
        StudyDataSource.objects.get_or_create(study=iglu_study, data_source=data_source)

        created = 0
        skipped = 0
        mock_pool = MOCK_PATIENTS.copy()
        subject_cache = {}  # subject_id -> (patient, study_patient)

        with transaction.atomic():
            with open(csv_path, newline="") as csvfile:
                reader = csv.reader(csvfile)
                next(reader, None)  # skip header

                for row in reader:
                    # Expect columns: [*, subject_id, timestamp, glucose, ...]
                    try:
                        subject_id = row[1].strip()
                        raw_time = row[2].strip()
                        gl_value = row[3].strip()
                    except (IndexError, AttributeError):
                        self.stdout.write(self.style.WARNING(f"Skip: bad row: {row!r}"))
                        skipped += 1
                        continue

                    # Find or create patient once per subject_id
                    if subject_id not in subject_cache:
                        sp = (
                            StudyPatient.objects.filter(patient__identifier=subject_id, study=iglu_study)
                            .select_related("patient")
                            .first()
                        )
                        if sp:
                            patient = sp.patient
                            study_patient = sp
                        else:
                            if not mock_pool:
                                self.stdout.write(self.style.ERROR(f"No mock Patient left for '{subject_id}'"))
                                skipped += 1
                                continue
                            mp = mock_pool.pop(0)

                            # Create user/patient (use manager to hash password & set flags)
                            user = JheUser.objects.create_user(
                                email=mp["email"],
                                password=get_random_string(16),
                                first_name=mp["name_given"],
                                last_name=mp["name_family"],
                                user_type="patient",
                                identifier=subject_id,
                            )
                            patient = user.patient_profile
                            patient.birth_date = mp["birth_date"]
                            patient.telecom_phone = mp["telecom_phone"]
                            patient.save()
                            patient.organizations.add(organization)

                            study_patient, _ = StudyPatient.objects.get_or_create(study=iglu_study, patient=patient)

                        subject_cache[subject_id] = (patient, study_patient)
                    else:
                        patient, study_patient = subject_cache[subject_id]

                    # Parse timestamp
                    try:
                        dt = datetime.strptime(raw_time, "%Y-%m-%d %H:%M:%S")
                        local_tz = timezone.get_default_timezone()
                        dt_local = timezone.make_aware(dt, local_tz)
                        iso_ts = dt_local.isoformat(timespec="seconds")
                    except ValueError:
                        self.stdout.write(self.style.WARNING(f"Skip: bad timestamp '{raw_time}'"))
                        skipped += 1
                        continue

                    # Build OMH payload
                    try:
                        glucose = float(gl_value)
                    except ValueError:
                        self.stdout.write(self.style.WARNING(f"Skip: bad glucose value '{gl_value}'"))
                        skipped += 1
                        continue

                    payload = copy.deepcopy(OMH_BLOOD_GLUCOSE_TEMPLATE)
                    payload["body"]["blood_glucose"]["value"] = glucose
                    payload["body"]["effective_time_frame"]["date_time"] = (dt_local - timedelta(hours=1)).isoformat(
                        timespec="seconds"
                    )
                    payload["header"]["source_creation_date_time"] = iso_ts
                    payload["header"]["uuid"] = str(uuid4())

                    # Ensure the patient has consent for this scope in this study
                    StudyPatientScopeConsent.objects.update_or_create(
                        study_patient=study_patient,
                        scope_code=study_scope_request.scope_code,
                        defaults={
                            "consented": True,
                            "consented_time": dt_local - timedelta(days=3),
                            "scope_actions": study_scope_request.scope_actions,
                        },
                    )

                    # Create observation
                    Observation.objects.create(
                        subject_patient=patient,
                        codeable_concept=bg_scope_code,
                        data_source=data_source,
                        status="final",
                        value_attachment_data=payload,
                    )
                    created += 1

        self.stdout.write(self.style.SUCCESS(f"Import complete: {created} created, {skipped} skipped."))
