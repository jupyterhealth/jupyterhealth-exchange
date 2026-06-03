from jhe_mcp.auth import pkce


def test_challenge_is_deterministic_and_urlsafe():
    v = pkce.generate_verifier()
    c1 = pkce.challenge_from_verifier(v)
    c2 = pkce.challenge_from_verifier(v)
    assert c1 == c2
    assert "=" not in c1 and "+" not in c1 and "/" not in c1


def test_verify_accepts_matching_and_rejects_mismatch():
    v = pkce.generate_verifier()
    c = pkce.challenge_from_verifier(v)
    assert pkce.verify(v, c) is True
    assert pkce.verify("wrong-verifier", c) is False


def test_known_vector():
    # RFC 7636 Appendix B
    v = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert pkce.challenge_from_verifier(v) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
