"""Guard: the test settings use a fast password hasher. This keeps the suite fast and
sidesteps a flaky native pbkdf2 access-violation crash on Windows; if it regresses,
user-creating tests slow down and the full suite can crash."""

from django.conf import settings


def test_tests_use_fast_password_hasher():
    assert settings.PASSWORD_HASHERS[0] == "django.contrib.auth.hashers.MD5PasswordHasher"
