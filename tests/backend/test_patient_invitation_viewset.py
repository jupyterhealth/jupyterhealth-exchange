from oauth2_provider.models import get_application_model

from core.models import JheClient

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
