from django.core import mail
from oauth2_provider.models import get_application_model

from core.models import JheClient
from core.views.patient_invitation import _single_pending_study_name

from .utils import Code, add_patient_to_study, create_study

Application = get_application_model()


def test_create_invitation_returns_201(api_client, user, patient):
    client_app = Application.objects.create(
        name="invite client",
        user=user,
        client_type=Application.CLIENT_PUBLIC,
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
    )
    JheClient.objects.create(application=client_app, invitation_url="https://example.org/CODE")
    r = api_client.post(
        "/api/v1/invitation",
        {"patient_id": patient.id, "client_id": client_app.id, "send_email": False},
    )
    assert r.status_code == 201, r.text
    assert "invitationLink" in r.json()


def test_create_invitation_sends_a_branded_email(api_client, user, patient):
    client_app = Application.objects.create(
        name="invite client",
        user=user,
        client_type=Application.CLIENT_PUBLIC,
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
    )
    JheClient.objects.create(application=client_app, invitation_url="https://example.org/CODE")
    mail.outbox = []

    r = api_client.post(
        "/api/v1/invitation",
        {"patient_id": patient.id, "client_id": client_app.id, "send_email": True},
    )

    assert r.status_code == 201, r.text
    assert mail.outbox[0].subject == "You're invited to share your health data"
    assert "/static/common/images/jupyterhealth-logo.png" in mail.outbox[0].body


def test_single_pending_study_name_none_when_no_pending_studies(db, patient):
    # A freshly-created patient has no study enrollment, so no pending study to name.
    assert _single_pending_study_name(patient) is None


def test_single_pending_study_name_returns_name_of_sole_pending_study(organization, patient):
    study = create_study(name="Cardiometabolic Health Study", organization=organization, codes=[Code.HeartRate])
    add_patient_to_study(patient=patient, study=study, consent=False)
    assert _single_pending_study_name(patient) == "Cardiometabolic Health Study"


def test_single_pending_study_name_none_when_multiple_pending_studies(organization, patient):
    study_a = create_study(name="Study A", organization=organization, codes=[Code.HeartRate])
    study_b = create_study(name="Study B", organization=organization, codes=[Code.BloodPressure])
    add_patient_to_study(patient=patient, study=study_a, consent=False)
    add_patient_to_study(patient=patient, study=study_b, consent=False)
    assert _single_pending_study_name(patient) is None
