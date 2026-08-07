# One aux row per upstream record — re-running a patient-access Connect (or re-POSTing the
# same EHR record through /fhir-import) must refresh, not duplicate. create_aux_resource
# upserts on (fhir_source, resource_type, fhir_resource_id); this migration first collapses
# any duplicates deployments already accumulated (keeping the most recently updated row),
# then adds the unique constraint that backs the upsert against races. Rows with no upstream
# id cannot be identified across imports and are exempt from the constraint.

import json

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
        losers = list(rows.exclude(pk=keeper.pk))
        # The #584 index-refs pass rewrites same-source references to JHE UUIDs, so another
        # row may point at a loser ("Encounter/<loser-uuid>"). Repoint those at the keeper
        # before deleting, or they dangle permanently. Ref indexing is per-source, so only
        # sibling rows of the same fhir_source need scanning. .update() avoids auto_now.
        replacements = {
            f"{group['resource_type']}/{row.pk}": f"{group['resource_type']}/{keeper.pk}" for row in losers
        }
        loser_pks = {row.pk for row in losers}
        siblings = FhirAuxResource.objects.filter(fhir_source_id=group["fhir_source_id"]).exclude(
            pk__in=loser_pks | {keeper.pk}
        )
        for sibling in siblings.iterator():
            blob = json.dumps(sibling.fhir_data)
            new_blob = blob
            for old, new in replacements.items():
                new_blob = new_blob.replace(old, new)
            if new_blob != blob:
                FhirAuxResource.objects.filter(pk=sibling.pk).update(fhir_data=json.loads(new_blob))
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
