from django.db.models import Count
from rest_framework import serializers

from core.models import FhirSource


class FhirSourceSerializer(serializers.ModelSerializer):
    """A patient-registered upstream FHIR source; ``patient`` is set server-side from the requesting user, never the request body."""

    resource_counts = serializers.SerializerMethodField()
    facility = serializers.SerializerMethodField()

    class Meta:
        model = FhirSource
        fields = [
            "id",
            "patient",
            "data_source",
            "label",
            "ehr_brand_location",
            "facility",
            "resource_counts",
            "last_updated",
        ]
        read_only_fields = ["id", "patient", "last_updated"]

    def get_resource_counts(self, obj):
        """{resource_type: rows stored under this source}, the per-type receipt the patient pages show."""
        counts = (
            obj.aux_resources.values("resource_type")
            .annotate(n=Count("id"))
            .order_by()
            .values_list("resource_type", "n")
        )
        return dict(counts)

    def get_facility(self, obj):
        return obj.ehr_brand_location.name if obj.ehr_brand_location else ""
