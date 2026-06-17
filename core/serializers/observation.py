import base64
import json

from rest_framework import serializers

from core.fhir.config import get_resource_mapping
from core.fhir.engine import build_fhir_resource
from core.models import Observation


class ObservationSerializer(serializers.ModelSerializer):
    patient_name_family = serializers.CharField()
    patient_name_given = serializers.CharField()
    jhe_user_id = serializers.IntegerField()
    coding_system = serializers.CharField()
    coding_code = serializers.CharField()
    coding_text = serializers.CharField()

    class Meta:
        model = Observation
        fields = [
            "id",
            "subject_patient_id",
            "patient_name_family",
            "patient_name_given",
            "jhe_user_id",
            "codeable_concept_id",
            "coding_system",
            "coding_code",
            "coding_text",
            "last_updated",
            "omh_data",
        ]


class ObservationWithoutDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = Observation
        fields = ["id", "subject_patient", "codeable_concept", "last_updated"]


class FHIRObservationSerializer(serializers.Serializer):
    """Renders an Observation model instance into a FHIR R5 Observation resource.

    The shape is driven by the mapping in core/fhir/fhir_config.json. The encoding of
    valueAttachment.data to Base64 (per the FHIR Attachment spec) is not expressible in
    the config, so it is applied here after the generic mapping has run. Output is not
    validated against fhir.resources -- validation happens on the way in
    (Observation.fhir_create), not on the way out.
    """

    def to_representation(self, observation):
        mapping = get_resource_mapping("Observation")
        as_dict = build_fhir_resource(observation, "Observation", mapping)
        # valueAttachment.data must be Base64-encoded binary per FHIR. The mapping yields the
        # raw JSON object from omh_data, so encode it here (mirrors fhir_create's decode path).
        attachment = as_dict.get("valueAttachment")
        if attachment and isinstance(attachment.get("data"), (dict, list)):
            attachment["data"] = base64.b64encode(json.dumps(attachment["data"]).encode("utf-8")).decode("ascii")
        return as_dict


class FHIRBundledObservationSerializer(serializers.Serializer):
    # TBD: full_url = serializers.CharField()
    resource = FHIRObservationSerializer(required=False, read_only=True, source="*")


class FHIRBundleSerializer(serializers.Serializer):
    _ = serializers.JSONField()
