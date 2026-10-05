import base64
import json

import humps
from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.db.models import QuerySet
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from fhir.resources.observation import Observation as FHIRObservation
from fhir.resources.patient import Patient as FHIRPatient
from oauth2_provider.models import get_application_model

from core.models import (
    CodeableConcept,
    DataSource,
    DataSourceSupportedScope,
    JheUser,
    Observation,
    ObservationIdentifier,
    Organization,
    Patient,
    PatientIdentifier,
    PatientOrganization,
    Practitioner,
    PractitionerOrganization,
    Study,
    StudyDataSource,
    StudyPatient,
    StudyPatientScopeConsent,
    StudyScopeRequest,
)
from core.serializers import FHIRObservationSerializer, FHIRPatientSerializer
from core.utils import generate_observation_value_attachment_data


# -----------------------------------------------------
# JheUser Methods
# -----------------------------------------------------
class JheUserMethodTests(TestCase):
    def setUp(self):
        self.user = JheUser.objects.create_user(email="test@example.com", password="password")

        # Create an OAuth2 application for testing
        Application = get_application_model()
        self.application = Application.objects.create(
            name="Test Application",
            user=self.user,
            client_type=Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris="http://example.com/redirect",
        )

    def test_create_superuser(self):
        superuser = JheUser.objects.create_superuser(email="admin@example.com", password="adminpass")
        self.assertTrue(superuser.is_staff)
        self.assertTrue(superuser.is_superuser)

    def test_send_email_verification(self):
        # Clear the mail outbox first.
        mail.outbox = []
        self.user.send_email_verificaion()  # method name as in your model (check spelling)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("JHE E-mail Verification", mail.outbox[0].subject)

    # def test_create_authorization_code(self):
    #     # TODO: fix - JheUser has no create_authorization_code method
    #     redirect_uri = "http://example.com/redirect"
    #     code_instance = self.user.create_authorization_code(self.application.id, redirect_uri)
    #     self.assertIsNotNone(code_instance)
    #     # redirect_uri is now built from get_setting("site.url") + OAUTH2_CALLBACK_PATH
    #     self.assertIn("/auth/callback", code_instance.redirect_uri)
    #     self.assertEqual(code_instance.scope, "openid email")
    #     self.assertTrue(bool(code_instance.code))  # Code should not be empty


# -----------------------------------------------------
# Organization Methods
# -----------------------------------------------------
class OrganizationMethodTests(TestCase):
    def setUp(self):
        self.parent_org = Organization.objects.create(name="Parent Org", type="prov")
        self.child_org = Organization.objects.create(name="Child Org", type="dept", part_of=self.parent_org)

        self.user = JheUser.objects.create_user(
            email="user@example.com",
            password="password",
            user_type="practitioner",
        )

        self.practitioner = Practitioner.objects.get(jhe_user=self.user)

    def test_get_children(self):
        children = list(Organization.get_children(self.parent_org.id))
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0], self.child_org)

    def test_collect_children(self):
        # Reset the parent's children list and populate it recursively.
        self.parent_org.children = []
        Organization.collect_children(self.parent_org)
        self.assertIn(self.child_org, self.parent_org.children)

    def test_for_practitioner(self):
        PractitionerOrganization.objects.create(practitioner=self.practitioner, organization=self.parent_org)

        orgs = list(Organization.for_practitioner(self.user.id))
        org_ids = [o.id for o in orgs]
        self.assertIn(self.parent_org.id, org_ids)

    def test_for_practitioner_excludes_unrelated_org(self):
        other_org = Organization.objects.create(name="Other Org", type="prov")
        PractitionerOrganization.objects.create(practitioner=self.practitioner, organization=self.parent_org)

        orgs = list(Organization.for_practitioner(self.user.id))
        org_ids = [o.id for o in orgs]
        self.assertNotIn(other_org.id, org_ids)

    def test_for_practitioner_excludes_unauthorized_practitioner(self):
        other_user = JheUser.objects.create_user(
            email="other@example.com", password="password", user_type="practitioner"
        )
        PractitionerOrganization.objects.create(practitioner=self.practitioner, organization=self.parent_org)

        orgs = list(Organization.for_practitioner(other_user.id))
        org_ids = [o.id for o in orgs]
        self.assertNotIn(self.parent_org.id, org_ids)

    def test_for_patient(self):
        patient_user = JheUser.objects.create_user(email="patient@example.com", password="password")

        patient = Patient.objects.create(
            jhe_user=patient_user,
            name_family="Johnson",
            name_given="John",
            birth_date="1980-01-01",
            telecom_phone="1234567890",
        )

        PatientOrganization.objects.create(patient=patient, organization=self.parent_org)

        orgs = list(Organization.for_patient(patient_user.id))
        self.assertGreaterEqual(len(orgs), 1)
        self.assertTrue(any(org.id == self.parent_org.id for org in orgs))

    def test_for_patient_excludes_unrelated_org(self):
        other_org = Organization.objects.create(name="Other Org", type="prov")
        patient_user = JheUser.objects.create_user(email="patient2@example.com", password="password")
        patient = Patient.objects.create(
            jhe_user=patient_user,
            name_family="Smith",
            name_given="Jane",
            birth_date="1990-05-05",
            telecom_phone="9998887777",
        )
        PatientOrganization.objects.create(patient=patient, organization=self.parent_org)

        orgs = list(Organization.for_patient(patient_user.id))
        org_ids = [o.id for o in orgs]
        self.assertIn(self.parent_org.id, org_ids)
        self.assertNotIn(other_org.id, org_ids)


# -----------------------------------------------------
# Patient Methods
# -----------------------------------------------------
class PatientMethodTests(TestCase):
    def setUp(self):
        self.user = JheUser.objects.create_user(
            email="patient@example.com",
            password="password",
            user_type="practitioner",
        )
        self.org = Organization.objects.create(name="Hospital", type="prov")
        self.user.practitioner.organizations.add(self.org)

        self.patient = Patient.objects.create(
            jhe_user=self.user,
            name_family="Smith",
            name_given="Alice",
            birth_date="1985-05-05",
            telecom_phone="1234567890",
        )

        PatientOrganization.objects.create(patient=self.patient, organization=self.org)
        # identifier moved from Patient to the PatientIdentifier model
        PatientIdentifier.objects.create(patient=self.patient, system="http://tcp.org", value="PAT001")
        cache.set("jhe_setting:site.url", settings.SITE_URL)

    def test_consolidated_consented_scopes_empty(self):
        scopes = list(self.patient.consolidated_consented_scopes())
        self.assertEqual(len(scopes), 0)

    def test_consolidated_consented_scopes_single(self):
        code = CodeableConcept.objects.create(
            coding_system="https://w3id.org/openmhealth", coding_code="omh:heart-rate:2.0", text="Heart Rate"
        )
        study = Study.objects.create(name="Study A", description="", organization=self.org)
        sp = StudyPatient.objects.create(study=study, patient=self.patient)
        StudyPatientScopeConsent.objects.create(
            study_patient=sp, scope_code=code, consented=True, consented_time=timezone.now()
        )

        scopes = list(self.patient.consolidated_consented_scopes())
        self.assertEqual(len(scopes), 1)
        self.assertEqual(scopes[0].id, code.id)

    def test_consolidated_consented_scopes_multiple_scopes(self):
        bp_code = CodeableConcept.objects.create(
            coding_system="https://w3id.org/openmhealth", coding_code="omh:blood-pressure:4.0", text="Blood pressure"
        )
        hr_code = CodeableConcept.objects.create(
            coding_system="https://w3id.org/openmhealth", coding_code="omh:heart-rate:2.0", text="Heart Rate"
        )
        study = Study.objects.create(name="Study A", description="", organization=self.org)
        sp = StudyPatient.objects.create(study=study, patient=self.patient)
        now = timezone.now()
        StudyPatientScopeConsent.objects.create(
            study_patient=sp, scope_code=bp_code, consented=True, consented_time=now
        )
        StudyPatientScopeConsent.objects.create(
            study_patient=sp, scope_code=hr_code, consented=True, consented_time=now
        )

        scope_ids = {s.id for s in self.patient.consolidated_consented_scopes()}
        self.assertEqual(scope_ids, {bp_code.id, hr_code.id})

    def test_consolidated_consented_scopes_deduplicates_across_studies(self):
        code = CodeableConcept.objects.create(
            coding_system="https://w3id.org/openmhealth", coding_code="omh:heart-rate:2.0", text="Heart Rate"
        )
        now = timezone.now()
        for name in ("Study A", "Study B"):
            study = Study.objects.create(name=name, description="", organization=self.org)
            sp = StudyPatient.objects.create(study=study, patient=self.patient)
            StudyPatientScopeConsent.objects.create(
                study_patient=sp, scope_code=code, consented=True, consented_time=now
            )

        scopes = list(self.patient.consolidated_consented_scopes())
        self.assertEqual(len(scopes), 1)
        self.assertEqual(scopes[0].id, code.id)

    def test_consolidated_consented_scopes_excludes_not_consented(self):
        code = CodeableConcept.objects.create(
            coding_system="https://w3id.org/openmhealth", coding_code="omh:heart-rate:2.0", text="Heart Rate"
        )
        study = Study.objects.create(name="Study A", description="", organization=self.org)
        sp = StudyPatient.objects.create(study=study, patient=self.patient)
        StudyPatientScopeConsent.objects.create(
            study_patient=sp, scope_code=code, consented=False, consented_time=timezone.now()
        )

        scopes = list(self.patient.consolidated_consented_scopes())
        self.assertEqual(len(scopes), 0)

    def test_consolidated_consented_scopes_excludes_other_patient(self):
        code = CodeableConcept.objects.create(
            coding_system="https://w3id.org/openmhealth", coding_code="omh:heart-rate:2.0", text="Heart Rate"
        )
        study = Study.objects.create(name="Study A", description="", organization=self.org)

        other_user = JheUser.objects.create_user(email="other@example.com", password="password", user_type="patient")
        other_patient = other_user.patient
        other_patient.organizations.add(self.org)
        other_sp = StudyPatient.objects.create(study=study, patient=other_patient)
        StudyPatientScopeConsent.objects.create(
            study_patient=other_sp, scope_code=code, consented=True, consented_time=timezone.now()
        )

        scopes = list(self.patient.consolidated_consented_scopes())
        self.assertEqual(len(scopes), 0)

    def test_from_jhe_user_id(self):
        fetched = Patient.from_jhe_user_id(self.user.id)
        self.assertEqual(fetched, self.patient)

    def test_for_practitioner_organization_study(self):
        practitioner_user = JheUser.objects.create_user(
            email="doctor@example.com",
            password="password",
            user_type="practitioner",
        )
        practitioner = Practitioner.objects.get(jhe_user=practitioner_user)

        PractitionerOrganization.objects.create(practitioner=practitioner, organization=self.org)

        study = Study.objects.create(name="Study1", description="Desc", organization=self.org)
        StudyPatient.objects.create(study=study, patient=self.patient)

        patients = list(Patient.for_practitioner_organization_study(practitioner_user.id))
        self.assertGreaterEqual(len(patients), 1)
        self.assertEqual(patients[0].id, self.patient.id)

    def test_for_practitioner_organization_study_filtered_by_organization(self):
        other_org = Organization.objects.create(name="Other Hospital", type="prov")
        self.user.practitioner.organizations.add(other_org)
        other_patient_user = JheUser.objects.create_user(
            email="other_patient@example.com", password="password", user_type="patient"
        )
        other_patient = other_patient_user.patient
        PatientOrganization.objects.create(patient=other_patient, organization=other_org)

        patients = list(Patient.for_practitioner_organization_study(self.user.id, organization_id=self.org.id))
        patient_ids = [p.id for p in patients]
        self.assertIn(self.patient.id, patient_ids)
        self.assertNotIn(other_patient.id, patient_ids)

    def test_for_practitioner_organization_study_filtered_by_study(self):
        study_a = Study.objects.create(name="Study A", description="", organization=self.org)
        study_b = Study.objects.create(name="Study B", description="", organization=self.org)
        StudyPatient.objects.create(study=study_a, patient=self.patient)

        patients_in_a = list(Patient.for_practitioner_organization_study(self.user.id, study_id=study_a.id))
        self.assertEqual(len(patients_in_a), 1)
        self.assertEqual(patients_in_a[0].id, self.patient.id)

        patients_in_b = list(Patient.for_practitioner_organization_study(self.user.id, study_id=study_b.id))
        self.assertEqual(len(patients_in_b), 0)

    def test_for_practitioner_organization_study_filtered_by_patient_id(self):
        other_patient_user = JheUser.objects.create_user(
            email="other_patient@example.com", password="password", user_type="patient"
        )
        other_patient = other_patient_user.patient
        PatientOrganization.objects.create(patient=other_patient, organization=self.org)

        patients = list(Patient.for_practitioner_organization_study(self.user.id, patient_id=self.patient.id))
        self.assertEqual(len(patients), 1)
        self.assertEqual(patients[0].id, self.patient.id)

    def test_for_practitioner_organization_study_filtered_by_patient_identifier(self):
        patients = list(Patient.for_practitioner_organization_study(self.user.id, patient_identifier_value="PAT001"))
        self.assertEqual(len(patients), 1)
        self.assertEqual(patients[0].id, self.patient.id)

    def test_for_practitioner_organization_study_excludes_unauthorized_practitioner(self):
        other_org = Organization.objects.create(name="Other Hospital", type="prov")
        unauthorized_user = JheUser.objects.create_user(
            email="unauthorized@example.com", password="password", user_type="practitioner"
        )
        unauthorized_user.practitioner.organizations.add(other_org)

        patients = list(Patient.for_practitioner_organization_study(unauthorized_user.id))
        patient_ids = [p.id for p in patients]
        self.assertNotIn(self.patient.id, patient_ids)

    def test_for_study_returns_enrolled_patient(self):
        study = Study.objects.create(name="Study1", description="Desc", organization=self.org)
        StudyPatient.objects.create(study=study, patient=self.patient)

        patients = list(Patient.for_study(self.user.id, study.id))
        self.assertEqual(len(patients), 1)
        self.assertEqual(patients[0].id, self.patient.id)

    def test_for_study_excludes_patient_not_enrolled(self):
        study = Study.objects.create(name="Study1", description="Desc", organization=self.org)

        patients = list(Patient.for_study(self.user.id, study.id))
        self.assertEqual(len(patients), 0)

    def test_for_study_excludes_unauthorized_practitioner(self):
        other_org = Organization.objects.create(name="Other Hospital", type="prov")
        unauthorized_user = JheUser.objects.create_user(
            email="unauthorized@example.com", password="password", user_type="practitioner"
        )
        unauthorized_user.practitioner.organizations.add(other_org)

        study = Study.objects.create(name="Study1", description="Desc", organization=self.org)
        StudyPatient.objects.create(study=study, patient=self.patient)

        patients = list(Patient.for_study(unauthorized_user.id, study.id))
        self.assertEqual(len(patients), 0)

    def test_practitioner_authorized(self):
        practitioner_user = JheUser.objects.create_user(
            email="doctor3@example.com",
            password="password",
            user_type="practitioner",
        )
        practitioner = Practitioner.objects.get(jhe_user=practitioner_user)

        PractitionerOrganization.objects.create(practitioner=practitioner, organization=self.org)

        study = Study.objects.create(name="Study1", description="Desc", organization=self.org)
        StudyPatient.objects.create(study=study, patient=self.patient)

        authorized = Patient.practitioner_authorized(practitioner_user.id, self.patient.id)
        self.assertTrue(authorized)

    def test_fhir_search(self):
        with CaptureQueriesContext(connection) as ctx:
            search = Observation.fhir_search(
                self.user.id,
                patient_id=self.patient.id,
                coding_system="https://w3id.org/openmhealth",
                coding_code="omh:heart-rate:2.0",
            )
        # calling fhir_search should only execute looking up practitioner id (lazy queryset)
        self.assertEqual(len(ctx.captured_queries), 1)
        self.assertIsInstance(search, QuerySet)
        # actually execute the result
        results = list(search)
        # TODO: verify actual search results
        self.assertEqual(results, [])


# -----------------------------------------------------
# Patient.fhir_search (ORM query behaviour)
# -----------------------------------------------------
class PatientFhirSearchTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Test Clinic", type="prov")

        self.practitioner_user = JheUser.objects.create_user(
            email="doctor@example.com",
            password="password",
            user_type="practitioner",
        )
        self.practitioner = self.practitioner_user.practitioner
        PractitionerOrganization.objects.create(practitioner=self.practitioner, organization=self.org)

        # Create patient directly (not via user_type="patient") so we fully control fields.
        self.patient_user = JheUser.objects.create_user(
            email="alice@example.com",
            password="password",
        )
        self.patient = Patient.objects.create(
            jhe_user=self.patient_user,
            name_family="Smith",
            name_given="Alice",
            birth_date="1985-05-05",
            telecom_phone="1234567890",
        )
        PatientOrganization.objects.create(patient=self.patient, organization=self.org)
        PatientIdentifier.objects.create(patient=self.patient, system="http://tcp.org", value="PAT001")

        # Enrolled in a study (used by the study-filter test below).
        self.study = Study.objects.create(name="Test Study", description="", organization=self.org)
        StudyPatient.objects.create(study=self.study, patient=self.patient)

    def test_fhir_search_is_lazy(self):
        with CaptureQueriesContext(connection) as ctx:
            search = Patient.fhir_search(self.practitioner_user.id)
        # Only the practitioner lookup should execute before the queryset is iterated
        self.assertEqual(len(ctx.captured_queries), 1)
        self.assertIsInstance(search, QuerySet)

    def test_fhir_search_returns_patient_instances(self):
        results = list(Patient.fhir_search(self.practitioner_user.id))
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], Patient)
        self.assertEqual(results[0].id, self.patient.id)

    def test_fhir_search_filtered_by_study(self):
        other_study = Study.objects.create(name="Other Study", description="", organization=self.org)
        results = list(Patient.fhir_search(self.practitioner_user.id, study_id=other_study.id))
        self.assertEqual(results, [])

        results = list(Patient.fhir_search(self.practitioner_user.id, study_id=self.study.id))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].id, self.patient.id)

    def test_fhir_search_filtered_by_identifier_value(self):
        results = list(Patient.fhir_search(self.practitioner_user.id, patient_identifier_value="PAT001"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].id, self.patient.id)

        results = list(Patient.fhir_search(self.practitioner_user.id, patient_identifier_value="NOMATCH"))
        self.assertEqual(results, [])

    def test_fhir_search_includes_unenrolled_org_patient(self):
        # A practitioner sees every patient sharing one of their organizations, whether or not
        # the patient is enrolled in a study -- organization membership is the access boundary.
        unenrolled_user = JheUser.objects.create_user(email="bob@example.com", password="password")
        unenrolled = Patient.objects.create(
            jhe_user=unenrolled_user,
            name_family="Jones",
            name_given="Bob",
            birth_date="1990-01-01",
            telecom_phone="0000000000",
        )
        PatientOrganization.objects.create(patient=unenrolled, organization=self.org)

        results = list(Patient.fhir_search(self.practitioner_user.id))
        result_ids = [r.id for r in results]
        self.assertIn(unenrolled.id, result_ids)

    def test_fhir_search_excludes_unauthorized_practitioner(self):
        other_user = JheUser.objects.create_user(
            email="other_doc@example.com",
            password="password",
            user_type="practitioner",
        )
        results = list(Patient.fhir_search(other_user.id))
        self.assertEqual(results, [])

    def test_fhir_search_no_duplicate_rows_across_orgs(self):
        # Patient in two shared orgs / enrolled in two studies must appear once (distinct)
        org2 = Organization.objects.create(name="Second Clinic", type="prov")
        PractitionerOrganization.objects.create(practitioner=self.practitioner, organization=org2)
        PatientOrganization.objects.create(patient=self.patient, organization=org2)
        study2 = Study.objects.create(name="Second Study", description="", organization=org2)
        StudyPatient.objects.create(study=study2, patient=self.patient)

        results = list(Patient.fhir_search(self.practitioner_user.id))
        self.assertEqual([r.id for r in results], [self.patient.id])

    def test_fhir_search_nonexistent_practitioner_raises_404(self):
        from django.http import Http404

        with self.assertRaises(Http404):
            list(Patient.fhir_search(jhe_user_id=99999))


# -----------------------------------------------------
# FHIRPatientSerializer (config-driven FHIR rendering)
# -----------------------------------------------------
class FHIRPatientSerializerTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Serializer Clinic", type="prov")
        self.patient_user = JheUser.objects.create_user(email="alice@example.com", password="password")
        self.patient = Patient.objects.create(
            jhe_user=self.patient_user,
            name_family="Smith",
            name_given="Alice",
            birth_date="1985-05-05",
            telecom_phone="1234567890",
        )
        PatientOrganization.objects.create(patient=self.patient, organization=self.org)
        PatientIdentifier.objects.create(patient=self.patient, system="http://tcp.org", value="PAT001")

    def _render(self):
        return FHIRPatientSerializer().to_representation(self.patient)

    def test_output_is_valid_fhir(self):
        as_dict = self._render()
        # Raises if the payload doesn't conform to FHIR R5 Patient
        fhir_patient = FHIRPatient.parse_obj(humps.camelize(as_dict))
        print("\n[FHIRPatientSerializerTests] resource shape:\n", json.dumps(as_dict, indent=2, default=str))
        self.assertEqual(fhir_patient.resource_type, "Patient")

    def test_top_level_shape(self):
        as_dict = self._render()
        self.assertEqual(as_dict["resourceType"], "Patient")
        # id must be a string per the FHIR spec
        self.assertEqual(as_dict["id"], str(self.patient.id))
        self.assertEqual(as_dict["name"], [{"family": "Smith", "given": ["Alice"]}])
        self.assertEqual(str(as_dict["birthDate"]), "1985-05-05")
        self.assertIn("lastUpdated", as_dict["meta"])

    def test_identifier_fans_out_system_and_value(self):
        PatientIdentifier.objects.create(patient=self.patient, system="http://other.org", value="ALT001")
        as_dict = self._render()
        pairs = {(i["system"], i["value"]) for i in as_dict["identifier"]}
        self.assertEqual(pairs, {("http://tcp.org", "PAT001"), ("http://other.org", "ALT001")})

    def test_telecom_includes_phone_and_email(self):
        as_dict = self._render()
        by_system = {t["system"]: t for t in as_dict["telecom"]}
        self.assertEqual(by_system["phone"]["value"], "1234567890")
        self.assertEqual(by_system["phone"]["use"], "mobile")
        self.assertEqual(by_system["email"]["value"], "alice@example.com")
        self.assertEqual(by_system["email"]["use"], "home")

    def test_empty_telecom_entry_is_pruned(self):
        # No phone on file -> the phone ContactPoint (its only DB-sourced value) is dropped
        self.patient.telecom_phone = None
        self.patient.save()
        as_dict = self._render()
        systems = {t["system"] for t in as_dict["telecom"]}
        self.assertNotIn("phone", systems)
        self.assertIn("email", systems)


# -----------------------------------------------------
# Observation.fhir_search (ORM query behaviour)
# -----------------------------------------------------
class ObservationFhirSearchTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Obs Clinic", type="prov")
        self.practitioner_user = JheUser.objects.create_user(
            email="doctor@example.com",
            password="password",
            user_type="practitioner",
        )
        PractitionerOrganization.objects.create(practitioner=self.practitioner_user.practitioner, organization=self.org)

        self.patient_user = JheUser.objects.create_user(email="alice@example.com", password="password")
        self.patient = Patient.objects.create(
            jhe_user=self.patient_user,
            name_family="Smith",
            name_given="Alice",
            birth_date="1985-05-05",
        )
        PatientOrganization.objects.create(patient=self.patient, organization=self.org)

        self.bp_code = CodeableConcept.objects.create(
            coding_system="https://w3id.org/openmhealth", coding_code="omh:blood-pressure:4.0", text="Blood pressure"
        )
        self.ds = DataSource.objects.create(name="Monitor", type="personal_device")
        self.observation = Observation.objects.create(
            subject_patient=self.patient,
            codeable_concept=self.bp_code,
            data_source=self.ds,
            status="final",
            omh_data=generate_observation_value_attachment_data(self.bp_code.coding_code),
        )

    def test_fhir_search_is_lazy(self):
        with CaptureQueriesContext(connection) as ctx:
            search = Observation.fhir_search(self.practitioner_user.id)
        self.assertEqual(len(ctx.captured_queries), 1)
        self.assertIsInstance(search, QuerySet)

    def test_fhir_search_returns_observation_instances(self):
        results = list(Observation.fhir_search(self.practitioner_user.id))
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], Observation)
        self.assertEqual(results[0].id, self.observation.id)

    def test_fhir_search_filtered_by_coding_code(self):
        results = list(Observation.fhir_search(self.practitioner_user.id, coding_code="omh:heart-rate:2.0"))
        self.assertEqual(results, [])

        results = list(Observation.fhir_search(self.practitioner_user.id, coding_code="omh:blood-pressure:4.0"))
        self.assertEqual([o.id for o in results], [self.observation.id])

    def test_fhir_search_filtered_by_patient(self):
        results = list(Observation.fhir_search(self.practitioner_user.id, patient_id=self.patient.id))
        self.assertEqual([o.id for o in results], [self.observation.id])

    def test_fhir_search_filtered_by_observation_id(self):
        other = Observation.objects.create(
            subject_patient=self.patient,
            codeable_concept=self.bp_code,
            data_source=self.ds,
            status="final",
            omh_data=generate_observation_value_attachment_data(self.bp_code.coding_code),
        )
        results = list(Observation.fhir_search(self.practitioner_user.id, resource_id=self.observation.id))
        result_ids = [o.id for o in results]
        self.assertIn(self.observation.id, result_ids)
        self.assertNotIn(other.id, result_ids)

    def test_fhir_search_filtered_by_study_scope(self):
        # Study must both enrol the patient and request the observation's code as a scope
        study = Study.objects.create(name="BP Study", description="", organization=self.org)
        StudyPatient.objects.create(study=study, patient=self.patient)
        StudyScopeRequest.objects.create(study=study, scope_code=self.bp_code)

        results = list(Observation.fhir_search(self.practitioner_user.id, study_id=study.id))
        self.assertEqual([o.id for o in results], [self.observation.id])

        # A study that does not request the BP code returns nothing
        other_code = CodeableConcept.objects.create(
            coding_system="https://w3id.org/openmhealth", coding_code="omh:heart-rate:2.0", text="Heart Rate"
        )
        hr_study = Study.objects.create(name="HR Study", description="", organization=self.org)
        StudyPatient.objects.create(study=hr_study, patient=self.patient)
        StudyScopeRequest.objects.create(study=hr_study, scope_code=other_code)

        results = list(Observation.fhir_search(self.practitioner_user.id, study_id=hr_study.id))
        self.assertEqual(results, [])

    def test_fhir_search_excludes_unauthorized_practitioner(self):
        other_user = JheUser.objects.create_user(
            email="other_doc@example.com",
            password="password",
            user_type="practitioner",
        )
        results = list(Observation.fhir_search(other_user.id))
        self.assertEqual(results, [])

    def test_fhir_search_nonexistent_practitioner_raises_404(self):
        from django.http import Http404

        with self.assertRaises(Http404):
            list(Observation.fhir_search(jhe_user_id=99999))


# -----------------------------------------------------
# FHIRObservationSerializer (config-driven FHIR rendering)
# -----------------------------------------------------
class FHIRObservationSerializerTests(TestCase):
    def setUp(self):
        self.patient_user = JheUser.objects.create_user(email="alice@example.com", password="password")
        self.patient = Patient.objects.create(
            jhe_user=self.patient_user, name_family="Smith", name_given="Alice", birth_date="1985-05-05"
        )
        self.code = CodeableConcept.objects.create(
            coding_system="https://w3id.org/openmhealth", coding_code="omh:blood-pressure:4.0", text="Blood pressure"
        )
        self.ds = DataSource.objects.create(name="Monitor", type="personal_device")
        self.value_data = generate_observation_value_attachment_data(self.code.coding_code)
        self.observation = Observation.objects.create(
            subject_patient=self.patient,
            codeable_concept=self.code,
            data_source=self.ds,
            status="final",
            omh_data=self.value_data,
        )

    def _render(self):
        return FHIRObservationSerializer().to_representation(self.observation)

    def test_output_is_valid_fhir(self):
        as_dict = self._render()
        fhir_observation = FHIRObservation.parse_obj(humps.camelize(as_dict))
        print("\n[FHIRObservationSerializerTests] resource shape:\n", json.dumps(as_dict, indent=2, default=str))
        self.assertEqual(fhir_observation.resource_type, "Observation")

    def test_top_level_shape(self):
        as_dict = self._render()
        self.assertEqual(as_dict["resourceType"], "Observation")
        self.assertEqual(as_dict["id"], str(self.observation.id))
        self.assertEqual(as_dict["status"], "final")
        self.assertIn("lastUpdated", as_dict["meta"])
        self.assertEqual(as_dict["subject"]["reference"], f"Patient/{self.patient.id}")

    def test_code_coding_is_mapped_to_fhir_shape(self):
        as_dict = self._render()
        self.assertEqual(
            as_dict["code"]["coding"],
            [{"system": "https://w3id.org/openmhealth", "code": "omh:blood-pressure:4.0", "display": "Blood pressure"}],
        )

    def test_value_attachment_is_base64_encoded(self):
        as_dict = self._render()
        attachment = as_dict["valueAttachment"]
        self.assertEqual(attachment["contentType"], "application/json")
        # data must be Base64-encoded JSON that round-trips back to the stored value
        decoded = json.loads(base64.b64decode(attachment["data"]).decode("utf-8"))
        self.assertEqual(decoded, self.value_data)

    def test_identifier_fans_out_system_and_value(self):
        ObservationIdentifier.objects.create(observation=self.observation, system="http://tcp.org", value="OBS001")
        as_dict = self._render()
        self.assertEqual(as_dict["identifier"], [{"system": "http://tcp.org", "value": "OBS001"}])

    def test_identifier_absent_when_none(self):
        as_dict = self._render()
        self.assertNotIn("identifier", as_dict)


# -----------------------------------------------------
# Study Methods
# -----------------------------------------------------
class StudyMethodTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Research Org", type="edu")
        self.study = Study.objects.create(name="Study A", description="A study", organization=self.org)

        self.user = JheUser.objects.create_user(
            email="doc@example.com",
            password="password",
            user_type="practitioner",
        )
        self.practitioner = Practitioner.objects.get(jhe_user=self.user)

        PractitionerOrganization.objects.create(practitioner=self.practitioner, organization=self.org)

    def test_for_practitioner_organization(self):
        studies = list(Study.for_practitioner_organization(self.user.id))
        self.assertGreaterEqual(len(studies), 1)
        self.assertTrue(any(s.id == self.study.id for s in studies))

    def test_for_practitioner_organization_filtered_by_organization(self):
        other_org = Organization.objects.create(name="Other Org", type="prov")
        other_study = Study.objects.create(name="Other Study", description="", organization=other_org)

        studies = list(Study.for_practitioner_organization(self.user.id, organization_id=self.org.id))
        study_ids = [s.id for s in studies]
        self.assertIn(self.study.id, study_ids)
        self.assertNotIn(other_study.id, study_ids)

    def test_for_practitioner_organization_filtered_by_study_id(self):
        study_b = Study.objects.create(name="Study B", description="", organization=self.org)

        studies_a = list(Study.for_practitioner_organization(self.user.id, study_id=self.study.id))
        self.assertEqual(len(studies_a), 1)
        self.assertEqual(studies_a[0].id, self.study.id)

        studies_b = list(Study.for_practitioner_organization(self.user.id, study_id=study_b.id))
        self.assertEqual(len(studies_b), 1)
        self.assertEqual(studies_b[0].id, study_b.id)

    def test_for_practitioner_organization_excludes_unauthorized_practitioner(self):
        other_org = Organization.objects.create(name="Other Org", type="prov")
        unauthorized_user = JheUser.objects.create_user(
            email="unauth@example.com", password="password", user_type="practitioner"
        )
        unauthorized_user.practitioner.organizations.add(other_org)

        studies = list(Study.for_practitioner_organization(unauthorized_user.id))
        study_ids = [s.id for s in studies]
        self.assertNotIn(self.study.id, study_ids)

    def test_for_practitioner_organization_nonexistent_user_raises_404(self):
        from django.http import Http404

        with self.assertRaises(Http404):
            list(Study.for_practitioner_organization(jhe_user_id=99999))

    def test_practitioner_authorized(self):
        authorized = Study.practitioner_authorized(self.user.id, self.study.id)
        self.assertTrue(authorized)

    def test_has_patient(self):
        patient_user = JheUser.objects.create_user(email="patient2@example.com", password="password")

        patient = Patient.objects.create(
            jhe_user=patient_user,
            name_family="Jones",
            name_given="Bob",
            birth_date="1990-01-01",
            telecom_phone="0987654321",
        )

        PatientOrganization.objects.create(patient=patient, organization=self.org)

        StudyPatient.objects.create(study=self.study, patient=patient)
        result = Study.has_patient(self.study.id, patient.id)
        self.assertTrue(result)

    def test_studies_with_scopes(self):
        # With no scope requests or consents present, expect an empty list.
        studies = Study.studies_with_scopes(patient_id=1)  # Using a dummy patient id
        self.assertIsInstance(studies, list)


# -----------------------------------------------------
# StudyPatientScopeConsent Methods
# -----------------------------------------------------
class StudyPatientScopeConsentMethodTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Hospital", type="prov")
        self.user = JheUser.objects.create_user(email="patient3@example.com", password="password")

        self.patient = Patient.objects.create(
            jhe_user=self.user,
            name_family="Brown",
            name_given="Charlie",
            birth_date="1980-01-01",
            telecom_phone="5550001234",
        )

        PatientOrganization.objects.create(patient=self.patient, organization=self.org)

        self.study = Study.objects.create(name="Study B", description="Desc", organization=self.org)
        self.study_patient = StudyPatient.objects.create(study=self.study, patient=self.patient)
        self.code = CodeableConcept.objects.create(
            coding_system="https://w3id.org/openmhealth", coding_code="omh:heart-rate:2.0", text="Heart Rate"
        )
        self.consent = StudyPatientScopeConsent.objects.create(
            study_patient=self.study_patient,
            scope_actions="rs",
            scope_code=self.code,
            consented=True,
            consented_time=timezone.now(),
        )
        self.scope_request = StudyScopeRequest.objects.create(study=self.study, scope_code=self.code)

    def test_patient_scopes_returns_consented_code(self):
        scopes = list(StudyPatientScopeConsent.patient_scopes(self.user.id))
        scope_ids = [s.id for s in scopes]
        self.assertIn(self.code.id, scope_ids)

    def test_patient_scopes_excludes_not_consented_scope(self):
        other_code = CodeableConcept.objects.create(
            coding_system="https://w3id.org/openmhealth", coding_code="omh:blood-pressure:3.0", text="Blood Pressure"
        )
        StudyPatientScopeConsent.objects.create(
            study_patient=self.study_patient,
            scope_actions="rs",
            scope_code=other_code,
            consented=False,
            consented_time=timezone.now(),
        )
        scopes = list(StudyPatientScopeConsent.patient_scopes(self.user.id))
        scope_ids = [s.id for s in scopes]
        self.assertNotIn(other_code.id, scope_ids)

    def test_studies_with_scopes_returns_consented_study(self):
        studies = Study.studies_with_scopes(patient_id=self.patient.id)
        self.assertEqual(len(studies), 1)
        self.assertEqual(studies[0].id, self.study.id)
        self.assertEqual(len(studies[0].scope_consents), 1)
        consent = studies[0].scope_consents[0]
        self.assertEqual(consent["code"]["id"], self.code.id)
        self.assertTrue(consent["consented"])

    def test_studies_with_scopes_pending_returns_unconsented_study(self):
        pending_code = CodeableConcept.objects.create(
            coding_system="https://w3id.org/openmhealth", coding_code="omh:step-count:3.0", text="Step Count"
        )
        pending_study = Study.objects.create(name="Pending Study", description="", organization=self.org)
        StudyScopeRequest.objects.create(study=pending_study, scope_code=pending_code)
        StudyPatient.objects.create(study=pending_study, patient=self.patient)

        studies = Study.studies_with_scopes(patient_id=self.patient.id, pending=True)
        study_ids = [s.id for s in studies]
        self.assertIn(pending_study.id, study_ids)
        self.assertNotIn(self.study.id, study_ids)

    def test_studies_with_scopes_empty_for_unknown_patient(self):
        studies = Study.studies_with_scopes(patient_id=99999)
        self.assertEqual(studies, [])


# -----------------------------------------------------
# DataSource Methods
# -----------------------------------------------------
class DataSourceMethodTests(TestCase):
    def setUp(self):
        self.ds = DataSource.objects.create(name="Smartphone", type="personal_device")
        self.code = CodeableConcept.objects.create(
            coding_system="https://w3id.org/openmhealth", coding_code="omh:heart-rate:2.0", text="Heart Rate"
        )
        self.supported = DataSourceSupportedScope.objects.create(data_source=self.ds, scope_code=self.code)

    def test_data_sources_with_scopes(self):
        data_sources = list(DataSource.data_sources_with_scopes(data_source_id=self.ds.id))
        self.assertGreaterEqual(len(data_sources), 1)
        ds = data_sources[0]
        self.assertTrue(hasattr(ds, "supported_scopes"))
        self.assertGreaterEqual(len(ds.supported_scopes), 1)

    def test_data_sources_with_scopes_scope_content(self):
        data_sources = list(DataSource.data_sources_with_scopes(data_source_id=self.ds.id))
        scope_ids = [s.id for s in data_sources[0].supported_scopes]
        self.assertIn(self.code.id, scope_ids)

    def test_data_sources_with_scopes_by_study_id(self):
        org = Organization.objects.create(name="Org", type="prov")
        study = Study.objects.create(name="Study", description="", organization=org)
        StudyDataSource.objects.create(study=study, data_source=self.ds)

        data_sources = list(DataSource.data_sources_with_scopes(study_id=study.id))
        ds_ids = [ds.id for ds in data_sources]
        self.assertIn(self.ds.id, ds_ids)
        scope_ids = [s.id for s in data_sources[0].supported_scopes]
        self.assertIn(self.code.id, scope_ids)

    def test_data_sources_with_scopes_excludes_ds_not_in_study(self):
        other_ds = DataSource.objects.create(name="Other Device", type="personal_device")
        org = Organization.objects.create(name="Org", type="prov")
        study = Study.objects.create(name="Study", description="", organization=org)
        StudyDataSource.objects.create(study=study, data_source=self.ds)

        data_sources = list(DataSource.data_sources_with_scopes(study_id=study.id))
        ds_ids = [ds.id for ds in data_sources]
        self.assertNotIn(other_ds.id, ds_ids)


# -----------------------------------------------------
# Observation Methods
# -----------------------------------------------------
class ObservationMethodTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Clinic", type="prov")
        self.user = JheUser.objects.create_user(
            email="patient4@example.com",
            password="password",
            user_type="practitioner",
        )
        self.user.practitioner.organizations.add(self.org)

        self.patient = Patient.objects.create(
            jhe_user=self.user,
            name_family="White",
            name_given="Daisy",
            birth_date="1975-07-07",
            telecom_phone="1112223333",
        )

        PatientOrganization.objects.create(patient=self.patient, organization=self.org)

        self.code = CodeableConcept.objects.create(
            coding_system="https://w3id.org/openmhealth",
            coding_code="omh:blood-pressure:4.0",
            text="Blood pressure",
        )
        self.ds = DataSource.objects.create(name="Monitor", type="personal_device")
        self.observation = Observation.objects.create(
            subject_patient=self.patient,
            codeable_concept=self.code,
            data_source=self.ds,
            status="final",
            omh_data=generate_observation_value_attachment_data(self.code.coding_code),
        )
        cache.set("jhe_setting:site.url", settings.SITE_URL)

    def _make_practitioner(self, email):
        user = JheUser.objects.create_user(email=email, password="password", user_type="practitioner")
        PractitionerOrganization.objects.create(practitioner=user.practitioner_profile, organization=self.org)
        return user

    def test_for_practitioner_organization_study_patient(self):
        practitioner_user = self._make_practitioner("doctor4@example.com")

        results = list(Observation.for_practitioner_organization_study_patient(practitioner_user.id))
        result_ids = [r.id for r in results]
        self.assertIn(self.observation.id, result_ids)

    def test_for_practitioner_organization_study_patient_filtered_by_organization(self):
        practitioner_user = self._make_practitioner("doctor_org@example.com")
        other_org = Organization.objects.create(name="Other Org", type="prov")
        other_patient_user = JheUser.objects.create_user(email="other_patient@example.com", password="password")
        other_patient = Patient.objects.create(
            jhe_user=other_patient_user,
            name_family="Other",
            name_given="Patient",
            birth_date="1990-01-01",
            telecom_phone="0000000000",
        )
        PatientOrganization.objects.create(patient=other_patient, organization=other_org)
        other_obs = Observation.objects.create(
            subject_patient=other_patient,
            codeable_concept=self.code,
            data_source=self.ds,
            status="final",
            omh_data=generate_observation_value_attachment_data(self.code.coding_code),
        )

        results = list(
            Observation.for_practitioner_organization_study_patient(practitioner_user.id, organization_id=self.org.id)
        )
        result_ids = [r.id for r in results]
        self.assertIn(self.observation.id, result_ids)
        self.assertNotIn(other_obs.id, result_ids)

    def test_for_practitioner_organization_study_patient_filtered_by_study_id(self):
        practitioner_user = self._make_practitioner("doctor_study@example.com")
        study = Study.objects.create(name="Study", description="", organization=self.org)
        StudyPatient.objects.create(study=study, patient=self.patient)
        StudyScopeRequest.objects.create(study=study, scope_code=self.code)

        results = list(Observation.for_practitioner_organization_study_patient(practitioner_user.id, study_id=study.id))
        result_ids = [r.id for r in results]
        self.assertIn(self.observation.id, result_ids)

    def test_for_practitioner_organization_study_patient_filtered_by_patient_id(self):
        practitioner_user = self._make_practitioner("doctor_pat@example.com")

        results = list(
            Observation.for_practitioner_organization_study_patient(practitioner_user.id, patient_id=self.patient.id)
        )
        result_ids = [r.id for r in results]
        self.assertIn(self.observation.id, result_ids)

    def test_for_practitioner_organization_study_patient_filtered_by_observation_id(self):
        practitioner_user = self._make_practitioner("doctor_obs@example.com")
        other_obs = Observation.objects.create(
            subject_patient=self.patient,
            codeable_concept=self.code,
            data_source=self.ds,
            status="final",
            omh_data=generate_observation_value_attachment_data(self.code.coding_code),
        )

        results = list(
            Observation.for_practitioner_organization_study_patient(
                practitioner_user.id, observation_id=self.observation.id
            )
        )
        result_ids = [r.id for r in results]
        self.assertIn(self.observation.id, result_ids)
        self.assertNotIn(other_obs.id, result_ids)

    def test_for_practitioner_organization_study_patient_excludes_unauthorized(self):
        other_org = Organization.objects.create(name="Other Org", type="prov")
        unauthorized_user = JheUser.objects.create_user(
            email="unauth@example.com", password="password", user_type="practitioner"
        )
        unauthorized_user.practitioner_profile.organizations.add(other_org)

        results = list(Observation.for_practitioner_organization_study_patient(unauthorized_user.id))
        result_ids = [r.id for r in results]
        self.assertNotIn(self.observation.id, result_ids)

    def test_for_practitioner_organization_study_patient_nonexistent_user_raises_404(self):
        from django.http import Http404

        with self.assertRaises(Http404):
            list(Observation.for_practitioner_organization_study_patient(jhe_user_id=99999))

    def test_practitioner_authorized(self):
        practitioner_user = JheUser.objects.create_user(
            email="doctor5@example.com",
            password="password",
            user_type="practitioner",
        )
        practitioner = Practitioner.objects.get(jhe_user=practitioner_user)

        PractitionerOrganization.objects.create(practitioner=practitioner, organization=self.org)

        # Create a study and link the patient to it
        study = Study.objects.create(name="Study for Auth", description="Desc", organization=self.org)
        study_patient = StudyPatient.objects.create(study=study, patient=self.patient)

        # Create consent for the required code
        StudyPatientScopeConsent.objects.create(
            study_patient=study_patient,
            scope_actions="rs",  # read/search permissions
            scope_code=self.code,
            consented=True,
            consented_time=timezone.now(),
        )

        # Now the authorization check should pass
        authorized = Observation.practitioner_authorized(practitioner_user.id, self.observation.id)
        self.assertTrue(authorized)

    def test_fhir_search(self):
        with CaptureQueriesContext(connection) as ctx:
            search = Observation.fhir_search(
                self.user.id,
                patient_id=self.patient.id,
                coding_system="https://w3id.org/openmhealth",
                coding_code="omh:heart-rate:2.0",
            )
        # calling fhir_search should only lookup practitioner id
        # not anything else (the queryset is lazy)
        self.assertEqual(len(ctx.captured_queries), 1)
        self.assertIsInstance(search, QuerySet)
        # actually execute the result: only a blood-pressure observation exists, so a
        # heart-rate coding_code filter returns nothing
        results = list(search)
        self.assertEqual(results, [])

    def test_fhir_create(self):
        # Construct a minimal valid FHIR observation payload.
        fhir_data = {
            "resourceType": "Observation",
            "status": "final",
            "code": {"coding": [{"system": "https://w3id.org/openmhealth", "code": "omh:blood-pressure:4.0"}]},
            "subject": {"reference": f"Patient/{self.patient.id}"},
            "device": {"reference": f"Device/{self.ds.id}"},
            "valueAttachment": {
                "data": base64.b64encode(
                    json.dumps(generate_observation_value_attachment_data("omh:blood-pressure:4.0")).encode("ascii")
                ).decode("ascii")
            },
        }
        # Create a study and consent so that fhir_create passes the permission check.
        study = Study.objects.create(name="Study for FHIR", description="Desc", organization=self.org)
        study_patient = StudyPatient.objects.create(study=study, patient=self.patient)
        StudyPatientScopeConsent.objects.create(
            study_patient=study_patient,
            scope_actions="rs",
            scope_code=self.code,
            consented=True,
            consented_time=timezone.now(),
        )
        try:
            created_obs = Observation.fhir_create(fhir_data, self.user)
            self.assertIsNotNone(created_obs)
            self.assertEqual(created_obs.codeable_concept, self.code)
        except Exception as e:
            self.fail(f"fhir_create raised an exception: {e}")


# -----------------------------------------------------
# Tests for PatientOrganization and PractitionerOrganization
# -----------------------------------------------------
class PatientOrganizationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Hospital", type="prov")
        self.user = JheUser.objects.create_user(email="patient5@example.com", password="password")
        self.patient = Patient.objects.create(
            jhe_user=self.user,
            name_family="Green",
            name_given="Edward",
            birth_date="1970-03-15",
            telecom_phone="5551234567",
        )

    def test_create_patient_organization(self):
        po = PatientOrganization.objects.create(patient=self.patient, organization=self.org)
        self.assertEqual(po.patient, self.patient)
        self.assertEqual(po.organization, self.org)

        orgs = self.patient.organizations.all()
        self.assertEqual(orgs.count(), 1)
        self.assertEqual(orgs.first(), self.org)

        patients = self.org.patients.all()
        self.assertEqual(patients.count(), 1)
        self.assertEqual(patients.first(), self.patient)


class PractitionerOrganizationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Hospital", type="prov")
        self.user = JheUser.objects.create_user(
            email="doctor7@example.com",
            password="password",
            user_type="practitioner",
        )
        self.practitioner = Practitioner.objects.get(jhe_user=self.user)

    def test_create_practitioner_organization(self):
        po = PractitionerOrganization.objects.create(practitioner=self.practitioner, organization=self.org)
        self.assertEqual(po.practitioner, self.practitioner)
        self.assertEqual(po.organization, self.org)

        orgs = self.practitioner.organizations.all()
        self.assertEqual(orgs.count(), 1)
        self.assertEqual(orgs.first(), self.org)

        practitioners = self.org.practitioners.all()
        self.assertEqual(practitioners.count(), 1)
        self.assertEqual(practitioners.first(), self.practitioner)

    def test_organization_users_property(self):
        patient_user = JheUser.objects.create_user(email="patient6@example.com", password="password")
        patient = Patient.objects.create(
            jhe_user=patient_user,
            name_family="Black",
            name_given="Frank",
            birth_date="1965-11-25",
            telecom_phone="5559876543",
        )
        PatientOrganization.objects.create(patient=patient, organization=self.org)

        PractitionerOrganization.objects.create(practitioner=self.practitioner, organization=self.org)

        users = self.org.users
        self.assertEqual(users.count(), 2)
        user_ids = list(users.values_list("id", flat=True))
        self.assertIn(patient_user.id, user_ids)
        self.assertIn(self.user.id, user_ids)
