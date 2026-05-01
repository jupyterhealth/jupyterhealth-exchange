from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone
from django.utils.crypto import get_random_string
from faker import Faker
from oauth2_provider.models import get_application_model

from core.models import (
    CodeableConcept,
    DataSource,
    JheSetting,
    JheUser,
    Observation,
    Organization,
    PractitionerOrganization,
    Study,
    StudyPatient,
    StudyPatientScopeConsent,
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
            self.seed_jhe_settings()
            self.seed_codeable_concept()
            self.seed_data_source()
            root_organization = self.create_root_organization()
            self.seed_example_institute(root_organization)
            self.seed_health_system(root_organization)
            self.seed_oauth_application()

        self.stdout.write(self.style.SUCCESS("Seeding complete."))

    def seed_jhe_settings(self):
        invite_code = "jhe"

        jhe_settings = [
            ("site.url", "string", "http://localhost:8000"),
            ("site.ui.title", "string", "JupyterHealth Exchange"),
            ("site.time_zone", "string", "America/Los_Angeles"),
            ("site.registration_invite_code", "string", invite_code),
            ("auth.default_orgs", "string", ""),  # "20001:v;20002:m"
            ("auth.sso.saml2", "int", 0),
            ("auth.sso.idp_metadata_url", "string", ""),
            ("auth.sso.valid_domains", "string", ""),
        ]
        for key, value_type, value in jhe_settings:
            setting, _ = JheSetting.objects.update_or_create(
                key=key,
                setting_id=None,
                defaults={"value_type": value_type},
            )
            setting.set_value(value_type, str(value) if value_type == "int" else value)
            setting.save()

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

    def seed_example_institute(self, root_organization):
        planetary_research_institute = Organization.objects.create(
            name="Planetary Research Institute",
            type="edu",
            part_of=root_organization,
        )
        saturn_school_of_data_science = Organization.objects.create(
            name="Saturn School of Data Science",
            type="team",
            part_of=planetary_research_institute,
        )
        lifespan_lab = Organization.objects.create(name="Lifespan Lab", type="laboratory", part_of=saturn_school_of_data_science)

        manager_mary = self.create_user_with_profile("manager_mary@example.com")

        manager_links = [
            PractitionerOrganization(practitioner=manager_mary, organization=org, role="manager") for org in [planetary_research_institute, saturn_school_of_data_science, lifespan_lab]
        ]
        PractitionerOrganization.objects.bulk_create(manager_links)

        member_megan = self.create_user_with_profile("member_megan@example.com")
        PractitionerOrganization.objects.create(practitioner=member_megan, organization=lifespan_lab, role="member")

        viewer_victor = self.create_user_with_profile("viewer_victor@example.com")
        PractitionerOrganization.objects.create(practitioner=viewer_victor, organization=lifespan_lab, role="viewer")

        three_org_tom = self.create_user_with_profile("three_org_tom@example.com")
        PractitionerOrganization.objects.create(practitioner=three_org_tom, organization=lifespan_lab, role="viewer")

        lifespan_study_bp_hr = Study.objects.create(
            name="Lifespan Study on BP & HR",
            description="Blood Pressure & Heart Rate",
            organization=lifespan_lab,
        )
        lifespan_study_bp = Study.objects.create(name="Lifespan Study on BP", description="Blood Pressure", organization=lifespan_lab)

        bp_code = CodeableConcept.objects.get(coding_code="omh:blood-pressure:4.0")
        hr_code = CodeableConcept.objects.get(coding_code="omh:heart-rate:2.0")

        StudyScopeRequest.objects.create(study=lifespan_study_bp_hr, scope_code=bp_code)
        StudyScopeRequest.objects.create(study=lifespan_study_bp_hr, scope_code=hr_code)
        StudyScopeRequest.objects.create(study=lifespan_study_bp, scope_code=bp_code)

        ll_patient_pete = self.create_user_with_profile("ll_patient_peter@example.com", user_type="patient")
        ll_patient_pete.organizations.add(lifespan_lab)
        ll_patient_pamela = self.create_user_with_profile("ll_patient_pamela@example.com", user_type="patient")
        ll_patient_pamela.organizations.add(lifespan_lab)

        sp_peter_bp_hr = StudyPatient.objects.create(study=lifespan_study_bp_hr, patient=ll_patient_pete)
        sp_peter_bp = StudyPatient.objects.create(study=lifespan_study_bp, patient=ll_patient_pete)  # noqa
        sp_pamela_bp_hr = StudyPatient.objects.create(study=lifespan_study_bp_hr, patient=ll_patient_pamela)
        sp_pamela_bp = StudyPatient.objects.create(study=lifespan_study_bp, patient=ll_patient_pamela)

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

        planetary_research_institute_study_patients = [sp_peter_bp_hr, sp_peter_bp, sp_pamela_bp_hr, sp_pamela_bp]
        for consent in StudyPatientScopeConsent.objects.filter(consented=True, study_patient__in=planetary_research_institute_study_patients):
            scope_code = consent.scope_code
            Observation.objects.create(
                subject_patient=consent.study_patient.patient,
                codeable_concept=scope_code,
                value_attachment_data=generate_observation_value_attachment_data(consent.scope_code.coding_code),
            )

    def seed_health_system(self, root_organization):
        nhs = Organization.objects.create(
            name="Neptune Health System",
            type="prov",
            part_of=root_organization,
        )
        department_of_medicine = Organization.objects.create(name="Department of Medicine", type="dept", part_of=nhs)
        cardiology_division = Organization.objects.create(name="Cardiology Division", type="dept", part_of=department_of_medicine)
        neptunian_pulse_lab = Organization.objects.create(name="Neptunian Pulse Lab", type="laboratory", part_of=cardiology_division)
        cosmic_cardio_lab = Organization.objects.create(name="Cosmic Cardio Lab", type="laboratory", part_of=cardiology_division)

        manager_mark = self.create_user_with_profile("manager_mark@example.com", user_type="practitioner")
        practitioner_org_links = [
            PractitionerOrganization(practitioner=manager_mark, organization=org, role="manager")
            for org in [nhs, department_of_medicine, cardiology_division, neptunian_pulse_lab]
        ]
        PractitionerOrganization.objects.bulk_create(practitioner_org_links)

        three_org_tom = JheUser.objects.get(email="three_org_tom@example.com").practitioner
        PractitionerOrganization.objects.create(practitioner=three_org_tom, organization=neptunian_pulse_lab, role="member")
        PractitionerOrganization.objects.create(practitioner=three_org_tom, organization=cosmic_cardio_lab, role="manager")

        rr_code = CodeableConcept.objects.get(coding_code="omh:respiratory-rate:2.0")
        bt_code = CodeableConcept.objects.get(coding_code="omh:body-temperature:4.0")
        o2_code = CodeableConcept.objects.get(coding_code="omh:oxygen-saturation:2.0")

        cardio_rr = Study.objects.create(
            name="Cardiology Div Study on RR",
            description="Respiratory rate",
            organization=cardiology_division,
        )
        neptunian_pulse_lab_bt = Study.objects.create(
            name="Nep Pulse Lab Study on BT",
            description="Body Temperature",
            organization=neptunian_pulse_lab,
        )
        cosmic_cardio_lab_o2 = Study.objects.create(
            name="Cosmic Cardio Lab Study on O2",
            description="Oxygen Saturation",
            organization=cosmic_cardio_lab,
        )

        StudyScopeRequest.objects.create(study=cardio_rr, scope_code=rr_code)
        StudyScopeRequest.objects.create(study=neptunian_pulse_lab_bt, scope_code=bt_code)
        StudyScopeRequest.objects.create(study=cosmic_cardio_lab_o2, scope_code=o2_code)

        neptunian_pulse_lab_patient_percy = self.create_user_with_profile("npl_patient_percy@example.com", user_type="patient")
        neptunian_pulse_lab_patient_percy.organizations.add(neptunian_pulse_lab)
        neptunian_pulse_lab_ccl_patient_paul = self.create_user_with_profile("ccl_patient_paul@example.com", user_type="patient")
        neptunian_pulse_lab_ccl_patient_paul.organizations.add(cosmic_cardio_lab)
        ccl_cardio_patient_pat = self.create_user_with_profile("ccl_cardio_patient_pat@example.com", user_type="patient")
        ccl_cardio_patient_pat.organizations.add(cardiology_division, cosmic_cardio_lab)

        sp_percy_bt = StudyPatient.objects.create(study=neptunian_pulse_lab_bt, patient=neptunian_pulse_lab_patient_percy)
        sp_paul_o2 = StudyPatient.objects.create(study=cosmic_cardio_lab_o2, patient=neptunian_pulse_lab_ccl_patient_paul)
        sp_pat_rr = StudyPatient.objects.create(study=cardio_rr, patient=ccl_cardio_patient_pat)
        sp_pat_o2 = StudyPatient.objects.create(study=cosmic_cardio_lab_o2, patient=ccl_cardio_patient_pat)

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

        med_study_patients = [sp_percy_bt, sp_paul_o2, sp_pat_rr, sp_pat_o2]
        for consent in StudyPatientScopeConsent.objects.filter(consented=True, study_patient__in=med_study_patients):
            scope_code = consent.scope_code
            Observation.objects.create(
                subject_patient=consent.study_patient.patient,
                codeable_concept=scope_code,
                value_attachment_data=generate_observation_value_attachment_data(consent.scope_code.coding_code),
            )

    def seed_oauth_application(self, name="JHE Admin UI"):
        application = get_application_model()
        application.objects.create(
            redirect_uris=settings.SITE_URL + settings.OAUTH2_CALLBACK_PATH,
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
    def generate_superuser(email="admin@example.com", password="Jhe1234!"):
        JheUser.objects.create_superuser(
            email=email,
            password=password,
        )
