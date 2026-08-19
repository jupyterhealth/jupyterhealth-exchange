# Data migration: widen the Patient Access client's SMART scopes to cover every pulled type.
#
# seed.py only writes aux_data when it *creates* the client, so an already-deployed row keeps
# the Phase-1 five-resource scope list while the client asks Epic for the full pull list and
# every new type fails with an authorization error. Updating here (instead of a post-merge
# manual step) means the deployed row is corrected the moment this release migrates.
#
# Only the "scopes" key is touched: client_id (and anything else an operator set in aux_data)
# is deployment-specific and must survive.

from django.db import migrations

# Keep in lockstep with the seed.py Patient Access client and PATIENT_ACCESS_PULLS
# (client-patient-access.js); tests/backend/test_fhir_r4_import.py guards the pairing.
NEW_SCOPES = (
    "openid profile launch/patient"
    " patient/Patient.read patient/Observation.read patient/Condition.read"
    " patient/MedicationRequest.read patient/MedicationDispense.read"
    " patient/AllergyIntolerance.read patient/Immunization.read"
    " patient/Procedure.read patient/DiagnosticReport.read"
    " patient/DocumentReference.read patient/Encounter.read"
    " patient/CarePlan.read patient/CareTeam.read patient/Goal.read"
    " patient/ServiceRequest.read"
    " patient/Device.read"
    " patient/QuestionnaireResponse.read"
)

OLD_SCOPES = (
    "openid profile launch/patient patient/Patient.read patient/Observation.read "
    "patient/Condition.read patient/MedicationRequest.read patient/AllergyIntolerance.read"
)


def _set_scopes(apps, scopes):
    JheClient = apps.get_model("core", "JheClient")
    for client in JheClient.objects.filter(application__name="Patient Access"):
        aux = client.aux_data or {}
        aux["scopes"] = scopes
        client.aux_data = aux
        client.save(update_fields=["aux_data"])


def widen_scopes(apps, schema_editor):
    _set_scopes(apps, NEW_SCOPES)


def restore_scopes(apps, schema_editor):
    _set_scopes(apps, OLD_SCOPES)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0042_ehrbrand_ehrbrandlocation"),
    ]

    operations = [
        migrations.RunPython(widen_scopes, restore_scopes),
    ]
