from rest_framework.test import APIClient
from django.test import TestCase

from core.models import (
    CodeableConcept,
    JheUser,
    Organization,
    Patient,
    PatientOrganization,
    Study,
    StudyPatient,
    StudyPatientScopeConsent,
)


class PatientViewSetTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.organization = Organization.objects.create(name="Test Org", type="prov")
        self.user = JheUser.objects.create_user(
            email="dual-role@example.com",
            password="password",
            identifier="dual-123",
            user_type="practitioner",
        )
        self.patient = Patient.objects.create(
            jhe_user=self.user,
            identifier="DUAL100",
            name_family="Dual",
            name_given="Role",
            birth_date="1990-01-01",
        )
        PatientOrganization.objects.create(patient=self.patient, organization=self.organization)
        self.study = Study.objects.create(name="Dual Study", description="desc", organization=self.organization)
        StudyPatient.objects.create(study=self.study, patient=self.patient)
        self.study_patient = StudyPatient.objects.get(study=self.study, patient=self.patient)
        self.scope = CodeableConcept.objects.create(
            coding_system="https://example.com",
            coding_code="dual-scope",
            text="Dual role scope",
        )

    def test_patient_practitioner_can_update_own_consents(self):
        payload = {
            "study_scope_consents": [
                {
                    "study_id": self.study.id,
                    "scope_consents": [
                        {
                            "coding_system": self.scope.coding_system,
                            "coding_code": self.scope.coding_code,
                            "consented": True,
                        }
                    ],
                }
            ]
        }
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            f"/api/v1/patients/{self.patient.id}/consents",
            data=payload,
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        # Idempotency: a second POST with the same payload should not create duplicates.
        response2 = self.client.post(
            f"/api/v1/patients/{self.patient.id}/consents",
            data=payload,
            format="json",
        )
        self.assertEqual(response2.status_code, 200)

        created = StudyPatientScopeConsent.objects.filter(
            study_patient=self.study_patient,
            scope_code=self.scope,
        )
        self.assertEqual(created.count(), 1)
