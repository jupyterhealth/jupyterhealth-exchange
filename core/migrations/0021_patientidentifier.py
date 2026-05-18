import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0020_alter_patient_birth_date_alter_patient_name_family_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='PatientIdentifier',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('system', models.CharField(db_index=True)),
                ('value', models.CharField(db_index=True)),
                ('patient', models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.CASCADE, related_name='identifiers', to='core.patient')),
            ],
        ),
        migrations.AddConstraint(
            model_name='patientidentifier',
            constraint=models.UniqueConstraint(fields=('system', 'value'), name='core_patientidentifier_unique_system_value'),
        ),
    ]
