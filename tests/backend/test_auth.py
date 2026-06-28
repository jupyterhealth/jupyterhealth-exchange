from django.conf import settings


def test_missing_arguments(client):
    response = client.post(
        "/o/token-exchange",
        data={"subject_token": "patient-id"},
    )
    info = response.json()
    assert response.headers["Content-Type"] == "application/json"
    assert response.status_code == 400
    assert "Missing required argument" in info["error"]


def test_wrong_subject_token_type(client):
    # The endpoint now requires id_token; reject old access_token type.
    response = client.post(
        "/o/token-exchange",
        data={
            "audience": settings.SITE_URL,
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "subject_token": "some-token",
        },
    )
    info = response.json()
    assert response.headers["Content-Type"] == "application/json"
    assert response.status_code == 400
    assert "subject_token_type" in info["error"]
