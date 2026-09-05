"""The seed's sleep wiring: Oura + the Open Wearables client on the Sleep & BP study.

Nothing else in the suite runs the full `seed` command, and a data point that fails schema
validation aborts it (Observation.save -> clean), so this doubles as a smoke test that the
seed still completes.
"""

import pytest
from oauth2_provider.models import get_application_model

from core.management.commands.seed import SEED_MANAGED_AUX_KEYS
from core.models import (
    ClientDataSource,
    CodeableConcept,
    DataSource,
    DataSourceSupportedScope,
    JheClient,
    JheSetting,
    Observation,
    Patient,
    Study,
    StudyClient,
    StudyDataSource,
    StudyScopeRequest,
)

SLEEP_CODE = "ieee:sleep-episode:1.0"
STUDY_NAME = "Lifespan Study on Sleep & BP"


def test_oura_supports_sleep_episode_and_heart_rate(seeded):
    oura = DataSource.objects.get(name="Oura")
    scopes = set(
        DataSourceSupportedScope.objects.filter(data_source=oura).values_list("scope_code__coding_code", flat=True)
    )
    assert scopes == {"omh:heart-rate:2.0", SLEEP_CODE}


def test_sleep_study_requests_sleep_from_oura_via_ow_client(seeded):
    study = Study.objects.get(name=STUDY_NAME)
    assert SLEEP_CODE in set(
        StudyScopeRequest.objects.filter(study=study).values_list("scope_code__coding_code", flat=True)
    )
    assert "Oura" in set(StudyDataSource.objects.filter(study=study).values_list("data_source__name", flat=True))
    assert "Open Wearables" in set(StudyClient.objects.filter(study=study).values_list("client__name", flat=True))

    ow = get_application_model().objects.get(name="Open Wearables")
    assert list(ClientDataSource.objects.filter(client=ow).values_list("data_source__name", flat=True)) == ["Oura"]


@pytest.mark.parametrize("email", ["ll_patient_peter@example.com", "ll_patient_pamela@example.com"])
def test_patients_have_schema_valid_sleep_observations(seeded, email):
    patient = Patient.objects.get(jhe_user__email=email)
    observations = Observation.objects.filter(subject_patient=patient, codeable_concept__coding_code=SLEEP_CODE)
    assert observations.count() == 2
    for observation in observations:
        observation.clean()  # raises if the body fails the IEEE sleep-episode schema
        assert observation.data_source.name == "Oura"
        # sleep-episode requires a time_interval, so both period columns must be populated.
        assert observation.effective_period_start and observation.effective_period_end


def test_bp_hr_study_requests_clinical_records_via_ehr_patient_portal(seeded):
    study = Study.objects.get(name="Lifespan Study on BP & HR")
    ds = DataSource.objects.get(name="EHR Patient Portal")
    star = CodeableConcept.objects.get(coding_system="http://hl7.org/fhir/resource-types", coding_code="*")
    assert star.text == "Clinical records"
    assert StudyDataSource.objects.filter(study=study, data_source=ds).exists()
    assert StudyScopeRequest.objects.filter(study=study, scope_code=star).exists()

    pamela = Patient.objects.get(jhe_user__email="ll_patient_pamela@example.com")
    pending = Study.studies_with_scopes(pamela.id, pending=True)
    assert any(
        s.id == study.id and any(c["code"]["coding_code"] == "*" for c in s.pending_scope_consents) for s in pending
    )


@pytest.mark.parametrize(
    ("name", "path"),
    [
        ("EHR Patient Portal", "/clients/ehr-patient-portal/?code=CODE"),
        ("Open Wearables", "/clients/ow/launch?code=CODE"),
    ],
)
def test_patient_clients_invite_to_their_own_page(seeded, name, path):
    # One invitation is one client: the link opens that client's page, which lists only its own data sources.
    jhe_client = JheClient.objects.get(application__name=name)
    assert jhe_client.invitation_url.endswith(path)
    assert "patient_facing" not in (jhe_client.aux_data or {})
    assert SEED_MANAGED_AUX_KEYS == {"scopes"}


@pytest.mark.parametrize("key", ["site.ui.logo", "site.ui.theme_css"])
def test_patient_facing_brand_settings_are_seeded_empty(seeded, key):
    setting = JheSetting.objects.get(key=key)
    assert setting.value_type == "string"
    assert not setting.get_value()
