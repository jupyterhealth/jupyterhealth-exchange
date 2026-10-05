"""Migration 0050 copies what token exchange and Open Wearables still read from
JheUser.identifier into identifier rows, before 0051 makes Django forget the column.

The copy runs against the models as of 0050 (the last state that still has the column).
MigrationExecutor's loader rebuilds that historical state from the migration files, which is
the same view RunPython gets during a real migrate.
"""

import importlib

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from core.models import OW_USER_ID_SYSTEM

migration = importlib.import_module("core.migrations.0050_practitioneridentifier")

EHR = "https://ehr.example.org"


@pytest.fixture
def old_apps(db):
    return MigrationExecutor(connection).loader.project_state(("core", "0050_practitioneridentifier")).apps


def _create_user(old_apps, email, identifier, *, has_patient=False, has_practitioner=False):
    user = old_apps.get_model("core", "JheUser").objects.create(email=email, identifier=identifier)
    if has_patient:
        old_apps.get_model("core", "Patient").objects.create(jhe_user=user)
    if has_practitioner:
        old_apps.get_model("core", "Practitioner").objects.create(jhe_user=user)
    return user


def _trust(old_apps, *issuers):
    old_apps.get_model("core", "JheSetting").objects.create(
        key="auth.sof.trusted_issuers", value_type="json", value_json=list(issuers)
    )


def _list_patient_identifiers(old_apps):
    return set(
        old_apps.get_model("core", "PatientIdentifier").objects.values_list(
            "patient__jhe_user__email", "system", "value"
        )
    )


def _list_practitioner_identifiers(old_apps):
    return set(
        old_apps.get_model("core", "PractitionerIdentifier").objects.values_list(
            "practitioner__jhe_user__email", "system", "value"
        )
    )


def test_migration_uses_the_app_ow_system():
    assert migration.OW_USER_ID_SYSTEM == OW_USER_ID_SYSTEM


def test_ow_link_moves_to_the_patient_without_its_prefix(old_apps):
    _create_user(old_apps, "peter@example.org", "ow:550e8400-e29b-41d4-a716-446655440000", has_patient=True)

    migration.copy_identifiers(old_apps, None)

    assert _list_patient_identifiers(old_apps) == {
        ("peter@example.org", OW_USER_ID_SYSTEM, "550e8400-e29b-41d4-a716-446655440000")
    }


def test_unusable_ow_links_are_skipped(old_apps):
    _create_user(old_apps, "empty@example.org", "ow:", has_patient=True)
    _create_user(old_apps, "none@example.org", "ow:None", has_patient=True)
    _create_user(old_apps, "no-patient@example.org", "ow:abc")

    migration.copy_identifiers(old_apps, None)

    assert _list_patient_identifiers(old_apps) == set()


def test_ow_link_already_taken_stays_with_the_first_patient(old_apps):
    _create_user(old_apps, "first@example.org", "ow:shared", has_patient=True)
    _create_user(old_apps, "second@example.org", "ow:shared", has_patient=True)

    migration.copy_identifiers(old_apps, None)

    assert _list_patient_identifiers(old_apps) == {("first@example.org", OW_USER_ID_SYSTEM, "shared")}


def test_practitioner_value_is_copied_once_per_trusted_issuer(old_apps):
    _trust(old_apps, "https://ehr-a.example.org/", "https://ehr-b.example.org", "https://ehr-b.example.org/")
    _create_user(old_apps, "doc@example.org", "Practitioner-123", has_practitioner=True)

    migration.copy_identifiers(old_apps, None)

    assert _list_practitioner_identifiers(old_apps) == {
        ("doc@example.org", "https://ehr-a.example.org", "Practitioner-123"),
        ("doc@example.org", "https://ehr-b.example.org", "Practitioner-123"),
    }


def test_practitioner_values_are_not_copied_without_trusted_issuers(old_apps):
    _create_user(old_apps, "doc@example.org", "Practitioner-123", has_practitioner=True)

    migration.copy_identifiers(old_apps, None)

    assert _list_practitioner_identifiers(old_apps) == set()


def test_practitioner_value_shared_by_two_users_is_skipped(old_apps):
    _trust(old_apps, EHR)
    _create_user(old_apps, "doc@example.org", "dup", has_practitioner=True)
    _create_user(old_apps, "pat@example.org", "dup", has_patient=True)

    migration.copy_identifiers(old_apps, None)

    assert _list_practitioner_identifiers(old_apps) == set()


def test_each_value_lands_on_the_profile_it_belongs_to(old_apps):
    _trust(old_apps, EHR)
    _create_user(old_apps, "dual@example.org", "ow:abc", has_patient=True, has_practitioner=True)
    _create_user(old_apps, "seed@example.org", "fhir-666", has_patient=True)
    _create_user(old_apps, "admin@example.org", "", has_practitioner=True)

    migration.copy_identifiers(old_apps, None)

    assert _list_patient_identifiers(old_apps) == {("dual@example.org", OW_USER_ID_SYSTEM, "abc")}
    assert _list_practitioner_identifiers(old_apps) == set()


def test_copy_is_safe_to_run_twice(old_apps):
    _trust(old_apps, EHR)
    _create_user(old_apps, "peter@example.org", "ow:abc", has_patient=True)
    _create_user(old_apps, "doc@example.org", "Practitioner-123", has_practitioner=True)

    migration.copy_identifiers(old_apps, None)
    migration.copy_identifiers(old_apps, None)

    assert len(_list_patient_identifiers(old_apps)) == 1
    assert len(_list_practitioner_identifiers(old_apps)) == 1


def test_users_created_after_0051_get_an_empty_identifier(db):
    """Code that still reads the column (the old release during a deploy, or after a rollback) never meets NULL."""
    from core.models import JheUser

    user = JheUser.objects.create_user(email="new@example.org", password="x")

    with connection.cursor() as cursor:
        cursor.execute("SELECT identifier FROM core_jheuser WHERE id = %s", [user.id])
        assert cursor.fetchone() == ("",)
