# Stops Django reading and writing JheUser.identifier and Practitioner.identifier without dropping
# them. Fly runs migrate before it swaps machines, so the previous release keeps selecting these
# columns for about a minute; 0052 drops them in a later release, once no running code knows them.
# JheUser.identifier gets an empty-string database default so inserts that leave it out still work
# and the column never holds NULL, which keeps rolling back to the previous release safe.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0050_practitioneridentifier"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.AlterField(
                    model_name="jheuser",
                    name="identifier",
                    field=models.CharField(db_default=""),
                ),
            ],
            state_operations=[
                migrations.RemoveField(model_name="jheuser", name="identifier"),
                migrations.RemoveField(model_name="practitioner", name="identifier"),
            ],
        ),
    ]
