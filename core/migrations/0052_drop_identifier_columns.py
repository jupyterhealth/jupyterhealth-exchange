# Drops the two columns 0051 stopped Django from reading. It must ship in a later release than 0051
# so no running code still selects them when they disappear; a release older than 0051 must be
# stopped before this runs. The reverse re-adds them with the type and default 0051 left them
# with, so a database can still be migrated back, but they come back empty.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0051_forget_identifier_columns"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE core_jheuser DROP COLUMN IF EXISTS identifier;",
            reverse_sql="ALTER TABLE core_jheuser ADD COLUMN IF NOT EXISTS identifier varchar NOT NULL DEFAULT '';",
        ),
        migrations.RunSQL(
            sql="ALTER TABLE core_practitioner DROP COLUMN IF EXISTS identifier;",
            reverse_sql="ALTER TABLE core_practitioner ADD COLUMN IF NOT EXISTS identifier varchar NULL;",
        ),
    ]
