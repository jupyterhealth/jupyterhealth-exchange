"""The Postgres connection's TLS options come from DB_SSLMODE and DB_SSLROOTCERT, and only
when set, so an unset environment keeps libpq's own default."""

from jhe.settings import database_tls_options


def test_unset_leaves_libpq_defaults():
    assert database_tls_options(None, None) == {}
    assert database_tls_options("", "") == {}


def test_mode_alone():
    assert database_tls_options("require", None) == {"sslmode": "require"}


def test_verified():
    assert database_tls_options("verify-full", "/etc/ssl/certs/db-ca-bundle.pem") == {
        "sslmode": "verify-full",
        "sslrootcert": "/etc/ssl/certs/db-ca-bundle.pem",
    }
