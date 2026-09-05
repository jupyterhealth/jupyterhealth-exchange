import io
import os
import secrets
import string
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone
from django.utils.crypto import get_random_string
from faker import Faker
from oauth2_provider.models import get_application_model

from core.models import (
    JHE_FHIR_SOURCE_BASE,
    ClientDataSource,
    CodeableConcept,
    DataSource,
    DataSourceSupportedScope,
    EhrBrand,
    EhrBrandLocation,
    FhirAuxResource,
    FhirSource,
    JheClient,
    JheSetting,
    JheUser,
    Observation,
    Organization,
    Patient,
    PatientIdentifier,
    Practitioner,
    PractitionerOrganization,
    Study,
    StudyClient,
    StudyDataSource,
    StudyPatient,
    StudyPatientScopeConsent,
    StudyScopeRequest,
    apply_jhe_extensions,
)
from core.utils import generate_observation_value_attachment_data

fake = Faker()

SLEEP_EPISODE_CODE = "ieee:sleep-episode:1.0"


def sleep_episode_data_point(start, hours_asleep, awakenings):
    """A schema-valid ieee:sleep-episode:1.0 data point.

    Built by hand rather than via generate_observation_value_attachment_data: there is no
    example data point on disk for this code, and the generator's fallback writes an
    ``effective_time_frame.date_time``, whereas sleep-episode requires a ``time_interval``.
    """
    end = start + timedelta(hours=hours_asleep)
    return {
        "header": {
            "uuid": str(uuid.uuid4()),
            "schema_id": {"namespace": "ieee", "name": "sleep-episode", "version": "1.0"},
            "source_creation_date_time": end.isoformat().replace("+00:00", "Z"),
            "modality": "sensed",
            "acquisition_provenance": {"source_name": "Oura"},
        },
        "body": {
            "effective_time_frame": {
                "time_interval": {
                    "start_date_time": start.isoformat().replace("+00:00", "Z"),
                    "end_date_time": end.isoformat().replace("+00:00", "Z"),
                }
            },
            "total_sleep_time": {"value": round(hours_asleep * 3600), "unit": "sec"},
            "number_of_awakenings": awakenings,
            "is_main_sleep": True,
        },
    }


# aux_data keys seed owns outright: their value is derived from application code (the
# scope list must match EHR_PATIENT_PORTAL_PULLS), so a stale deployed value is a bug and seed
# overwrites it on every run. Every other key -- notably the EHR-registered `client_id`, which
# differs per deployment -- belongs to whoever set it and is preserved.
SEED_MANAGED_AUX_KEYS = frozenset({"scopes"})


def _merged_aux_data(existing, seeded):
    """Seeded aux_data merged over `existing`: seed fills gaps, operator values win, code wins.

    Ordering is deliberate. Seeded values are defaults for keys the row does not have yet;
    anything already stored beats them, so re-seeding never clobbers deployment-specific
    config; and the seed-managed keys are then re-applied last so a code-owned value can
    never go stale on an existing row.
    """
    merged = {**seeded, **(existing or {})}
    merged.update({key: value for key, value in seeded.items() if key in SEED_MANAGED_AUX_KEYS})
    return merged


class Command(BaseCommand):
    help = "Seed the database"

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush-db",
            action="store_true",
            help="Flush the entire database before seeding as already seeding won't work with already populated DB.",
        )
        # Usage: python manage.py seed --flush-db --with-rich-demo
        parser.add_argument(
            "--with-rich-demo",
            action="store_true",
            help="After the base seed, also generate the synthetic CGM + Oura demo cohort.",
        )

    # -- uniform step reporting ------------------------------------------------------
    #
    # Every step reports what it wrote in the same shape. Counts are *measured* -- row
    # counts diffed around the call -- rather than tallied by hand inside each step. A
    # hand-written total goes stale the moment a step starts writing a new model, which
    # is how this command came to report three of its dozen steps and nothing else.

    _STEP_LABEL_WIDTH = 26

    # Labels come from each model's Meta, which gets a few of them wrong for display: Django
    # pluralises Study as "studys", and the JHE-prefixed models read as lowercase "jhe ...".
    # Overriding here keeps it to the seed output rather than an AlterModelOptions migration
    # (which would also change the admin -- a separate call).
    _ROW_LABELS = {
        "study": ("study", "studies"),
        "jhe setting": ("JHE setting", "JHE settings"),
        "jhe client": ("JHE client", "JHE clients"),
    }

    def _step(self, label, models, fn, *args, **kwargs):
        """Run one seeding step, then report the rows it added on a single aligned line."""
        self._skip_reason = None
        before = [model.objects.count() for model in models]
        result = fn(*args, **kwargs)
        added = [(model, model.objects.count() - count) for model, count in zip(models, before)]

        if self._skip_reason:
            detail = self.style.WARNING(f"skipped — {self._skip_reason}")
        else:
            counts = [self._row_count(model, n) for model, n in added if n]
            detail = ", ".join(counts) if counts else "no new rows"
        self.stdout.write(f"  {label.ljust(self._STEP_LABEL_WIDTH)}{detail}")
        return result

    def _skip(self, reason):
        """Called by a step that deliberately did nothing, so _step can report why."""
        self._skip_reason = reason

    @classmethod
    def _row_count(cls, model, n):
        meta = model._meta
        singular, plural = cls._ROW_LABELS.get(
            str(meta.verbose_name), (str(meta.verbose_name), str(meta.verbose_name_plural))
        )
        return f"{n} {singular if n == 1 else plural}"

    def handle(self, *args, **options):
        self.stdout.write("Seeding RBAC…")
        if options["flush_db"]:
            self.stdout.write("Flushing the database…")
            call_command("flush", "--noinput")

        Application = get_application_model()
        with transaction.atomic():
            self.reset_sequences()
            self._step("superuser", (JheUser, Practitioner), self.generate_superuser)
            self._step("settings", (JheSetting,), self.seed_jhe_settings)
            self._step("codeable concepts", (CodeableConcept,), self.seed_codeable_concepts)
            self._step("data sources", (DataSource, DataSourceSupportedScope), self.seed_data_sources)
            self._step("clients", (Application, JheClient, ClientDataSource), self.seed_clients)
            root_organization = self._step("root organization", (Organization,), self.create_root_organization)
            self._step(
                "example institute",
                (
                    Organization,
                    JheUser,
                    Patient,
                    Study,
                    Observation,
                    FhirSource,
                    FhirAuxResource,
                    EhrBrand,
                    EhrBrandLocation,
                ),
                self.seed_example_institute,
                root_organization,
            )
            self._step(
                "health system",
                (Organization, JheUser, Patient, Study, Observation),
                self.seed_health_system,
                root_organization,
            )
            self._step("admin UI client", (Application,), self.seed_oauth_application)
            self._step("MCP broker client", (Application,), self.seed_mcp_broker_application)
            self._step("SMART EHR launch client", (Application,), self.seed_sof_ehr_launch_application)

        if options.get("with_rich_demo"):
            # Its own summary line (the CGM/wearable split) is suppressed here so the seed
            # output stays uniform; run `manage.py seed_rich_demo` directly to see it.
            self._step(
                "rich demo (CGM + Oura)",
                (Patient, Observation),
                call_command,
                "seed_rich_demo",
                stdout=io.StringIO(),
            )

        self.stdout.write(self.style.SUCCESS("Seeding complete."))

    def seed_jhe_settings(self):
        invite_code = "jhe"

        jhe_settings = [
            # Honor the SITE_URL env so the seeded host matches where JHE is actually
            # served (e.g. http://localhost:8001 for the EHR Patient Portal client). Defaults to
            # http://localhost:8000 when SITE_URL is unset, preserving prior behavior.
            # Invitation links embed this host, so a mismatch sends redemption to the
            # wrong server.
            ("site.url", "string", settings.SITE_URL),
            ("site.ui.title", "string", "JupyterHealth Exchange"),
            ("site.ui.logo", "string", ""),  # static path of a deployment's own logo for patient-facing pages
            ("site.ui.theme_css", "string", ""),  # static path of a stylesheet overriding the --pf-* tokens/fonts
            ("site.time_zone", "string", "America/Los_Angeles"),
            ("site.registration_invite_code", "string", invite_code),
            ("auth.default_orgs", "string", ""),  # "20001:viewer;20002:member"
            # IdP config is a SocialApp row (Django admin); these only gate the
            # login button and restrict signup domains.
            ("auth.sso.saml2", "int", 0),
            ("auth.sso.valid_domains", "string", ""),
            ("auth.patient.invitation_expiration_days", "int", 7),
            ("auth.patient.invitation_redemption_window_hours", "int", 12),
            # OAuth client_ids whose auth-code login uses the email one-time-code
            # flow (/accounts/login-otp/) instead of the password form.
            ("auth.patient_access_clients", "json", []),
            # SMART on FHIR EHR-launch token exchange (/o/token-exchange).
            # trusted_issuers: EHR OIDC issuers (id_token `iss`) whose id_tokens we
            # accept; JWKS is discovered from each. trusted_audience: the SMART app's
            # client_id registered at the EHR (the id_token `aud`).
            # NB: the issuer is the id_token's literal `iss` — per standard OIDC this
            # is the EHR's OAuth server, NOT its FHIR base (verified live against the
            # Epic sandbox 2026-07-12; MedPlum is the outlier whose iss is the FHIR
            # base URL with a trailing slash). Docs: https://jupyterhealth.github.io/software-documentation/jhe/provider-ehr-launch
            (
                "auth.sof.trusted_issuers",
                "json",
                ["https://fhir.epic.com/interconnect-fhir-oauth/oauth2"],
            ),
            ("auth.sof.trusted_audience", "string", "77849e74-8e2a-4c2f-826c-bdbef6da3357"),
            # Open Wearables polling pipeline (see ow_poll management command).
            ("module.ow", "bool", False),
            ("ow.sync_in_progress", "string", ""),
            # Open Wearables connection config (moved from env). api_key is a secret —
            # left blank in the seed for an operator to fill in; api_url defaults to the
            # local OW dev server. Consumed at runtime via get_setting() in ow.py /
            # patient.py / ow_poll.
            ("ow.api_url", "string", "http://localhost:8001"),
            ("ow.api_key", "string", ""),
        ]
        for key, value_type, value in jhe_settings:
            setting, _ = JheSetting.objects.update_or_create(
                key=key,
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
                "core_fhirsource_id_seq",
            ]

            for seq in seqs:
                cursor.execute(f"ALTER SEQUENCE {seq} RESTART WITH %s;", [restart_with])
                restart_with = restart_with + 10000

            cursor.execute("ALTER SEQUENCE core_patientinvitation_id_seq RESTART WITH 101;")

    @staticmethod
    def seed_codeable_concepts():
        # OMH = https://w3id.org/openmhealth, IEEE = https://w3id.org/ieee1752. IEEE 1752 is the
        # balloted form of an OMH schema and JHE treats the two coding systems interchangeably,
        # so a measure can be seeded under either -- or both, where the schema versions differ.
        # Every label therefore names its namespace: the two are indistinguishable otherwise,
        # and the admin renders a CodeableConcept by its text alone.
        omh = "https://w3id.org/openmhealth"
        ieee = "https://w3id.org/ieee1752"
        codes = [
            (omh, "omh:blood-glucose:4.0", "Blood glucose (OMH)"),
            (omh, "omh:blood-pressure:4.0", "Blood pressure (OMH)"),
            (omh, "omh:body-temperature:4.0", "Body temperature (OMH)"),
            (omh, "omh:heart-rate:2.0", "Heart Rate (OMH)"),
            (omh, "omh:oxygen-saturation:2.0", "Oxygen saturation (OMH)"),
            (omh, "omh:respiratory-rate:2.0", "Respiratory rate (OMH)"),
            (omh, "omh:rr-interval:1.0", "RR Interval (OMH)"),
            (omh, "omh:body-weight:3.0", "Body weight (OMH)"),
            (omh, "omh:forced-vital-capacity:1.0", "Forced vital capacity (OMH)"),
            (omh, "omh:forced-expiratory-volume-1-second:1.0", "Forced expiratory volume 1 second (OMH)"),
            (omh, "omh:physical-activity:1.2", "Physical activity (OMH)"),
            (omh, "omh:step-count:3.0", "Step count (OMH)"),
            (omh, "omh:sleep-episode:1.1", "Sleep episode (OMH)"),
            (omh, "omh:sleep-duration:2.0", "Sleep duration (OMH)"),
            (omh, "omh:total-sleep-time:1.0", "Total sleep time (OMH)"),
            (ieee, "ieee:sleep-stage-summary:1.0", "Sleep stage summary (IEEE)"),
            (ieee, "ieee:physical-activity:1.0", "Physical activity (IEEE)"),
            (ieee, "ieee:sleep-episode:1.0", "Sleep episode (IEEE)"),
            (ieee, "ieee:time-in-bed:1.0", "Time in bed (IEEE)"),
            # Not OMH/IEEE data points: FHIR resource types, so they carry no suffix. "*" is the
            # wildcard -- a data source scoped to it supplies FHIR resources of any type, which is
            # what an EHR patient-portal pull does (17 types today, all stored as aux rows).
            ("http://hl7.org/fhir/resource-types", "QuestionnaireResponse", "FHIR QuestionnaireResponse"),
            ("http://hl7.org/fhir/resource-types", "*", "Clinical records"),
        ]
        # bulk create thing
        for system, code, text in codes:
            CodeableConcept.objects.update_or_create(
                coding_system=system,
                coding_code=code,
                defaults={"text": text},
            )

    def seed_data_sources(self):
        data_sources = [
            ("CareX", "personal_device", ["omh:blood-pressure:4.0", "omh:heart-rate:2.0"]),
            ("Dexcom Stelo", "personal_device", ["omh:blood-glucose:4.0"]),
            ("iHealth", "personal_device", ["omh:body-temperature:4.0", "omh:heart-rate:2.0"]),
            ("Oura", "personal_device", ["omh:heart-rate:2.0", "ieee:sleep-episode:1.0"]),
            ("Questionnaire", "patient_app", ["QuestionnaireResponse"]),
            # Source for clinical records pulled from an EHR's patient-access FHIR API
            # (#489). Shares its name with the client of the same name -- one product, one
            # name, the way CareX does. Its supported scope is the FHIR wildcard rather than
            # any OMH measure: the pull spans every mapped resource type and lands in the
            # auxiliary FHIR store, not the OMH Observation model.
            ("EHR Patient Portal", "patient_app", ["*"]),
        ]
        for name, type, scope_codes in data_sources:
            # `type` is a default, not a lookup: matching on (name, type) would make a retype
            # create a second row rather than update the existing one, silently orphaning the
            # FhirSource/StudyDataSource rows that point at the original by FK.
            ds, _ = DataSource.objects.update_or_create(name=name, defaults={"type": type})
            for coding_code in scope_codes:
                scope = CodeableConcept.objects.get(coding_code=coding_code)
                DataSourceSupportedScope.objects.get_or_create(data_source=ds, scope_code=scope)

    def seed_clients(self):
        _alphabet = string.ascii_letters + string.digits

        def _generate_client_id(length=40):
            return "".join(secrets.choice(_alphabet) for _ in range(length))

        Application = get_application_model()

        clients = [
            {
                "name": "CareX",
                "invitation_url": "https://carex.ai/invitation/CODE",
                "data_sources": ["CareX", "Questionnaire"],
            },
            {
                "name": "CommonHealth",
                "invitation_url": "https://commonhealth.tcp.org?invitation=CODE",
                "data_sources": ["Dexcom Stelo", "iHealth"],
            },
            {
                # Open Wearables patient client, served by JHE itself at /clients/ow/launch
                # (core/views/common.py::ow_launch). The OW polling pipeline (ow_poll) pulls
                # Oura data for patients who have linked an OW account.
                "name": "Open Wearables",
                "invitation_url": "http://localhost:8001/clients/ow/launch?code=CODE",
                "data_sources": ["Oura"],
            },
            {
                # SMART on FHIR patient EHR-records client (issue #489). Served on JHE's
                # normal :8001, which the Epic app has registered as a redirect.
                #
                # The row, its DataSource and its URL path are all "EHR Patient Portal".
                # The earlier name "Patient Access" collided with the unrelated
                # `auth.patient_access_clients` login-mode setting (#604), which is a login
                # mode for a list of clients, not this client.
                #
                # NOTE: this app's redirect_uris is the JHE OAuth callback (the default
                # /auth/callback, used by the invitation -> /o/token exchange), NOT the
                # Epic callback. The Epic callback URLs
                # (http://localhost:8001/clients/ehr-patient-portal/callback and the
                # jhe.fly.dev equivalent) are registered separately on the Epic app at
                # fhir.epic.com -- they must be updated there in step with this path.
                "name": "EHR Patient Portal",
                "invitation_url": "http://localhost:8001/clients/ehr-patient-portal/?code=CODE",
                # The client and its DataSource are one and the same product (as with CareX),
                # so they share a name and are linked here. That ClientDataSource row is the
                # only link: the connect page reads the data source id through it, never by
                # looking a name up at request time.
                "data_sources": ["EHR Patient Portal"],
                # No iss here: the hospital the patient picks supplies it (EhrBrand.fhir_base_url).
                "aux_data": {
                    # Non-production client id of the Epic app "JupyterHealth Exchange -
                    # USCDI v3" (appId 55446), the app that has the localhost:8001 +
                    # jhe.fly.dev /clients/ehr-patient-portal/callback redirect URIs registered.
                    # (Same Epic sandbox app/client id used in the Phase 1 POC.)
                    "client_id": "77849e74-8e2a-4c2f-826c-bdbef6da3357",
                    # One read scope per EHR_PATIENT_PORTAL_PULLS type (client-ehr-patient-portal.js);
                    # the Epic app registration already covers all of these APIs.
                    "scopes": (
                        "openid profile launch/patient"
                        " patient/Patient.read patient/Observation.read patient/Condition.read"
                        " patient/MedicationRequest.read patient/MedicationDispense.read"
                        " patient/AllergyIntolerance.read patient/Immunization.read"
                        " patient/Procedure.read patient/DiagnosticReport.read"
                        " patient/DocumentReference.read patient/Encounter.read"
                        " patient/CarePlan.read patient/CareTeam.read patient/Goal.read"
                        " patient/ServiceRequest.read"
                        " patient/Device.read"
                        " patient/QuestionnaireResponse.read"
                    ),
                },
            },
        ]

        for client in clients:
            app, created = Application.objects.get_or_create(
                name=client["name"],
                defaults={
                    "client_id": _generate_client_id(),
                    "client_type": Application.CLIENT_PUBLIC,
                    "authorization_grant_type": Application.GRANT_AUTHORIZATION_CODE,
                    "skip_authorization": True,
                    "redirect_uris": client.get("redirect_uris", settings.SITE_URL + settings.OAUTH2_CALLBACK_PATH),
                    "algorithm": "RS256",
                },
            )
            # Create the JheClient explicitly (there is no post_save signal) and keep the
            # seed-managed aux_data keys current on every run -- not just on creation. That
            # create-only guard is why widening the Patient Access scopes needed migration
            # 0043: the deployed row could not pick a code-owned value up from seed at all.
            jhe_client, _ = JheClient.objects.get_or_create(application=app)
            if created:
                # Deployment-specific: a deployment's real invitation host is not the seeded
                # localhost URL, so this is written once and never overwritten.
                jhe_client.invitation_url = client["invitation_url"]
            if client.get("aux_data") is not None:
                jhe_client.aux_data = _merged_aux_data(jhe_client.aux_data, client["aux_data"])
            jhe_client.save()

            for ds_name in client["data_sources"]:
                ds = DataSource.objects.get(name=ds_name)
                ClientDataSource.objects.get_or_create(client=app, data_source=ds)

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
        lifespan_lab = Organization.objects.create(
            name="Lifespan Lab", type="laboratory", part_of=saturn_school_of_data_science
        )

        manager_mary = self.create_user_with_profile("manager_mary@example.com")

        manager_links = [
            PractitionerOrganization(practitioner=manager_mary, organization=org, role="manager")
            for org in [planetary_research_institute, saturn_school_of_data_science, lifespan_lab]
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
        lifespan_study_sleep_bp = Study.objects.create(
            name="Lifespan Study on Sleep & BP",
            description="Sleep & Blood Pressure",
            organization=lifespan_lab,
        )

        code_omh_bp = CodeableConcept.objects.get(coding_code="omh:blood-pressure:4.0")
        code_omh_hr = CodeableConcept.objects.get(coding_code="omh:heart-rate:2.0")
        code_ieee_sleep = CodeableConcept.objects.get(coding_code=SLEEP_EPISODE_CODE)
        code_fhir_qr = CodeableConcept.objects.get(coding_code="QuestionnaireResponse")
        code_fhir_star = CodeableConcept.objects.get(
            coding_system="http://hl7.org/fhir/resource-types", coding_code="*"
        )

        StudyScopeRequest.objects.create(study=lifespan_study_bp_hr, scope_code=code_omh_bp)
        StudyScopeRequest.objects.create(study=lifespan_study_bp_hr, scope_code=code_omh_hr)
        StudyScopeRequest.objects.create(study=lifespan_study_bp_hr, scope_code=code_fhir_qr)
        StudyScopeRequest.objects.create(study=lifespan_study_sleep_bp, scope_code=code_omh_bp)
        StudyScopeRequest.objects.create(study=lifespan_study_sleep_bp, scope_code=code_ieee_sleep)
        StudyScopeRequest.objects.create(study=lifespan_study_sleep_bp, scope_code=code_fhir_qr)

        carex_ds = DataSource.objects.get(name="CareX")
        questionnaire_ds = DataSource.objects.get(name="Questionnaire")
        carex_client = get_application_model().objects.get(name="CareX")
        for study in [lifespan_study_bp_hr, lifespan_study_sleep_bp]:
            StudyDataSource.objects.create(study=study, data_source=carex_ds)
            StudyDataSource.objects.create(study=study, data_source=questionnaire_ds)
            StudyClient.objects.create(study=study, client=carex_client)

        # The sleep study collects its sleep episodes from Oura via the Open Wearables client.
        oura_ds = DataSource.objects.get(name="Oura")
        ow_client = get_application_model().objects.get(name="Open Wearables")
        StudyDataSource.objects.create(study=lifespan_study_sleep_bp, data_source=oura_ds)
        StudyClient.objects.create(study=lifespan_study_sleep_bp, client=ow_client)

        # Wire the EHR Patient Portal (Epic SMART) client to Peter's BP & HR study so it
        # shows up as an invitable client for him (consolidated_clients gates on StudyClient).
        # This makes the EHR patient-portal end-to-end flow (#489) testable out of the box.
        ehr_patient_portal_client = get_application_model().objects.get(name="EHR Patient Portal")
        StudyClient.objects.create(study=lifespan_study_bp_hr, client=ehr_patient_portal_client)

        # Also requests clinical records via the EHR Patient Portal; left pending so the demo starts unconsented.
        ehr_patient_portal_ds = DataSource.objects.get(name="EHR Patient Portal")
        StudyDataSource.objects.create(study=lifespan_study_bp_hr, data_source=ehr_patient_portal_ds)
        StudyScopeRequest.objects.create(study=lifespan_study_bp_hr, scope_code=code_fhir_star)

        # Seed EHR hospital brands so the EHR Patient Portal hospital picker (#489) has data to
        # search out of the box. Uses the curated sample bundle; production imports the
        # full Epic file via `manage.py import_ehr_brands --file <download>`.
        call_command("import_ehr_brands", stdout=io.StringIO())

        ll_patient_pete = self.create_user_with_profile("ll_patient_peter@example.com", user_type="patient")
        ll_patient_pete.organizations.add(lifespan_lab)
        # Example multi-identifier demo data (see PR #409 / ghi-352).
        PatientIdentifier.objects.create(
            patient=ll_patient_pete, system="https://openwearables.io/ns/patient-id", value="abc123"
        )
        PatientIdentifier.objects.create(patient=ll_patient_pete, system="urn:oid:1.2.3.4", value="E1234")
        ll_patient_pamela = self.create_user_with_profile("ll_patient_pamela@example.com", user_type="patient")
        ll_patient_pamela.organizations.add(lifespan_lab)

        sp_peter_bp_hr = StudyPatient.objects.create(study=lifespan_study_bp_hr, patient=ll_patient_pete)
        sp_peter_sleep_bp = StudyPatient.objects.create(study=lifespan_study_sleep_bp, patient=ll_patient_pete)
        sp_pamela_bp_hr = StudyPatient.objects.create(study=lifespan_study_bp_hr, patient=ll_patient_pamela)
        sp_pamela_sleep_bp = StudyPatient.objects.create(study=lifespan_study_sleep_bp, patient=ll_patient_pamela)

        now = timezone.now()
        StudyPatientScopeConsent.objects.create(
            study_patient=sp_peter_bp_hr,
            scope_code=code_omh_bp,
            consented=True,
            consented_time=now,
        )
        StudyPatientScopeConsent.objects.create(
            study_patient=sp_peter_bp_hr,
            scope_code=code_omh_hr,
            consented=True,
            consented_time=now,
        )
        StudyPatientScopeConsent.objects.create(
            study_patient=sp_peter_bp_hr,
            scope_code=code_fhir_qr,
            consented=True,
            consented_time=now,
        )

        for sp, codes in [
            (sp_pamela_bp_hr, [code_omh_bp, code_omh_hr, code_fhir_qr]),
            (sp_pamela_sleep_bp, [code_omh_bp, code_ieee_sleep, code_fhir_qr]),
            (sp_peter_sleep_bp, [code_ieee_sleep]),
        ]:
            for code in codes:
                StudyPatientScopeConsent.objects.create(
                    study_patient=sp,
                    scope_code=code,
                    consented=True,
                    consented_time=now,
                )

        planetary_research_institute_study_patients = [
            sp_peter_bp_hr,
            sp_peter_sleep_bp,
            sp_pamela_bp_hr,
            sp_pamela_sleep_bp,
        ]
        for consent in (
            StudyPatientScopeConsent.objects.filter(
                consented=True, study_patient__in=planetary_research_institute_study_patients
            )
            .exclude(scope_code__coding_system__startswith="http://hl7.org/fhir/")
            # Seeded explicitly below: generate_observation_value_attachment_data has no
            # example for this code and its fallback body fails schema validation.
            .exclude(scope_code__coding_code=SLEEP_EPISODE_CODE)
        ):
            scope_code = consent.scope_code
            Observation.objects.create(
                subject_patient=consent.study_patient.patient,
                codeable_concept=scope_code,
                omh_data=generate_observation_value_attachment_data(consent.scope_code.coding_code),
            )

        # A couple of nights each, from the Oura data source the sleep study collects through.
        for patient, nights in [
            (ll_patient_pete, [(2, 7.5, 1), (1, 6.25, 3)]),
            (ll_patient_pamela, [(2, 8.0, 0), (1, 5.75, 2)]),
        ]:
            for days_ago, hours_asleep, awakenings in nights:
                bedtime = (now - timedelta(days=days_ago)).replace(hour=23, minute=0, second=0, microsecond=0)
                Observation.objects.create(
                    subject_patient=patient,
                    codeable_concept=code_ieee_sleep,
                    data_source=oura_ds,
                    omh_data=sleep_episode_data_point(bedtime, hours_asleep, awakenings),
                )

        peter_fhir_source = FhirSource.objects.create(
            patient=ll_patient_pete,
            data_source=questionnaire_ds,
            label="Records from Halo Health System",
        )
        pamela_fhir_source = FhirSource.objects.create(
            patient=ll_patient_pamela,
            data_source=questionnaire_ds,
            label="Chart from Comet Clinic",
        )

        peter_patient_fhir_id = "a1b2c3d4e5f6"
        pamela_patient_fhir_id = "b2c3d4e5f6a1"

        peter_qr_resources = [
            ("c3d4e5f6a1b2", "2025-10-01", 42, 18, 65),
            ("d4e5f6a1b2c3", "2025-10-08", 55, 30, 72),
            ("e5f6a1b2c3d4", "2025-10-15", 38, 22, 48),
        ]
        pamela_qr_resources = [
            ("f6a1b2c3d4e5", "2025-10-03", 60, 45, 80),
            ("a2b3c4d5e6f7", "2025-10-10", 50, 35, 70),
            ("b3c4d5e6f7a2", "2025-10-17", 25, 10, 40),
            ("c4d5e6f7a2b3", "2025-10-24", 70, 55, 90),
        ]

        for fhir_source, patient_fhir_id, resources in [
            (peter_fhir_source, peter_patient_fhir_id, peter_qr_resources),
            (pamela_fhir_source, pamela_patient_fhir_id, pamela_qr_resources),
        ]:
            for fhir_resource_id, authored, cough, dyspnea, fatigue in resources:
                fhir_data = apply_jhe_extensions(
                    {
                        "resourceType": "QuestionnaireResponse",
                        "status": "completed",
                        "questionnaire": "Questionnaire/weekly-symptom-severity-vas",
                        "subject": {"reference": f"Patient/{patient_fhir_id}"},
                        "authored": authored,
                        "item": [
                            {"linkId": "cough-severity", "answer": [{"valueInteger": cough}]},
                            {"linkId": "dyspnea-severity", "answer": [{"valueInteger": dyspnea}]},
                            {"linkId": "fatigue-severity", "answer": [{"valueInteger": fatigue}]},
                        ],
                    },
                    fhir_source,
                )
                FhirAuxResource.objects.create(
                    fhir_source=fhir_source,
                    resource_type="QuestionnaireResponse",
                    patient_fhir_id=patient_fhir_id,
                    fhir_resource_id=fhir_resource_id,
                    fhir_data=fhir_data,
                )

        for practitioner in [manager_mary, member_megan, viewer_victor]:
            practitioner.save_setting("current_organization_id", lifespan_lab.id)
            practitioner.save_setting("current_study_id", lifespan_study_bp_hr.id)
            # Open the FHIR browser on the QuestionnaireResponses seeded above, so the page has
            # rows on a first visit instead of an empty JHE-native store. These are the same
            # settings the browser writes back via X-JHE-Remember-View (FHIRResourceView.
            # _remember_search). The source value is the "External" option: JHE_FHIR_SOURCE_BASE
            # with the trailing slash the `_source:below` prefix search uses -- the browser
            # normalizes away anything that is not exactly that string. The JHE Patient ID box is
            # deliberately left unset: an absent current_fhir_jhe_patient_id is a cleared filter.
            practitioner.save_setting("current_fhir_resource", "QuestionnaireResponse")
            practitioner.save_setting("current_fhir_source", f"{JHE_FHIR_SOURCE_BASE}/")

    def seed_health_system(self, root_organization):
        nhs = Organization.objects.create(
            name="Neptune Health System",
            type="prov",
            part_of=root_organization,
        )
        department_of_medicine = Organization.objects.create(name="Department of Medicine", type="dept", part_of=nhs)
        cardiology_division = Organization.objects.create(
            name="Cardiology Division", type="dept", part_of=department_of_medicine
        )
        neptunian_pulse_lab = Organization.objects.create(
            name="Neptunian Pulse Lab", type="laboratory", part_of=cardiology_division
        )
        cosmic_cardio_lab = Organization.objects.create(
            name="Cosmic Cardio Lab", type="laboratory", part_of=cardiology_division
        )

        manager_mark = self.create_user_with_profile("manager_mark@example.com", user_type="practitioner")
        practitioner_org_links = [
            PractitionerOrganization(practitioner=manager_mark, organization=org, role="manager")
            for org in [nhs, department_of_medicine, cardiology_division, neptunian_pulse_lab]
        ]
        PractitionerOrganization.objects.bulk_create(practitioner_org_links)

        three_org_tom = JheUser.objects.get(email="three_org_tom@example.com").practitioner
        PractitionerOrganization.objects.create(
            practitioner=three_org_tom, organization=neptunian_pulse_lab, role="member"
        )
        PractitionerOrganization.objects.create(
            practitioner=three_org_tom, organization=cosmic_cardio_lab, role="manager"
        )

        code_omh_bg = CodeableConcept.objects.get(coding_code="omh:blood-glucose:4.0")
        code_omh_bt = CodeableConcept.objects.get(coding_code="omh:body-temperature:4.0")
        code_omh_o2 = CodeableConcept.objects.get(coding_code="omh:oxygen-saturation:2.0")

        cardio_bgl = Study.objects.create(
            name="Cardiology Div Study on BGL",
            description="Blood Glucose",
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

        StudyScopeRequest.objects.create(study=cardio_bgl, scope_code=code_omh_bg)
        StudyScopeRequest.objects.create(study=neptunian_pulse_lab_bt, scope_code=code_omh_bt)
        StudyScopeRequest.objects.create(study=cosmic_cardio_lab_o2, scope_code=code_omh_o2)

        commonhealth_client = get_application_model().objects.get(name="CommonHealth")
        StudyDataSource.objects.create(study=neptunian_pulse_lab_bt, data_source=DataSource.objects.get(name="iHealth"))
        StudyClient.objects.create(study=neptunian_pulse_lab_bt, client=commonhealth_client)
        StudyDataSource.objects.create(study=cardio_bgl, data_source=DataSource.objects.get(name="Dexcom Stelo"))
        StudyClient.objects.create(study=cardio_bgl, client=commonhealth_client)

        neptunian_pulse_lab_patient_percy = self.create_user_with_profile(
            "npl_patient_percy@example.com", user_type="patient"
        )
        neptunian_pulse_lab_patient_percy.organizations.add(neptunian_pulse_lab)
        neptunian_pulse_lab_ccl_patient_paul = self.create_user_with_profile(
            "ccl_patient_paul@example.com", user_type="patient"
        )
        neptunian_pulse_lab_ccl_patient_paul.organizations.add(cosmic_cardio_lab)
        ccl_cardio_patient_pat = self.create_user_with_profile(
            "ccl_cardio_patient_pat@example.com", user_type="patient"
        )
        ccl_cardio_patient_pat.organizations.add(cardiology_division, cosmic_cardio_lab)

        sp_percy_bt = StudyPatient.objects.create(
            study=neptunian_pulse_lab_bt, patient=neptunian_pulse_lab_patient_percy
        )
        sp_paul_o2 = StudyPatient.objects.create(
            study=cosmic_cardio_lab_o2, patient=neptunian_pulse_lab_ccl_patient_paul
        )
        sp_pat_bg = StudyPatient.objects.create(study=cardio_bgl, patient=ccl_cardio_patient_pat)
        sp_pat_o2 = StudyPatient.objects.create(study=cosmic_cardio_lab_o2, patient=ccl_cardio_patient_pat)

        now = timezone.now()

        StudyPatientScopeConsent.objects.create(
            study_patient=sp_percy_bt,
            scope_code=code_omh_bt,
            consented=True,
            consented_time=now,
        )
        StudyPatientScopeConsent.objects.create(
            study_patient=sp_paul_o2,
            scope_code=code_omh_o2,
            consented=True,
            consented_time=now,
        )
        StudyPatientScopeConsent.objects.create(
            study_patient=sp_pat_bg,
            scope_code=code_omh_bg,
            consented=True,
            consented_time=now,
        )
        StudyPatientScopeConsent.objects.create(
            study_patient=sp_pat_o2,
            scope_code=code_omh_o2,
            consented=True,
            consented_time=now,
        )

        med_study_patients = [sp_percy_bt, sp_paul_o2, sp_pat_bg, sp_pat_o2]
        for consent in StudyPatientScopeConsent.objects.filter(
            consented=True, study_patient__in=med_study_patients
        ).exclude(scope_code__coding_system__startswith="http://hl7.org/fhir/"):
            scope_code = consent.scope_code
            Observation.objects.create(
                subject_patient=consent.study_patient.patient,
                codeable_concept=scope_code,
                omh_data=generate_observation_value_attachment_data(consent.scope_code.coding_code),
            )

        manager_mark.save_setting("current_organization_id", neptunian_pulse_lab.id)
        manager_mark.save_setting("current_study_id", neptunian_pulse_lab_bt.id)
        three_org_tom.save_setting("current_organization_id", cosmic_cardio_lab.id)
        three_org_tom.save_setting("current_study_id", cosmic_cardio_lab_o2.id)

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

    def seed_mcp_broker_application(self):
        """Seed the JHE MCP Server broker as a confidential OAuth client.

        Credentials come from the environment (mirroring how the broker reads
        its own JHE_CLIENT_ID / JHE_CLIENT_SECRET), so no secret is committed to
        the repo. Skipped when they are not set — e.g. local or CI seeds that
        don't run the broker. The broker is the one client that needs
        ``skip_authorization=True`` recorded in code so a reseed reproduces the
        no-consent-prompt behavior.
        """
        client_id = os.environ.get("MCP_OAUTH_CLIENT_ID")
        client_secret = os.environ.get("MCP_OAUTH_CLIENT_SECRET")
        if not (client_id and client_secret):
            self._skip("MCP_OAUTH_CLIENT_ID/SECRET not set")
            return
        redirect_uri = os.environ.get("MCP_OAUTH_REDIRECT_URI", "https://jhe-mcp.fly.dev/oauth/callback")
        # django-oauth-toolkit hashes the plaintext on save (hash_client_secret
        # defaults to True); the broker authenticates by sending the matching
        # plaintext from its own env. Set it explicitly to mirror
        # seed_oauth_application and make the behavior obvious.
        get_application_model().objects.update_or_create(
            name="JHE MCP Server",
            defaults={
                "client_id": client_id,
                "client_secret": client_secret,
                "hash_client_secret": True,
                "client_type": "confidential",
                "authorization_grant_type": "authorization-code",
                "redirect_uris": redirect_uri,
                "skip_authorization": True,
                "algorithm": "RS256",
                "user_id": None,
            },
        )

    def seed_sof_ehr_launch_application(self):
        """Seed the "SoF EHR Launch" confidential OAuth client used by /o/token-exchange.

        An external SMART on FHIR app authenticates to the token-exchange endpoint
        with this client's id + secret, then exchanges an EHR-issued id_token for a
        JHE user-bound access token linked to this Application. The grant type is
        recorded as ``client-credentials`` because the app authenticates directly with
        id+secret at the token endpoint (no browser redirect); the issued token is
        nonetheless user-bound (docs: https://jupyterhealth.github.io/software-documentation/jhe/provider-ehr-launch).

        The seeded credentials are DEV-ONLY placeholders — rotate them for any real
        deployment (or override via SOF_EHR_LAUNCH_CLIENT_ID / _SECRET). DOT hashes the
        plaintext secret on save (hash_client_secret=True); the app sends the matching
        plaintext.
        """
        client_id = os.environ.get("SOF_EHR_LAUNCH_CLIENT_ID", "sof-ehr-launch")
        client_secret = os.environ.get("SOF_EHR_LAUNCH_CLIENT_SECRET", "sof-ehr-launch-dev-secret")
        get_application_model().objects.update_or_create(
            name="SoF EHR Launch",
            defaults={
                "client_id": client_id,
                "client_secret": client_secret,
                "hash_client_secret": True,
                "client_type": "confidential",
                "authorization_grant_type": "client-credentials",
                "redirect_uris": "",
                "skip_authorization": True,
                "algorithm": "RS256",
                "user_id": None,
            },
        )

    def create_user_with_profile(self, email, user_type="practitioner", password="Jhe1234!"):
        user = JheUser.objects.create_user(
            email=email,
            password=password or get_random_string(length=16),
            first_name=email.split("@")[0].replace("_", " ").title().replace(" ", ""),
            last_name=fake.last_name(),
            user_type=user_type,
        )
        user.identifier = f"fhir-{str(user.id)[-1] * 3}"
        user.save()
        if user_type == "practitioner":
            practitioner = user.practitioner_profile
            practitioner.birth_date = fake.date_of_birth(minimum_age=25, maximum_age=45)
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
