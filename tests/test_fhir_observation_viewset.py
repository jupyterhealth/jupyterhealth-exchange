import base64
import json
import uuid
from copy import deepcopy

from rest_framework.test import APIClient
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import (
    CodeableConcept,
    DataSource,
    JheUser,
    Observation,
    Organization,
    Patient,
    PatientOrganization,
    Study,
    StudyPatient,
    StudyPatientScopeConsent,
    StudyScopeRequest,
)
from core.utils import generate_observation_value_attachment_data


class ObservationViewSetTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.organization = Organization.objects.create(name="Test Org", type="prov")
        self.user = JheUser.objects.create_user(
            email="dual-role@example.com",
            password="password",
            identifier="dual-123",
            user_type="practitioner",
        )
        self.user.practitioner.organizations.add(self.organization)
        self.patient = Patient.objects.create(
            jhe_user=self.user,
            identifier="DUAL100",
            name_family="Dual",
            name_given="Role",
            birth_date="1990-01-01",
        )
        PatientOrganization.objects.create(patient=self.patient, organization=self.organization)
        self.client.force_authenticate(user=self.user)

    def fill_study(self, n_records=100):
        self.study = Study.objects.create(name="hr strudy", description="desc", organization=self.organization)
        StudyPatient.objects.create(study=self.study, patient=self.patient)
        self.study_patient = StudyPatient.objects.get(study=self.study, patient=self.patient)
        self.code, _ = CodeableConcept.objects.update_or_create(
            coding_system="https://w3id.org/openmhealth",
            coding_code="omh:heart-rate:2.0",
            text="Heart Rate",
        )
        code = self.code
        StudyScopeRequest.objects.create(study=self.study, scope_code=code)
        StudyPatientScopeConsent.objects.create(
            study_patient=self.study_patient,
            scope_code=code,
            consented=True,
            consented_time=timezone.now(),
        )

        observations = []

        starting_attachment = generate_observation_value_attachment_data(code.coding_code)
        for i in range(n_records):
            attachment = deepcopy(starting_attachment)
            attachment["header"]["uuid"] = str(uuid.uuid4())
            observations.append(
                Observation(
                    subject_patient=self.patient,
                    codeable_concept=code,
                    value_attachment_data=attachment,
                )
            )
        Observation.objects.bulk_create(observations, batch_size=100)

    def _get_observations(self, **params):
        r = self.client.get(
            "/fhir/r5/Observation",
            {
                "patient": self.patient.id,
                "_has:Group:member:_id": self.study.id,
                **params,
            },
        )
        return r.json()

    def _get_link(self, bundle, rel):
        """Get link from FHIR link list"""
        for link in bundle["link"]:
            if link["relation"] == rel:
                return link["url"]
        return None

    def test_observation_pagination(self):
        n = 101
        per_page = 10
        self.fill_study(n)
        with CaptureQueriesContext(connection) as ctx:
            response = self._get_observations(_count=per_page)
        self.assertLess(len(ctx.captured_queries), 8)
        last_query = ctx.captured_queries[-1]["sql"]
        # try to make sure our offset/limit were applied
        self.assertIn("LIMIT 10", last_query)
        self.assertNotIn("OFFSET", last_query)

        self.assertEqual(response["type"], "searchset")
        self.assertEqual(response["resourceType"], "Bundle")
        results = []
        self.assertEqual(response["total"], n)
        results = response["entry"]
        self.assertEqual(len(results), per_page)
        visited = set()
        have_ids = [r["resource"]["id"] for r in results]

        while len(results) < n:
            next_url = self._get_link(response, "next")
            self.assertNotIn(next_url, have_ids)
            visited.add(next_url)
            with CaptureQueriesContext(connection) as ctx:
                response = self.client.get(next_url).json()
            for record in response["entry"]:
                resource_id = record["resource"]["id"]
                self.assertNotIn(resource_id, have_ids)
                have_ids.append(resource_id)
                results.append(record)

        self.assertEqual(len(results), n)
        self.assertLess(len(ctx.captured_queries), 8)
        last_query = ctx.captured_queries[-1]["sql"]
        # try to make sure our offset/limit were applied
        self.assertIn("OFFSET 100", last_query)

        # no 'next' link on last page
        next_rels = [link["relation"] for link in response["link"]]
        self.assertEqual(next_rels, ["previous"])

    def test_observation_limit(self):
        """Test a large query with lots of entries"""
        n = 10_100
        per_page = 1_000
        self.fill_study(n)
        response = self._get_observations(_count=per_page)

        results = response["entry"]
        self.assertEqual(len(results), per_page)
        visited = set()
        have_ids = {r["resource"]["id"] for r in results}
        while len(results) < n:
            next_url = self._get_link(response, "next")
            self.assertNotIn(next_url, have_ids)
            visited.add(next_url)
            response = self.client.get(next_url).json()
            for record in response["entry"]:
                resource_id = record["resource"]["id"]
                self.assertNotIn(resource_id, have_ids)
                have_ids.add(resource_id)
                results.append(record)

        self.assertEqual(len(results), n)

        # no 'next' link on last page
        next_rels = [link["relation"] for link in response["link"]]
        self.assertEqual(next_rels, ["previous"])

    def test_observation_upload_bundle(self):
        self.fill_study(0)
        coding_code = [
            {
                "system": self.code.coding_system,
                "code": self.code.coding_code,
            }
        ]

        entries = []
        device, _ = DataSource.objects.update_or_create(name="test device")
        device_id = device.id
        for i in range(10):
            record = generate_observation_value_attachment_data(self.code.coding_code)

            entry = {
                "resource": {
                    "resourceType": "Observation",
                    "status": "final",
                    "code": {
                        "coding": coding_code,
                    },
                    "subject": {"reference": f"Patient/{self.patient.id}"},
                    "device": {"reference": f"Device/{device_id}"},
                    "valueAttachment": {
                        "contentType": "application/json",
                        "data": base64.b64encode(json.dumps(record).encode()).decode(),
                        # "data": record,
                    },
                },
                "request": {"method": "POST", "url": "Observation"},
            }
            entries.append(entry)
        request_payload = {
            "resourceType": "Bundle",
            "type": "batch",
            "entry": entries,
        }
        r = self.client.post("/fhir/r5/", data=request_payload, format="json")
        for entry in r.json()["entry"]:
            if "outcome" in entry["response"]:
                for issue in entry["response"]["outcome"]["issue"]:
                    print(issue["diagnostics"])
                raise ValueError("error!")
        if r.status_code != 200:
            print(r)
        self.assertEqual(r.status_code, 200)
        response = self._get_observations(patient=self.patient.id)
        results = response["entry"]
        self.assertEqual(len(results), 10)
        self.assertEqual(results[0]["resource"]["subject"], entries[0]["resource"]["subject"])
        value_attachment_out = json.loads(base64.b64decode(results[0]["resource"]["valueAttachment"]["data"]).decode())
        value_attachment_in = json.loads(base64.b64decode(entries[0]["resource"]["valueAttachment"]["data"]).decode())
        self.assertEqual(value_attachment_out["body"], value_attachment_in["body"])

    def test_observation_upload(self):
        self.fill_study(0)
        coding_code = [
            {
                "system": self.code.coding_system,
                "code": self.code.coding_code,
            }
        ]

        device, _ = DataSource.objects.update_or_create(name="test device")
        device_id = device.id
        record = generate_observation_value_attachment_data(self.code.coding_code)

        resource = {
            "resourceType": "Observation",
            "status": "final",
            "code": {
                "coding": coding_code,
            },
            "subject": {"reference": f"Patient/{self.patient.id}"},
            "device": {"reference": f"Device/{device_id}"},
            "valueAttachment": {
                "contentType": "application/json",
                "data": base64.b64encode(json.dumps(record).encode()).decode(),
                # "data": record,
            },
        }
        r = self.client.post("/fhir/r5/Observation", data=resource, format="json")
        if r.status_code != 201:
            print(r)
        self.assertEqual(r.status_code, 201)
        response = self._get_observations(patient=self.patient.id)
        results = response["entry"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["resource"]["subject"], resource["subject"])
        value_attachment_out = json.loads(base64.b64decode(results[0]["resource"]["valueAttachment"]["data"]).decode())
        value_attachment_in = json.loads(base64.b64decode(resource["valueAttachment"]["data"]).decode())
        self.assertEqual(value_attachment_out["body"], value_attachment_in["body"])
