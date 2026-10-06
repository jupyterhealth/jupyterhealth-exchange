"""Guards for the identifier migrations 0050 to 0052.

0050's copy is frozen history but still runs on every database upgrading from before it, so its
hardcoded OW system must keep matching the app constant. 0052 must actually drop the old columns.
"""

import importlib

from django.db import connection

from core.models import OW_USER_ID_SYSTEM

migration = importlib.import_module("core.migrations.0050_practitioneridentifier")


def test_migration_uses_the_app_ow_system():
    assert migration.OW_USER_ID_SYSTEM == OW_USER_ID_SYSTEM


def test_identifier_columns_are_dropped(db):
    with connection.cursor() as cursor:
        jhe_user_columns = {c.name for c in connection.introspection.get_table_description(cursor, "core_jheuser")}
        practitioner_columns = {
            c.name for c in connection.introspection.get_table_description(cursor, "core_practitioner")
        }

    assert "identifier" not in jhe_user_columns
    assert "identifier" not in practitioner_columns
