import json
import time
from urllib.parse import parse_qs

import pytest
import responses
from django.conf import settings

from core.models import JheUser

practitioner_id = "practitioner-c56c"
patient_id = "patient-1fbe0b2f"
idp_url = "https://example.localhost"


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
            "iss": idp_url,
        },
    )
    info = response.json()
    assert response.headers["Content-Type"] == "application/json"
    assert response.status_code == 400
    assert "subject_token_type" in info["error"]
