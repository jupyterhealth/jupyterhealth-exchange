from oauth2_provider.models import get_application_model

from core.models import ClientDataSource

Application = get_application_model()


def test_add_remove_client_data_sources(api_client, user, device):
    client_app = Application.objects.create(
        name="test client",
        user=user,
        client_type=Application.CLIENT_PUBLIC,
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
    )
    url = f"/api/v1/clients/{client_app.id}/data_sources"
    r = api_client.post(url, {"data_source_id": device.id})
    assert r.status_code == 201, r.text
    assert ClientDataSource.objects.filter(client=client_app, data_source=device).count() == 1

    r = api_client.delete(url, {"data_source_id": device.id})
    assert r.status_code == 204, r.text
    assert ClientDataSource.objects.filter(client=client_app, data_source=device).count() == 0
