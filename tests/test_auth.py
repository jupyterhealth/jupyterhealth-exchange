import json
import time
from urllib.parse import parse_qs

from django.conf import settings
import pytest
import responses

from core.models import JheUser

practitioner_id = "practitioner-c56c"
patient_id = "patient-1fbe0b2f"
idp_url = idp_url = "https://example.localhost"


def userinfo_response(request):
    token = request.headers["Authorization"].split()[1]
    headers = {"Content-Type": "application/json"}
    if token == "practitioner-token":
        return (
            200,
            headers,
            json.dumps(
                {
                    "sub": practitioner_id,
                    "profile": f"Practitioner/{practitioner_id}",
                    "name": "Practitioner Name",
                }
            ),
        )
    elif token == "patient-token":
        return (
            200,
            headers,
            json.dumps(
                {
                    "sub": patient_id,
                    "profile": f"Patient/{patient_id}",
                    "name": "Patient Name",
                }
            ),
        )
    elif token == "other-token":
        return (
            200,
            headers,
            json.dumps(
                {
                    "sub": "other-sub-id",
                    "profile": "Practitioner/other-sub-id",
                    "name": "Practitioner Name",
                }
            ),
        )
    else:
        return 403, headers, json.dumps({"error": "Not a valid token"})


def introspection_response(request):
    token = parse_qs(request.body)["token"][0]

    headers = {"Content-Type": "application/json"}

    # sample introspection from medplum
    # (excluding 'sub')
    introspection = {
        "active": True,
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
        "iss": settings.TRUSTED_TOKEN_IDP,
        "client_id": "abc-123",
        "scope": "user/*.* patient/*.read openid profile launch launch/patient",
        "patient": "01961612-dbdc-759b-b885-f55117556bb6",
    }

    if token == "practitioner-token":
        response = {"sub": practitioner_id}
        response.update(introspection)
        return 200, headers, json.dumps(response)
    elif token == "patient-token":
        response = {"sub": patient_id}
        response.update(introspection)
        return 200, headers, json.dumps(response)
    else:
        return 200, headers, json.dumps({"active": False})


@pytest.fixture
def practitioner_user(db):
    practitioner_user = JheUser.objects.create_user(
        email="practitioner-user@example.com",
        password="password",
        identifier=practitioner_id,
        user_type="practitioner",
    )
    return practitioner_user


@pytest.fixture
def patient_user(db):
    return JheUser.objects.create_user(
        email="patient-user@example.com",
        password="password",
        identifier=patient_id,
        user_type="patient",
    )


@pytest.fixture(autouse=True)
def setup_idp(practitioner_user, patient_user):
    settings.TRUSTED_TOKEN_IDP = idp_url
    oidc_config = {
        "authorization_endpoint": f"{idp_url}/oauth2/authorize",
        "token_endpoint": f"{idp_url}/oauth2/token",
        "userinfo_endpoint": f"{idp_url}/oauth2/userinfo",
        "introspection_endpoint": f"{idp_url}/oauth2/introspect",
    }

    responses.add_callback(
        responses.GET,
        oidc_config["userinfo_endpoint"],
        callback=userinfo_response,
        content_type="application/json",
    )

    responses.add_callback(
        responses.POST,
        oidc_config["introspection_endpoint"],
        callback=introspection_response,
        content_type="application/json",
    )

    responses.get(
        f"{idp_url}/.well-known/openid-configuration",
        body=json.dumps(oidc_config),
        content_type="application/json",
    )
    responses.start()
    yield
    responses.stop()
    responses.reset()


request_fields = {
    "audience": settings.SITE_URL,
    "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
    "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
}


def test_token_exchange(client, practitioner_user):
    request_data = {}
    request_data.update(request_fields)
    request_data["subject_token"] = "practitioner-token"

    response = client.post(
        "/o/token-exchange",
        data=request_data,
    )
    token_info = response.json()
    assert response.headers["Content-Type"] == "application/json"
    assert response.status_code == 200
    assert token_info["expires_in"] > 0
    assert token_info["scope"] == "openid"
    assert token_info["token_type"] == "Bearer"
    assert token_info["issued_token_type"] == "urn:ietf:params:oauth:token-type:access_token"
    access_token = token_info["access_token"]
    r = client.get("/api/v1/users/profile", headers={"Authorization": f"Bearer {access_token}"})
    assert r.status_code == 200
    user_info = r.json()
    assert user_info["id"] == practitioner_user.id


def test_missing_arguments(client):
    response = client.post(
        "/o/token-exchange",
        data={"subject_token": "patient-id"},
    )
    info = response.json()
    assert response.headers["Content-Type"] == "application/json"
    assert response.status_code == 400
    assert "Missing required argument" in info["error"]


def test_audience_mismatch(client):
    request_data = {}
    request_data.update(request_fields)
    request_data["subject_token"] = "practitioner-token"
    request_data["audience"] = "https://jhe"
    response = client.post(
        "/o/token-exchange",
        data=request_data,
    )
    info = response.json()
    assert response.headers["Content-Type"] == "application/json"
    assert response.status_code == 400
    assert info["error"] == "audience must be http://localhost:8000, not https://jhe"


def test_patient_not_practitioner(client, practitioner_user):
    request_data = {}
    request_data.update(request_fields)
    request_data["subject_token"] = "patient-token"
    response = client.post(
        "/o/token-exchange",
        data=request_data,
    )
    info = response.json()
    assert response.headers["Content-Type"] == "application/json"
    assert response.status_code == 404
    assert "Practitioner not found" in info["error"]


def test_practitioner_not_found(client):
    request_data = {}
    request_data.update(request_fields)
    request_data["subject_token"] = "other-token"
    response = client.post(
        "/o/token-exchange",
        data=request_data,
    )
    info = response.json()
    assert response.headers["Content-Type"] == "application/json"
    assert response.status_code == 404
    assert "Practitioner not found" in info["error"]


def test_invalid_token(client):
    request_data = {}
    request_data.update(request_fields)
    request_data["subject_token"] = "no-such-token"
    response = client.post(
        "/o/token-exchange",
        data=request_data,
    )
    info = response.json()
    assert response.headers["Content-Type"] == "application/json"
    assert response.status_code == 400
    assert "Token not found" in info["error"]
