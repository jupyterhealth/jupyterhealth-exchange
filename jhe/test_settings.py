import os

from .settings import *  # noqa

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "test_jhe_dev"),
        "USER": os.environ.get("DB_USER", "postgres"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "postgres"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}

TEST_RUNNER = "django.test.runner.DiscoverRunner"

# Fast password hashing for tests: speeds up every test that creates a user and sidesteps a
# flaky native pbkdf2 access-violation crash seen on Windows. Never use MD5 in production.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
