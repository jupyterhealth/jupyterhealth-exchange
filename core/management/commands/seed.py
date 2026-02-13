from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import get_random_string
from faker import Faker
from oauth2_provider.models import get_application_model

from core.models import (
    CodeableConcept,
    DataSource,
    Organization,
    StudyPatientScopeConsent,
    Study,
    PractitionerOrganization,
    StudyPatient,
    Observation,
    JheUser,
    StudyScopeRequest,
)
from core.utils import generate_observation_value_attachment_data

fake = Faker()


class Command(BaseCommand):
    help = "Seed the database"

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush-db",
            action="store_true",
            help="Flush the entire database before seeding as already seeding won't work with already populated DB.",
        )

    def handle(self, *args, **options):
        self.stdout.write("Seeding RBAC…")
        if options["flush_db"]:
            self.stdout.write("Flushing the database…")
            call_command("flush", "--noinput")
        with transaction.atomic():
            self.reset_sequences()
            self.generate_superuser()
            self.seed_codeable_concept()
            self.seed_data_source()
            root_organization = self.create_root_organization()
            self.seed_berkeley(root_organization)
            self.seed_ucsf(root_organization)
            self.seed_oauth_application()

        self.stdout.write(self.style.SUCCESS("Seeding complete."))

    @staticmethod
    def us_phone_number():
        return fake.numerify(text="+1-###-###-####")

    @staticmethod
    def reset_sequences(restart_with=10001):
        with connection.cursor() as cursor:
            seqs = [
                "core_jheuser_id_seq",
                "core_organization_id_seq",
                "core_study_id_seq",
                "core_patient_id_seq",
                "core_codeableconcept_id_seq",
                "core_observation_id_seq",
                "core_datasource_id_seq",
                "core_practitioner_id_seq",
            ]

            for seq in seqs:
                cursor.execute(f"ALTER SEQUENCE {seq} RESTART WITH %s;", [restart_with])
                restart_with = restart_with + 10000

    @staticmethod
    def seed_codeable_concept():
        codes = [
            ("https://w3id.org/openmhealth", "omh:blood-glucose:4.0", "Blood glucose"),
            (
                "https://w3id.org/openmhealth",
                "omh:blood-pressure:4.0",
                "Blood pressure",
            ),
            (
                "https://w3id.org/openmhealth",
                "omh:body-temperature:4.0",
                "Body temperature",
            ),
            ("https://w3id.org/openmhealth", "omh:heart-rate:2.0", "Heart Rate"),
            (
                "https://w3id.org/openmhealth",
                "omh:oxygen-saturation:2.0",
                "Oxygen saturation",
            ),
            (
                "https://w3id.org/openmhealth",
                "omh:respiratory-rate:2.0",
                "Respiratory rate",
            ),
            ("https://w3id.org/openmhealth", "omh:rr-interval:1.0", "RR Interval"),
        ]
        # bulk create thing
        for system, code, text in codes:
            CodeableConcept.objects.update_or_create(
                coding_system=system,
                coding_code=code,
                text=text,
            )

    def seed_data_source(self):
        data_source = [
            ("CareX", "personal_device"),
            ("Dexcom", "personal_device"),
            ("iHealth", "personal_device"),
        ]
        for name, type in data_source:
            DataSource.objects.update_or_create(name=name, type=type)

    @staticmethod
    def create_root_organization():
        return Organization.objects.create(id=0, name="ROOT", type="root")

    def seed_berkeley(self, root_organization):

        ucb = Organization.objects.create(
            name="University of California Berkeley",
            type="edu",
            part_of=root_organization,
        )
        ccdss = Organization.objects.create(
            name="College of Computing, Data Science and Society",
            type="edu",
            part_of=ucb,
        )
        bids = Organization.objects.create(name="Berkeley Institute for Data Science (BIDS)", type="edu", part_of=ccdss)

        mary = self.create_user_with_profile("mary@example.com")

        manager_links = [
            PractitionerOrganization(practitioner=mary, organization=org, role="manager") for org in [ucb, ccdss, bids]
        ]
        PractitionerOrganization.objects.bulk_create(manager_links)

        megan = self.create_user_with_profile("megan@example.com")
        PractitionerOrganization.objects.create(practitioner=megan, organization=bids, role="member")

        victor = self.create_user_with_profile("victor@example.com")
        PractitionerOrganization.objects.create(practitioner=victor, organization=bids, role="viewer")

        tom = self.create_user_with_profile("tom@example.com")
        PractitionerOrganization.objects.create(practitioner=tom, organization=bids, role="viewer")

        # 3) Create BIDS studies
        bp_hr = Study.objects.create(
            name="BIDS Study on BP & HR",
            description="Blood Pressure & Heart Rate",
            organization=bids,
        )
        bp = Study.objects.create(name="BIDS Study on BP", description="Blood Pressure", organization=bids)

        bp_code = CodeableConcept.objects.get(coding_code="omh:blood-pressure:4.0")
        hr_code = CodeableConcept.objects.get(coding_code="omh:heart-rate:2.0")

        StudyScopeRequest.objects.create(study=bp_hr, scope_code=bp_code)
        StudyScopeRequest.objects.create(study=bp_hr, scope_code=hr_code)
        StudyScopeRequest.objects.create(study=bp, scope_code=bp_code)

        peter = self.create_user_with_profile("peter@example.com", user_type="patient")
        peter.organizations.add(bids)
        pamela = self.create_user_with_profile("pamela@example.com", user_type="patient")
        pamela.organizations.add(bids)

        sp_peter_bp_hr = StudyPatient.objects.create(study=bp_hr, patient=peter)
        sp_peter_bp = StudyPatient.objects.create(study=bp, patient=peter)  # noqa
        sp_pamela_bp_hr = StudyPatient.objects.create(study=bp_hr, patient=pamela)
        sp_pamela_bp = StudyPatient.objects.create(study=bp, patient=pamela)

        now = timezone.now()
        StudyPatientScopeConsent.objects.create(
            study_patient=sp_peter_bp_hr,
            scope_code=bp_code,
            consented=True,
            consented_time=now,
        )
        StudyPatientScopeConsent.objects.create(
            study_patient=sp_peter_bp_hr,
            scope_code=hr_code,
            consented=True,
            consented_time=now,
        )

        for sp, codes in [
            (sp_pamela_bp_hr, [bp_code, hr_code]),
            (sp_pamela_bp, [bp_code]),
        ]:
            for code in codes:
                StudyPatientScopeConsent.objects.create(
                    study_patient=sp,
                    scope_code=code,
                    consented=True,
                    consented_time=now,
                )

        for consent in StudyPatientScopeConsent.objects.filter(consented=True):
            scope_code = consent.scope_code
            Observation.objects.create(
                subject_patient=consent.study_patient.patient,
                codeable_concept=scope_code,
                value_attachment_data=generate_observation_value_attachment_data(consent.scope_code.coding_code),
            )

    def seed_ucsf(self, root_organization):

        ucsf = Organization.objects.create(
            name="University of California San Francisco",
            type="edu",
            part_of=root_organization,
        )
        med = Organization.objects.create(name="Department of Medicine", type="edu", part_of=ucsf)
        cardio = Organization.objects.create(name="Cardiology", type="edu", part_of=med)
        mosl = Organization.objects.create(name="Moslehi Lab", type="laboratory", part_of=cardio)
        olgin = Organization.objects.create(name="Olgin Lab", type="laboratory", part_of=cardio)

        mark = self.create_user_with_profile("mark@example.com", user_type="practitioner")
        practitioner_org_links = [
            PractitionerOrganization(practitioner=mark, organization=org, role="manager")
            for org in [ucsf, med, cardio, mosl]
        ]
        PractitionerOrganization.objects.bulk_create(practitioner_org_links)

        tom = JheUser.objects.get(email="tom@example.com").practitioner
        PractitionerOrganization.objects.create(practitioner=tom, organization=mosl, role="member")
        PractitionerOrganization.objects.create(practitioner=tom, organization=olgin, role="manager")

        rr_code = CodeableConcept.objects.get(coding_code="omh:respiratory-rate:2.0")
        bt_code = CodeableConcept.objects.get(coding_code="omh:body-temperature:4.0")
        o2_code = CodeableConcept.objects.get(coding_code="omh:oxygen-saturation:2.0")

        cardio_rr = Study.objects.create(
            name="Cardio Study on RR",
            description="Respiratory rate",
            organization=cardio,
        )
        mosl_bt = Study.objects.create(
            name="Moslehi Study on BT",
            description="Body Temperature",
            organization=mosl,
        )
        olgin_o2 = Study.objects.create(
            name="Olgin Study on O2",
            description="Oxygen Saturation",
            organization=olgin,
        )

        StudyScopeRequest.objects.create(study=cardio_rr, scope_code=rr_code)
        StudyScopeRequest.objects.create(study=mosl_bt, scope_code=bt_code)
        StudyScopeRequest.objects.create(study=olgin_o2, scope_code=o2_code)

        percy = self.create_user_with_profile("percy@example.com", user_type="patient")
        percy.organizations.add(mosl)
        paul = self.create_user_with_profile("paul@example.com", user_type="patient")
        paul.organizations.add(olgin)
        pat = self.create_user_with_profile("pat@example.com", user_type="patient")
        pat.organizations.add(cardio, olgin)

        sp_percy_bt = StudyPatient.objects.create(study=mosl_bt, patient=percy)
        sp_paul_o2 = StudyPatient.objects.create(study=olgin_o2, patient=paul)
        sp_pat_rr = StudyPatient.objects.create(study=cardio_rr, patient=pat)
        sp_pat_o2 = StudyPatient.objects.create(study=olgin_o2, patient=pat)

        now = timezone.now()

        StudyPatientScopeConsent.objects.create(
            study_patient=sp_percy_bt,
            scope_code=bt_code,
            consented=True,
            consented_time=now,
        )
        StudyPatientScopeConsent.objects.create(
            study_patient=sp_paul_o2,
            scope_code=o2_code,
            consented=True,
            consented_time=now,
        )
        StudyPatientScopeConsent.objects.create(
            study_patient=sp_pat_rr,
            scope_code=rr_code,
            consented=True,
            consented_time=now,
        )
        StudyPatientScopeConsent.objects.create(
            study_patient=sp_pat_o2,
            scope_code=o2_code,
            consented=True,
            consented_time=now,
        )

        for consent in StudyPatientScopeConsent.objects.filter(consented=True):
            scope_code = consent.scope_code
            Observation.objects.create(
                subject_patient=consent.study_patient.patient,
                codeable_concept=scope_code,
                value_attachment_data=generate_observation_value_attachment_data(consent.scope_code.coding_code),
            )

    @staticmethod
    def seed_oauth_application(name="JHE Dev"):

        application = get_application_model()
        application.objects.create(
            id=1,
            redirect_uris=settings.SITE_URL+settings.OAUTH2_CALLBACK_PATH,
            client_type="public",
            authorization_grant_type="authorization-code",
            client_secret="pbkdf2_sha256$870000$Hrxk93CVKgRSGJdyusw4go$umXWiaCn152vXWiXl1bQZwupccDt18QiQcotff+hBmQ=",
            name=name,
            user_id=None,
            skip_authorization=True,
            created=timezone.now(),
            updated=timezone.now(),
            algorithm="RS256",
            post_logout_redirect_uris="",
            hash_client_secret=True,
            allowed_origins="",
        )

    def create_user_with_profile(self, email, user_type="practitioner", password="Jhe1234!"):
        user = JheUser.objects.create_user(
            email=email,
            password=password or get_random_string(length=16),
            first_name=email.split("@")[0].capitalize(),
            last_name=fake.last_name(),
            user_type=user_type,
        )
        user.identifier = f"fhir-{str(user.id)[-1] * 3}"
        user.save()
        if user_type == "practitioner":
            practitioner = user.practitioner_profile
            practitioner.birth_date = fake.date_of_birth(minimum_age=25, maximum_age=45)
            practitioner.telecom_phone = self.us_phone_number()
            practitioner.save()
            return practitioner
        elif user_type == "patient":
            patient = user.patient_profile
            patient.birth_date = fake.date_of_birth(minimum_age=25, maximum_age=45)
            patient.telecom_phone = self.us_phone_number()
            patient.save()
            return patient
        return None

    @staticmethod
    def generate_superuser(email="sam@example.com", password="Jhe1234!"):
        JheUser.objects.create_superuser(
            email=email,
            password=password,
        )
