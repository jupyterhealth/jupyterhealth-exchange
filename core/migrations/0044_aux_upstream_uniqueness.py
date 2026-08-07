# One aux row per upstream record — re-running a patient-access Connect (or re-POSTing the
# same EHR record through /fhir-import) must refresh, not duplicate. create_aux_resource
# upserts on (fhir_source, resource_type, fhir_resource_id); this migration first collapses
# any duplicates deployments already accumulated (keeping the most recently updated row),
# then adds the unique constraint that backs the upsert against races. Rows with no upstream
# id cannot be identified across imports and are exempt from the constraint.

from django.db import migrations, models
from django.db.models import Count


def collapse_duplicate_imports(apps, schema_editor):
    FhirAuxResource = apps.get_model("core", "FhirAuxResource")
    duplicated = (
        FhirAuxResource.objects.exclude(fhir_resource_id__isnull=True)
        .exclude(fhir_resource_id="")
        .values("fhir_source_id", "resource_type", "fhir_resource_id")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
    )
    for group in duplicated:
        rows = FhirAuxResource.objects.filter(
            fhir_source_id=group["fhir_source_id"],
            resource_type=group["resource_type"],
            fhir_resource_id=group["fhir_resource_id"],
        ).order_by("-last_updated", "-id")
        keeper = rows.first()
        rows.exclude(pk=keeper.pk).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0043_patient_access_scopes"),
    ]

    operations = [
        migrations.RunPython(collapse_duplicate_imports, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="fhirauxresource",
            constraint=models.UniqueConstraint(
                condition=models.Q(("fhir_resource_id__isnull", False), models.Q(("fhir_resource_id", ""), _negated=True)),
                fields=("fhir_source", "resource_type", "fhir_resource_id"),
                name="uniq_aux_upstream_record_per_source",
            ),
        ),
    ]
