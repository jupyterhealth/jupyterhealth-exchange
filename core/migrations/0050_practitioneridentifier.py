# Gives Practitioner the external-identifier table Patient has had since 0021. JheUser.identifier
# was one free-text slot shared by the Open Wearables link, the EHR practitioner id that token
# exchange matches on and the SAML uid, so each writer overwrote the others.
#
# The copy keeps what is still read. An "ow:<uuid>" link moves to the user's Patient. Any other
# practitioner value is copied once per trusted token-exchange issuer, which is exactly what token
# exchange could match before; a value two users share is skipped because it already returned
# 404. Values on users without a Practitioner (the seed's patient "fhir-NNN" values, demo emails)
# and blanks are dropped.

from collections import Counter

import django.db.models.deletion
from django.db import migrations, models

OW_USER_ID_SYSTEM = "https://jupyterhealth.org/fhir/identifier/ow-user-id"
OW_PREFIX = "ow:"


def _copy_ow_links(apps):
    JheUser = apps.get_model("core", "JheUser")
    Patient = apps.get_model("core", "Patient")
    PatientIdentifier = apps.get_model("core", "PatientIdentifier")
    for user in JheUser.objects.filter(identifier__startswith=OW_PREFIX).order_by("id"):
        ow_user_id = user.identifier.removeprefix(OW_PREFIX)
        patient = Patient.objects.filter(jhe_user=user).first()
        if ow_user_id in ("", "None") or patient is None:
            continue
        PatientIdentifier.objects.get_or_create(
            system=OW_USER_ID_SYSTEM, value=ow_user_id, defaults={"patient": patient}
        )


def _read_trusted_issuers(apps):
    JheSetting = apps.get_model("core", "JheSetting")
    setting = JheSetting.objects.filter(key="auth.sof.trusted_issuers").first()
    issuers = (setting.value_json if setting else None) or []
    return sorted({issuer.rstrip("/") for issuer in issuers})


def _copy_practitioner_ids(apps):
    JheUser = apps.get_model("core", "JheUser")
    Practitioner = apps.get_model("core", "Practitioner")
    PractitionerIdentifier = apps.get_model("core", "PractitionerIdentifier")
    issuers = _read_trusted_issuers(apps)
    if not issuers:
        return
    users_per_value = Counter(JheUser.objects.exclude(identifier="").values_list("identifier", flat=True))
    practitioners = (
        Practitioner.objects.select_related("jhe_user")
        .exclude(jhe_user__identifier="")
        .exclude(jhe_user__identifier__startswith=OW_PREFIX)
        .order_by("id")
    )
    for practitioner in practitioners:
        value = practitioner.jhe_user.identifier
        if users_per_value[value] > 1:
            continue
        for issuer in issuers:
            PractitionerIdentifier.objects.get_or_create(
                system=issuer, value=value, defaults={"practitioner": practitioner}
            )


def copy_identifiers(apps, schema_editor):
    _copy_ow_links(apps)
    _copy_practitioner_ids(apps)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0049_ehr_vendor"),
    ]

    operations = [
        migrations.CreateModel(
            name="PractitionerIdentifier",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("system", models.CharField(db_index=True)),
                ("value", models.CharField(db_index=True)),
                (
                    "practitioner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="identifiers",
                        to="core.practitioner",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="practitioneridentifier",
            constraint=models.UniqueConstraint(
                fields=("system", "value"), name="core_practitioneridentifier_unique_system_value"
            ),
        ),
        migrations.RunPython(copy_identifiers, migrations.RunPython.noop),
    ]
