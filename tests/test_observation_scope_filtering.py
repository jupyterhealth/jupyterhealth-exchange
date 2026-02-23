"""
Tests for GHI #228: Observation scope filtering by study

Validates that observations are correctly filtered by study scope codes,
ensuring that a study requesting only Blood Pressure does NOT surface
Heart Rate observations (and vice versa), even when the patient is
enrolled in multiple studies with different scopes.

Also validates the seed command (GHI #193 Bug 1) produces no duplicate
observations across study groups.
"""

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import JheUser, Observation, Organization, PractitionerOrganization

from .utils import (
    Code,
    add_observations,
    add_patient_to_study,
    create_study,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def org(db):
    return Organization.objects.create(name="Test Org", type="other")


@pytest.fixture
def practitioner(org):
    user = JheUser.objects.create_user(
        email="scope-practitioner@example.org",
        password="testpass123",
        identifier="scope-practitioner",
        user_type="practitioner",
    )
    PractitionerOrganization.objects.create(
        practitioner=user.practitioner,
        organization=org,
        role="manager",
    )
    return user


@pytest.fixture
def patient_a(org):
    user = JheUser.objects.create_user(
        email="patient-a@example.org",
        password="testpass123",
        identifier="patient-a",
        user_type="patient",
    )
    user.patient.organizations.add(org)
    return user.patient


@pytest.fixture
def patient_b(org):
    user = JheUser.objects.create_user(
        email="patient-b@example.org",
        password="testpass123",
        identifier="patient-b",
        user_type="patient",
    )
    user.patient.organizations.add(org)
    return user.patient


# ---------------------------------------------------------------------------
# Unit tests – for_practitioner_organization_study_patient()
# ---------------------------------------------------------------------------


class TestForPractitionerStudyFilters:
    """Unit tests for Observation.for_practitioner_organization_study_patient()"""

    def test_study_filters_by_scope_code(self, practitioner, org, patient_a):
        """
        GHI #228 regression: querying by study_id should only return
        observations whose codeable_concept matches a StudyScopeRequest.
        """
        hr_study = create_study(organization=org, codes=[Code.HeartRate])
        add_patient_to_study(patient_a, hr_study)
        add_observations(patient=patient_a, code=Code.HeartRate, n=3)
        add_observations(patient=patient_a, code=Code.BloodPressure, n=4)

        results = list(
            Observation.for_practitioner_organization_study_patient(
                jhe_user_id=practitioner.id,
                study_id=hr_study.id,
            )
        )
        assert len(results) == 3
        codes = {r.codeable_concept.coding_code for r in results}
        assert codes == {Code.HeartRate.value}

    def test_study_with_multiple_scopes(self, practitioner, org, patient_a):
        """A study requesting BP + HR should return both, but not glucose."""
        bp_hr_study = create_study(organization=org, codes=[Code.HeartRate, Code.BloodPressure])
        add_patient_to_study(patient_a, bp_hr_study)
        add_observations(patient=patient_a, code=Code.HeartRate, n=2)
        add_observations(patient=patient_a, code=Code.BloodPressure, n=3)
        add_observations(patient=patient_a, code=Code.BloodGlucose, n=4)

        results = list(
            Observation.for_practitioner_organization_study_patient(
                jhe_user_id=practitioner.id,
                study_id=bp_hr_study.id,
            )
        )
        assert len(results) == 5
        codes = {r.codeable_concept.coding_code for r in results}
        assert codes == {Code.HeartRate.value, Code.BloodPressure.value}

    def test_no_study_returns_all_observations(self, practitioner, org, patient_a):
        """When study_id is None, all observations should be returned."""
        hr_study = create_study(organization=org, codes=[Code.HeartRate])
        add_patient_to_study(patient_a, hr_study)
        add_observations(patient=patient_a, code=Code.HeartRate, n=3)
        add_observations(patient=patient_a, code=Code.BloodPressure, n=4)

        results = list(
            Observation.for_practitioner_organization_study_patient(
                jhe_user_id=practitioner.id,
                study_id=None,
            )
        )
        assert len(results) == 7

    def test_patient_in_two_studies_different_scopes(self, practitioner, org, patient_a):
        """
        GHI #228 core scenario: patient enrolled in HR study AND BP study.
        Querying by HR study must return only HR observations.
        """
        hr_study = create_study(name="HR Only", organization=org, codes=[Code.HeartRate])
        bp_study = create_study(name="BP Only", organization=org, codes=[Code.BloodPressure])
        add_patient_to_study(patient_a, hr_study)
        add_patient_to_study(patient_a, bp_study)
        add_observations(patient=patient_a, code=Code.HeartRate, n=5)
        add_observations(patient=patient_a, code=Code.BloodPressure, n=3)

        hr_results = list(
            Observation.for_practitioner_organization_study_patient(
                jhe_user_id=practitioner.id,
                study_id=hr_study.id,
            )
        )
        assert len(hr_results) == 5

        bp_results = list(
            Observation.for_practitioner_organization_study_patient(
                jhe_user_id=practitioner.id,
                study_id=bp_study.id,
            )
        )
        assert len(bp_results) == 3


# ---------------------------------------------------------------------------
# Unit tests – fhir_search()
# ---------------------------------------------------------------------------


class TestFhirSearchStudyFilters:
    """Unit tests for Observation.fhir_search() scope filtering"""

    def test_fhir_search_filters_by_study_scope(self, practitioner, org, patient_a):
        """fhir_search with study_id should only return matching scope codes."""
        hr_study = create_study(organization=org, codes=[Code.HeartRate])
        add_patient_to_study(patient_a, hr_study)
        add_observations(patient=patient_a, code=Code.HeartRate, n=4)
        add_observations(patient=patient_a, code=Code.BloodPressure, n=6)

        results = list(
            Observation.fhir_search(
                jhe_user_id=practitioner.id,
                study_id=hr_study.id,
            )
        )
        assert len(results) == 4

    def test_fhir_search_no_study_returns_all(self, practitioner, org, patient_a):
        """fhir_search without study_id returns all observations."""
        study = create_study(organization=org, codes=[Code.HeartRate])
        add_patient_to_study(patient_a, study)
        add_observations(patient=patient_a, code=Code.HeartRate, n=2)
        add_observations(patient=patient_a, code=Code.BloodPressure, n=3)

        results = list(
            Observation.fhir_search(
                jhe_user_id=practitioner.id,
                study_id=None,
            )
        )
        assert len(results) == 5

    def test_fhir_search_two_studies_isolation(self, practitioner, org, patient_a):
        """Each study should see only its own scope codes through fhir_search."""
        hr_study = create_study(name="HR", organization=org, codes=[Code.HeartRate])
        bp_study = create_study(name="BP", organization=org, codes=[Code.BloodPressure])
        add_patient_to_study(patient_a, hr_study)
        add_patient_to_study(patient_a, bp_study)
        add_observations(patient=patient_a, code=Code.HeartRate, n=3)
        add_observations(patient=patient_a, code=Code.BloodPressure, n=7)

        hr = list(Observation.fhir_search(jhe_user_id=practitioner.id, study_id=hr_study.id))
        bp = list(Observation.fhir_search(jhe_user_id=practitioner.id, study_id=bp_study.id))
        assert len(hr) == 3
        assert len(bp) == 7


# ---------------------------------------------------------------------------
# Integration tests – FHIR API endpoint
# ---------------------------------------------------------------------------


class TestFhirApiObservationByStudyScope:
    """Integration tests hitting the /fhir/r5/Observation endpoint"""

    @pytest.fixture
    def api_client(self, practitioner):
        from rest_framework.test import APIClient

        client = APIClient()
        client.default_format = "json"
        client.force_authenticate(practitioner)
        return client

    def test_api_returns_only_study_scoped_observations(self, api_client, org, patient_a):
        """
        End-to-end: create HR and BP studies, add patient to both,
        query via FHIR API with study filter — should get only matching obs.
        """
        hr_study = create_study(name="HR API", organization=org, codes=[Code.HeartRate])
        bp_study = create_study(name="BP API", organization=org, codes=[Code.BloodPressure])
        add_patient_to_study(patient_a, hr_study)
        add_patient_to_study(patient_a, bp_study)
        add_observations(patient=patient_a, code=Code.HeartRate, n=4)
        add_observations(patient=patient_a, code=Code.BloodPressure, n=6)

        # Query by HR study
        r = api_client.get(
            "/fhir/r5/Observation",
            {"patient._has:Group:member:_id": hr_study.id},
        )
        assert r.status_code == 200, f"{r.status_code}: {r.text}"
        assert r.json()["total"] == 4

        # Query by BP study
        r = api_client.get(
            "/fhir/r5/Observation",
            {"patient._has:Group:member:_id": bp_study.id},
        )
        assert r.status_code == 200, f"{r.status_code}: {r.text}"
        assert r.json()["total"] == 6

    def test_api_no_study_filter_returns_all(self, api_client, org, patient_a):
        """Without a study filter, all observations for the patient are returned."""
        study = create_study(organization=org, codes=[Code.HeartRate])
        add_patient_to_study(patient_a, study)
        add_observations(patient=patient_a, code=Code.HeartRate, n=2)
        add_observations(patient=patient_a, code=Code.BloodPressure, n=3)

        r = api_client.get(
            "/fhir/r5/Observation",
            {"patient": patient_a.id},
        )
        assert r.status_code == 200, f"{r.status_code}: {r.text}"
        assert r.json()["total"] == 5

    def test_api_multi_scope_study(self, api_client, org, patient_a):
        """A study with BP+HR scopes returns both codes but not glucose."""
        bp_hr = create_study(name="BPHR", organization=org, codes=[Code.HeartRate, Code.BloodPressure])
        add_patient_to_study(patient_a, bp_hr)
        add_observations(patient=patient_a, code=Code.HeartRate, n=2)
        add_observations(patient=patient_a, code=Code.BloodPressure, n=3)
        add_observations(patient=patient_a, code=Code.BloodGlucose, n=4)

        r = api_client.get(
            "/fhir/r5/Observation",
            {"patient._has:Group:member:_id": bp_hr.id},
        )
        assert r.status_code == 200, f"{r.status_code}: {r.text}"
        assert r.json()["total"] == 5


# ---------------------------------------------------------------------------
# Boundary tests
# ---------------------------------------------------------------------------


class TestObservationBoundaryConditions:
    """Edge cases and boundary conditions for observation scope filtering"""

    def test_patient_with_no_consents_returns_nothing(self, practitioner, org, patient_a):
        """A patient with observations but no study enrollment returns nothing when queried by study."""
        hr_study = create_study(organization=org, codes=[Code.HeartRate])
        # Patient not added to study
        add_observations(patient=patient_a, code=Code.HeartRate, n=3)

        results = list(
            Observation.for_practitioner_organization_study_patient(
                jhe_user_id=practitioner.id,
                study_id=hr_study.id,
            )
        )
        assert len(results) == 0

    def test_patient_consented_no_matching_observations(self, practitioner, org, patient_a):
        """Patient enrolled in HR study but has only BP observations → 0 results."""
        hr_study = create_study(organization=org, codes=[Code.HeartRate])
        add_patient_to_study(patient_a, hr_study)
        add_observations(patient=patient_a, code=Code.BloodPressure, n=5)

        results = list(
            Observation.for_practitioner_organization_study_patient(
                jhe_user_id=practitioner.id,
                study_id=hr_study.id,
            )
        )
        assert len(results) == 0

    def test_two_patients_same_study_isolated(self, practitioner, org, patient_a, patient_b):
        """Two patients in same HR study; each patient's obs counted correctly."""
        hr_study = create_study(organization=org, codes=[Code.HeartRate])
        add_patient_to_study(patient_a, hr_study)
        add_patient_to_study(patient_b, hr_study)
        add_observations(patient=patient_a, code=Code.HeartRate, n=3)
        add_observations(patient=patient_b, code=Code.HeartRate, n=5)

        # Query for patient_a only
        results_a = list(
            Observation.for_practitioner_organization_study_patient(
                jhe_user_id=practitioner.id,
                study_id=hr_study.id,
                patient_id=patient_a.id,
            )
        )
        assert len(results_a) == 3

        # Query all patients in study
        results_all = list(
            Observation.for_practitioner_organization_study_patient(
                jhe_user_id=practitioner.id,
                study_id=hr_study.id,
            )
        )
        assert len(results_all) == 8

    def test_three_code_scenario(self, practitioner, org, patient_a):
        """
        Three studies with different single scopes.
        Observations spread across all three codes.
        Each study query returns only its own code's observations.
        """
        hr_study = create_study(name="HR3", organization=org, codes=[Code.HeartRate])
        bp_study = create_study(name="BP3", organization=org, codes=[Code.BloodPressure])
        bg_study = create_study(name="BG3", organization=org, codes=[Code.BloodGlucose])

        add_patient_to_study(patient_a, hr_study)
        add_patient_to_study(patient_a, bp_study)
        add_patient_to_study(patient_a, bg_study)

        add_observations(patient=patient_a, code=Code.HeartRate, n=2)
        add_observations(patient=patient_a, code=Code.BloodPressure, n=4)
        add_observations(patient=patient_a, code=Code.BloodGlucose, n=6)

        hr = list(
            Observation.for_practitioner_organization_study_patient(
                jhe_user_id=practitioner.id,
                study_id=hr_study.id,
            )
        )
        bp = list(
            Observation.for_practitioner_organization_study_patient(
                jhe_user_id=practitioner.id,
                study_id=bp_study.id,
            )
        )
        bg = list(
            Observation.for_practitioner_organization_study_patient(
                jhe_user_id=practitioner.id,
                study_id=bg_study.id,
            )
        )
        assert len(hr) == 2
        assert len(bp) == 4
        assert len(bg) == 6


# ---------------------------------------------------------------------------
# Seed command test
# ---------------------------------------------------------------------------


class TestSeedNoDuplicateObservations:
    """Validates that the seed command does not produce duplicate observations (GHI #193 Bug 1)"""

    def test_seed_observation_counts(self, db):
        """
        Run the seed command and verify:
        - Total count matches expected consents (no Bug 1 duplicates)
        - Berkeley and UCSF observations are correctly scoped
        """
        call_command("seed", "--flush-db")
        observations = Observation.objects.all()

        # Expected consents:
        # Berkeley:
        #   peter  → bp_hr(BP, HR) = 2 obs
        #   pamela → bp_hr(BP, HR) + bp(BP) = 3 obs  (2 BP obs is correct: one per consent)
        # UCSF:
        #   percy → mosl_bt(BT) = 1 obs
        #   paul  → olgin_o2(O2) = 1 obs
        #   pat   → cardio_rr(RR) + olgin_o2(O2) = 2 obs
        # Total = 5 + 4 = 9
        assert observations.count() == 9, f"Expected 9 observations (5 berkeley + 4 ucsf), got {observations.count()}"

        # Verify no cross-contamination from Bug 1:
        # Berkeley patients should not have UCSF-only codes (RR, BT, O2)
        from core.models import CodeableConcept, JheUser

        berkeley_emails = ["peter@example.com", "pamela@example.com"]
        berkeley_patients = [JheUser.objects.get(email=e).patient for e in berkeley_emails]
        ucsf_only_codes = CodeableConcept.objects.filter(
            coding_code__in=["omh:respiratory-rate:2.0", "omh:body-temperature:4.0", "omh:oxygen-saturation:2.0"]
        )
        cross_contaminated = observations.filter(
            subject_patient__in=berkeley_patients,
            codeable_concept__in=ucsf_only_codes,
        )
        assert (
            cross_contaminated.count() == 0
        ), f"Bug 1 regression: Berkeley patients have UCSF observations: {list(cross_contaminated.values_list('subject_patient__jhe_user__email', 'codeable_concept__coding_code'))}"
