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
def trust_settings(settings):
    from django.core.cache import cache
    settings.SITE_URL = "http://testserver"
    settings.TRUSTED_TOKEN_ISSUERS = [ISS]
    settings.TRUSTED_TOKEN_AUDIENCE = AUD
    cache.delete("jhe_setting:site.url")


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


def make_token(priv_pem, *, iss=ISS, aud=AUD, fhir_user="Practitioner/test-practitioner",
               exp_delta=3600, alg="RS256", key=None):
    now = int(time.time())
    claims = {"iss": iss, "aud": aud, "sub": "prac-1", "fhirUser": fhir_user,
              "iat": now, "exp": now + exp_delta}
    return jwt.encode(claims, key or priv_pem, algorithm=alg)


def post(client, token):
    return client.post(
        "/o/token-exchange",
        data={
            "subject_token": token,
            "subject_token_type": ID_TOKEN_TYPE,
            "requested_token_type": ACCESS_TOKEN_TYPE,
            "audience": "http://testserver",
            "grant_type": GRANT,
            "iss": ISS,
            "scope": "openid",
        },
    )


# `user` fixture (conftest) is a practitioner with identifier="test-practitioner".
def test_valid_id_token_issues_jhe_token(client, user, rsa_private_pem):
    priv, _ = rsa_private_pem
    r = post(client, make_token(priv))
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["access_token"]
    assert body["token_type"] == "Bearer"


def test_untrusted_issuer_forbidden(client, user, rsa_private_pem):
    priv, _ = rsa_private_pem
    r = client.post(
        "/o/token-exchange",
        data={
            "subject_token": make_token(priv, iss="https://evil.example"),
            "subject_token_type": ID_TOKEN_TYPE,
            "requested_token_type": ACCESS_TOKEN_TYPE,
            "audience": "http://testserver",
            "grant_type": GRANT,
            "iss": "https://evil.example",
            "scope": "openid",
        },
    )
    assert r.status_code == 403


def test_expired_token_unauthorized(client, user, rsa_private_pem):
    priv, _ = rsa_private_pem
    r = post(client, make_token(priv, exp_delta=-10))
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


def test_audience_mismatch_bad_request(client, user, rsa_private_pem):
    """audience != SITE_URL must be rejected with HTTP 400."""
    priv, _ = rsa_private_pem
    r = client.post(
        "/o/token-exchange",
        data={
            "subject_token": make_token(priv),
            "subject_token_type": ID_TOKEN_TYPE,
            "requested_token_type": ACCESS_TOKEN_TYPE,
            "audience": "https://wrong.example.org",
            "grant_type": GRANT,
            "iss": ISS,
            "scope": "openid",
        },
    )
    assert r.status_code == 400
