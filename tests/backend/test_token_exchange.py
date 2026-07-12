import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from core import oidc_verify

ISS = "https://ehr.example.org/fhir"
AUD = "smart-client-id"
ID_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id_token"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"

# Confidential "SoF EHR Launch" client used to authenticate to the exchange endpoint.
CLIENT_ID = "test-sof-client"
CLIENT_SECRET = "test-sof-secret"


@pytest.fixture
def rsa_private_pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem, key.public_key()


@pytest.fixture(autouse=True)
def trust_settings(settings, db):
    """Configure the token exchange via JheSettings (auth.sof.*), matching runtime."""
    from django.core.cache import cache

    from core.models import JheSetting

    settings.SITE_URL = "http://testserver"

    def _set(key, value_type, value):
        s, _ = JheSetting.objects.update_or_create(key=key, defaults={"value_type": value_type})
        s.set_value(value_type, value)
        s.save()
        cache.delete(f"jhe_setting:{key}")

    _set("auth.sof.trusted_issuers", "json", [ISS])
    _set("auth.sof.trusted_audience", "string", AUD)
    cache.delete("jhe_setting:site.url")


@pytest.fixture(autouse=True)
def sof_client(db):
    """The confidential client an external SMART app authenticates as."""
    from oauth2_provider.models import get_application_model

    app = get_application_model()(
        name="Test SoF EHR Launch",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,  # DOT hashes on save (hash_client_secret=True)
        client_type="confidential",
        authorization_grant_type="client-credentials",
        hash_client_secret=True,
        redirect_uris="",
        skip_authorization=True,
        algorithm="RS256",
    )
    app.save()
    return app


@pytest.fixture(autouse=True)
def patch_jwks(monkeypatch, rsa_private_pem):
    _, public_key = rsa_private_pem

    class _SigningKey:
        key = public_key

    class _FakeClient:
        def get_signing_key_from_jwt(self, token):
            return _SigningKey()

    monkeypatch.setattr(oidc_verify, "discover_jwks_uri", lambda issuer: "https://ehr.example.org/jwks")
    monkeypatch.setattr(oidc_verify, "_jwk_client", lambda jwks_uri: _FakeClient())


def make_token(
    priv_pem, *, iss=ISS, aud=AUD, fhir_user="Practitioner/test-practitioner", exp_delta=3600, alg="RS256", key=None
):
    now = int(time.time())
    claims = {"iss": iss, "aud": aud, "sub": "prac-1", "fhirUser": fhir_user, "iat": now, "exp": now + exp_delta}
    return jwt.encode(claims, key or priv_pem, algorithm=alg)


def post(client, token, *, client_id=CLIENT_ID, client_secret=CLIENT_SECRET, http_authorization=None, **overrides):
    data = {
        "subject_token": token,
        "subject_token_type": ID_TOKEN_TYPE,
        "requested_token_type": ACCESS_TOKEN_TYPE,
        "audience": "http://testserver",
        "grant_type": GRANT,
        "scope": "openid",
    }
    # client_secret_post: client credentials in the form body.
    if client_id is not None:
        data["client_id"] = client_id
    if client_secret is not None:
        data["client_secret"] = client_secret
    data.update(overrides)
    extra = {"HTTP_AUTHORIZATION": http_authorization} if http_authorization else {}
    return client.post("/o/token-exchange", data=data, **extra)


# `user` fixture (conftest) is a practitioner with identifier="test-practitioner".
def test_valid_id_token_issues_jhe_token(client, user, rsa_private_pem):
    priv, _ = rsa_private_pem
    r = post(client, make_token(priv))
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["access_token"]
    assert body["token_type"] == "Bearer"


def test_issued_token_linked_to_client_and_user(client, user, sof_client, rsa_private_pem):
    """The issued access token must be linked to the authenticated client and the
    resolved Practitioner (not an orphan token with application=NULL)."""
    from oauth2_provider.models import get_access_token_model

    priv, _ = rsa_private_pem
    r = post(client, make_token(priv))
    assert r.status_code == 200, r.content
    tok = get_access_token_model().objects.get(token=r.json()["access_token"])
    assert tok.application_id == sof_client.id
    assert tok.user_id == user.id


def test_missing_client_auth_unauthorized(client, user, rsa_private_pem):
    priv, _ = rsa_private_pem
    r = post(client, make_token(priv), client_id=None, client_secret=None)
    assert r.status_code == 401


def test_wrong_client_secret_unauthorized(client, user, rsa_private_pem):
    priv, _ = rsa_private_pem
    r = post(client, make_token(priv), client_secret="wrong-secret")
    assert r.status_code == 401


def test_http_basic_client_auth_accepted(client, user, rsa_private_pem):
    """Client credentials via HTTP Basic (client_secret_basic) must also work — both
    placements are documented. Regression: the oauthlib Request was built with an
    'Authorization' header key, but django-oauth-toolkit's _extract_basic_auth reads
    'HTTP_AUTHORIZATION', so Basic auth could never succeed."""
    import base64

    priv, _ = rsa_private_pem
    creds = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    r = post(
        client,
        make_token(priv),
        client_id=None,
        client_secret=None,
        http_authorization=f"Basic {creds}",
    )
    assert r.status_code == 200, r.content
    assert r.json()["access_token"]


def test_http_basic_wrong_secret_unauthorized(client, user, rsa_private_pem):
    import base64

    priv, _ = rsa_private_pem
    creds = base64.b64encode(f"{CLIENT_ID}:wrong-secret".encode()).decode()
    r = post(
        client,
        make_token(priv),
        client_id=None,
        client_secret=None,
        http_authorization=f"Basic {creds}",
    )
    assert r.status_code == 401


def test_trailing_slash_issuer_accepted(client, user, rsa_private_pem):
    """A token whose `iss` has a trailing slash (e.g. MedPlum's
    'https://api.medplum.com/') must still verify when the trusted issuer is
    configured without the slash. Regression: the expected issuer passed to
    signature verification must match the token's `iss` exactly, not a stripped form."""
    priv, _ = rsa_private_pem
    r = post(client, make_token(priv, iss=ISS + "/"))
    assert r.status_code == 200, r.content
    assert r.json()["access_token"]


def test_untrusted_issuer_forbidden(client, user, rsa_private_pem):
    priv, _ = rsa_private_pem
    r = post(client, make_token(priv, iss="https://evil.example"))
    assert r.status_code == 403


def test_expired_token_unauthorized(client, user, rsa_private_pem):
    priv, _ = rsa_private_pem  # expired beyond the clock-skew leeway
    r = post(client, make_token(priv, exp_delta=-3600))
    assert r.status_code == 401


def test_hs256_token_unauthorized(client, user, rsa_private_pem):
    priv, _ = rsa_private_pem
    r = post(client, make_token(priv, alg="HS256", key="shared-secret"))
    assert r.status_code == 401


def test_unknown_practitioner_not_found(client, db, rsa_private_pem):
    priv, _ = rsa_private_pem  # no `user` fixture -> identifier not in DB
    r = post(client, make_token(priv, fhir_user="Practitioner/ghost"))
    assert r.status_code == 404


def test_non_practitioner_fhir_user_forbidden(client, user, rsa_private_pem):
    priv, _ = rsa_private_pem
    r = post(client, make_token(priv, fhir_user="Patient/test-practitioner"))
    assert r.status_code == 403


def test_duplicate_identifier_not_found(client, user, rsa_private_pem):
    # identifier is not unique -> a duplicate row must 404, not 500
    from core.models import JheUser

    JheUser.objects.create_user(email="dupe@example.org", identifier="test-practitioner")
    priv, _ = rsa_private_pem
    r = post(client, make_token(priv))
    assert r.status_code == 404


def test_audience_mismatch_bad_request(client, user, rsa_private_pem):
    """audience != SITE_URL must be rejected with HTTP 400."""
    priv, _ = rsa_private_pem
    r = post(client, make_token(priv), audience="https://wrong.example.org")
    assert r.status_code == 400
