import time

import jwt
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from core import oidc_verify
from core.oidc_verify import IdTokenError, discover_jwks_uri, parse_fhir_user, verify_id_token

ISS = "https://ehr.example.org/fhir"
AUD = "smart-client-id"


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
def patch_jwks(monkeypatch, rsa_private_pem):
    _, public_key = rsa_private_pem

    class _SigningKey:
        key = public_key

    class _FakeClient:
        def get_signing_key_from_jwt(self, token):
            return _SigningKey()

    monkeypatch.setattr(oidc_verify, "discover_jwks_uri", lambda issuer: "https://ehr.example.org/jwks")
    monkeypatch.setattr(oidc_verify, "_jwk_client", lambda jwks_uri: _FakeClient())


def make_token(priv_pem, *, iss=ISS, aud=AUD, fhir_user="Practitioner/abc",
               exp_delta=3600, alg="RS256", key=None):
    now = int(time.time())
    claims = {"iss": iss, "aud": aud, "sub": "prac-1", "fhirUser": fhir_user,
              "iat": now, "exp": now + exp_delta}
    return jwt.encode(claims, key or priv_pem, algorithm=alg)


def test_valid_token_returns_claims(rsa_private_pem):
    priv, _ = rsa_private_pem
    claims = verify_id_token(make_token(priv), issuer=ISS, audience=AUD)
    assert claims["fhirUser"] == "Practitioner/abc"


def test_wrong_audience_rejected(rsa_private_pem):
    priv, _ = rsa_private_pem
    with pytest.raises(IdTokenError) as e:
        verify_id_token(make_token(priv, aud="someone-else"), issuer=ISS, audience=AUD)
    assert e.value.status_code == 401


def test_wrong_issuer_rejected(rsa_private_pem):
    priv, _ = rsa_private_pem
    with pytest.raises(IdTokenError) as e:
        verify_id_token(make_token(priv, iss="https://evil.example"), issuer=ISS, audience=AUD)
    assert e.value.status_code == 401


def test_expired_token_rejected(rsa_private_pem):
    # expired well beyond the clock-skew leeway
    priv, _ = rsa_private_pem
    with pytest.raises(IdTokenError) as e:
        verify_id_token(make_token(priv, exp_delta=-3600), issuer=ISS, audience=AUD)
    assert e.value.status_code == 401


def test_within_leeway_expiry_accepted(rsa_private_pem):
    # expired within the clock-skew leeway -> still accepted (EHR<->JHE drift)
    priv, _ = rsa_private_pem
    claims = verify_id_token(make_token(priv, exp_delta=-10), issuer=ISS, audience=AUD)
    assert claims["fhirUser"] == "Practitioner/abc"


def test_wrong_key_rejected(rsa_private_pem):
    # valid RS256 signature, but signed by a key other than the JWKS key -> reject
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with pytest.raises(IdTokenError) as e:
        verify_id_token(make_token(other_pem), issuer=ISS, audience=AUD)
    assert e.value.status_code == 401


def test_hs256_token_rejected(rsa_private_pem):
    # algorithm-confusion: a symmetric-signed token must never validate
    priv, _ = rsa_private_pem
    hs_token = make_token(priv, alg="HS256", key="shared-secret")
    with pytest.raises(IdTokenError) as e:
        verify_id_token(hs_token, issuer=ISS, audience=AUD)
    assert e.value.status_code == 401


def test_parse_fhir_user_relative():
    assert parse_fhir_user("Practitioner/abc") == ("Practitioner", "abc")


def test_parse_fhir_user_absolute():
    assert parse_fhir_user("https://ehr.example.org/fhir/Practitioner/abc") == ("Practitioner", "abc")


def test_parse_fhir_user_malformed():
    with pytest.raises(IdTokenError):
        parse_fhir_user("nope")


def test_alg_none_rejected(rsa_private_pem):
    import time as _t
    now = int(_t.time())
    claims = {"iss": ISS, "aud": AUD, "sub": "prac-1", "fhirUser": "Practitioner/abc",
              "iat": now, "exp": now + 3600}
    none_token = jwt.encode(claims, "", algorithm="none")
    with pytest.raises(IdTokenError) as e:
        verify_id_token(none_token, issuer=ISS, audience=AUD)
    assert e.value.status_code == 401


def test_http_jwks_uri_rejected(monkeypatch):
    """A jwks_uri that uses http:// (not https://) must be rejected."""
    class _FakeResponse:
        ok = True
        def json(self):
            return {"jwks_uri": "http://ehr.example.org/jwks"}

    monkeypatch.setattr(requests, "get", lambda url, **kwargs: _FakeResponse())
    with pytest.raises(IdTokenError) as e:
        discover_jwks_uri(ISS)
    assert e.value.status_code == 502


def test_non_json_discovery_rejected(monkeypatch):
    """A 200 discovery response that isn't JSON must not crash -> 502."""
    class _FakeResponse:
        ok = True
        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(requests, "get", lambda url, **kwargs: _FakeResponse())
    with pytest.raises(IdTokenError) as e:
        discover_jwks_uri(ISS)
    assert e.value.status_code == 502
