import json

import humps
from django.core.exceptions import BadRequest
from rest_framework import serializers
from fhir.resources.observation import Observation as FHIRObservation
from fhir.resources.patient import Patient as FHIRPatient

from core.models import (
    ClientDataSource,
    CodeableConcept,
    DataSource,
    DataSourceSupportedScope,
    JheSetting,
    Observation,
    Organization,
    JheUser,
    Patient,
    Study,
    StudyClient,
    StudyDataSource,
    StudyPatient,
    StudyPatientScopeConsent,
    StudyScopeRequest,
    PractitionerOrganization,
)

from oauth2_provider.models import get_application_model


class PractitionerOrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PractitionerOrganization
        fields = ["id", "organization", "practitioner", "role"]
        depth = 1


class PatientOrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "organization", "patient"]
        depth = 1


class OrganizationSerializer(serializers.ModelSerializer):
    current_user_role = serializers.SerializerMethodField()

    def to_representation(self, instance):
        self.fields["children"] = OrganizationSerializer(many=True, read_only=True)
        return super(OrganizationSerializer, self).to_representation(instance)

    class Meta:
        model = Organization
        fields = ["id", "name", "type", "part_of", "current_user_role"]

    def get_current_user_role(self, obj):
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            if request.user.is_superuser:
                return "super_user"
            try:
                practitioner = request.user.practitioner_profile
            except AttributeError:
                return None

            link = PractitionerOrganization.objects.filter(practitioner=practitioner, organization=obj).first()
            return link.role if link else None
        return None


class OrganizationWithoutLineageSerializer(serializers.ModelSerializer):

    class Meta:
        model = Organization
        fields = ["id", "name", "type"]


class OrganizationUsersSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()

    class Meta:
        model = JheUser
        fields = ["id", "email", "first_name", "last_name", "role"]

    def get_role(self, user):
        if org_id := self.context.get("organization_id"):
            link = PractitionerOrganization.objects.filter(practitioner__jhe_user=user, organization_id=org_id).first()
            return link.role if link else None
        return None


class PatientSerializer(serializers.ModelSerializer):

    telecom_email = serializers.SerializerMethodField()
    organizations = serializers.SerializerMethodField()

    def get_telecom_email(self, obj):
        if obj.telecom_email:
            return obj.telecom_email
        else:
            return obj.jhe_user.email

    def get_organizations(self, obj):
        organizations = obj.organizations.all()
        return OrganizationSerializer(organizations, many=True).data

    class Meta:
        model = Patient
        fields = [
            "id",
            "jhe_user_id",
            "identifier",
            "name_family",
            "name_given",
            "birth_date",
            "telecom_phone",
            "telecom_email",
            "organizations",
        ]


class PractitionerSerializer(serializers.ModelSerializer):

    telecom_email = serializers.SerializerMethodField()
    organizations = serializers.SerializerMethodField()

    def get_telecom_email(self, obj):
        return obj.jhe_user.email

    def get_organizations(self, obj):
        organizations = obj.organizations.all()
        return OrganizationSerializer(organizations, many=True).data

    class Meta:
        model = Patient
        fields = [
            "id",
            "jhe_user_id",
            "identifier",
            "name_family",
            "name_given",
            "telecom_phone",
            "telecom_email",
            "organizations",
        ]


class JheUserSerializer(serializers.ModelSerializer):

    patient = PatientSerializer(many=False, read_only=True)

    class Meta:
        model = JheUser
        fields = ["id", "email", "first_name", "last_name", "patient", "user_type", "is_superuser"]


class StudySerializer(serializers.ModelSerializer):

    class Meta:
        model = Study
        fields = ["id", "name", "description", "organization", "icon_url"]


class StudyOrganizationSerializer(serializers.ModelSerializer):

    organization = OrganizationWithoutLineageSerializer(many=False, read_only=True)

    class Meta:
        model = Study
        fields = ["id", "name", "description", "organization", "icon_url"]


class StudyPatientSerializer(serializers.ModelSerializer):

    class Meta:
        model = StudyPatient
        fields = ["id", "study", "patient"]
        depth = 1


class StudyScopeRequestSerializer(serializers.ModelSerializer):

    class Meta:
        model = StudyScopeRequest
        fields = ["id", "study", "scope_code"]
        depth = 1


class StudyPatientScopeConsentSerializer(serializers.ModelSerializer):

    class Meta:
        model = StudyPatientScopeConsent
        fields = ["id", "study_patient", "scope_code", "consented", "consented_time"]
        depth = 1


class CodeableConceptSerializer(serializers.ModelSerializer):

    class Meta:
        model = CodeableConcept
        fields = ["id", "coding_system", "coding_code", "text"]


class DataSourceSerializer(serializers.ModelSerializer):

    supported_scopes = CodeableConceptSerializer(many=True, read_only=True)

    class Meta:
        model = DataSource
        fields = ["id", "name", "type", "supported_scopes"]


Application = get_application_model()


# !!! NB: weird stuff is going on here with how djangorestframework-camel-case selectively transforms some fields but not all
# Do not make any changes without manual testing
class ClientSerializer(serializers.ModelSerializer):

    clientId = serializers.CharField(source="client_id", required=False)
    invitationUrl = serializers.CharField(
        source="invitation_url", required=False, allow_blank=True, allow_null=True, write_only=True
    )
    codeVerifier = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, write_only=True
    )  # needs special treatment

    class Meta:
        model = Application
        # expose camelCase fields to the client
        fields = ["id", "name", "clientId", "codeVerifier", "invitationUrl"]

    def to_representation(self, instance):
        data = {
            "id": instance.id,
            "name": instance.name,
            "clientId": instance.client_id,
        }

        code_verifier_setting = JheSetting.objects.filter(setting_id=instance.id, key="client.code_verifier").first()
        data["codeVerifier"] = code_verifier_setting.get_value() if code_verifier_setting else None

        invitation_url_setting = JheSetting.objects.filter(setting_id=instance.id, key="client.invitation_url").first()
        data["invitationUrl"] = invitation_url_setting.get_value() if invitation_url_setting else None

        grants = getattr(instance, "patient_grants", None)
        code_verifier = getattr(instance, "code_verifier", None)
        invitation_url = getattr(instance, "invitation_url", None)

        # prevent None / empty list explosions
        grant_code = None
        if grants:
            # works for list/tuple/queryset
            first = grants[0] if hasattr(grants, "__getitem__") else grants.first()
            grant_code = getattr(first, "code", None)

        data["invitationLink"] = (
            Patient.construct_invitation_link(invitation_url, instance.client_id, grant_code, code_verifier)
            if grant_code
            else None
        )

        return data

    def _upsert_setting(self, app_id: int, key: str, value):
        # treat "" as delete
        if value is None or value == "":
            JheSetting.objects.filter(setting_id=app_id, key=key).delete()
            return

        obj, _ = JheSetting.objects.update_or_create(
            setting_id=app_id,
            key=key,
            defaults={"value_type": "string"},
        )
        obj.set_value("string", value)
        obj.save()

    def create(self, validated_data):
        print("validated_data keys:", sorted(validated_data.keys()))
        code_verifier = validated_data.pop("codeVerifier", None)
        invitation_url = validated_data.pop("invitation_url", None)
        if invitation_url is None:
            invitation_url = self.initial_data.get("invitation_url")

        app = super().create(validated_data)

        if code_verifier is not None:
            self._upsert_setting(app.id, "client.code_verifier", code_verifier)
        if invitation_url is not None:
            self._upsert_setting(app.id, "client.invitation_url", invitation_url)

        return app

    def update(self, instance, validated_data):
        code_verifier = validated_data.pop("codeVerifier", None)
        invitation_url = validated_data.pop("invitation_url", None)
        if invitation_url is None:
            invitation_url = self.initial_data.get("invitation_url")

        app = super().update(instance, validated_data)

        if code_verifier is not None:
            self._upsert_setting(app.id, "client.code_verifier", code_verifier)
        if invitation_url is not None:
            self._upsert_setting(app.id, "client.invitation_url", invitation_url)

        return app


class ClientDataSourceSerializer(serializers.ModelSerializer):

    class Meta:
        model = ClientDataSource
        fields = ["id", "client_id", "data_source_id"]
        depth = 1


class StudyClientSerializer(serializers.ModelSerializer):

    class Meta:
        model = StudyClient
        fields = ["id", "study", "client"]
        depth = 1


class DataSourceSupportedScopeSerializer(serializers.ModelSerializer):

    class Meta:
        model = DataSourceSupportedScope
        fields = ["id", "data_source", "scope_code"]
        depth = 1


class StudyDataSourceSerializer(serializers.ModelSerializer):

    class Meta:
        model = StudyDataSource
        fields = ["id", "study", "data_source"]
        depth = 1


class StudyPendingConsentsSerializer(serializers.ModelSerializer):

    organization = OrganizationWithoutLineageSerializer(many=False, read_only=True)
    data_sources = DataSourceSerializer(many=True, read_only=True)
    pending_scope_consents = serializers.JSONField()

    class Meta:
        model = Study
        fields = [
            "id",
            "name",
            "description",
            "organization",
            "data_sources",
            "pending_scope_consents",
        ]


class StudyConsentsSerializer(serializers.ModelSerializer):

    organization = OrganizationWithoutLineageSerializer(many=False, read_only=True)
    data_sources = DataSourceSerializer(many=True, read_only=True)
    scope_consents = serializers.JSONField()

    class Meta:
        model = Study
        fields = [
            "id",
            "name",
            "description",
            "organization",
            "data_sources",
            "scope_consents",
        ]


class ObservationSerializer(serializers.ModelSerializer):

    patient_name_family = serializers.CharField()
    patient_name_given = serializers.CharField()
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
            "codeable_concept_id",
            "coding_system",
            "coding_code",
            "coding_text",
            "last_updated",
            "value_attachment_data",
        ]


class ObservationWithoutDataSerializer(serializers.ModelSerializer):

    class Meta:
        model = Observation
        fields = ["id", "subject_patient", "codeable_concept", "last_updated"]


# Why value is JSONField?
# - Can handle "50", 50, true, "true", objects, arrays, etc.
# - Model method handles coercion based on value_type.

# Why resolved_value instead of value?
# - Because DRF can’t have the same field name be both write-only and read-only cleanly.


class JheSettingSerializer(serializers.ModelSerializer):
    value = serializers.JSONField(write_only=True, required=False)
    resolved_value = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = JheSetting
        fields = [
            "id",
            "key",
            "setting_id",
            "value_type",
            "value",  # input
            "resolved_value",  # output
            "last_updated",
        ]

    def get_resolved_value(self, obj):
        return obj.get_value()

    def create(self, validated_data):
        value = validated_data.pop("value", None)
        value_type = validated_data.get("value_type")

        obj = JheSetting(**validated_data)
        if value is not None:
            obj.set_value(value_type, value)
        else:
            # If you require value on create, enforce here:
            raise serializers.ValidationError({"value": "This field is required."})

        obj.save()
        return obj

    def update(self, instance, validated_data):
        value = validated_data.pop("value", None)

        # allow changing type/value together
        new_value_type = validated_data.get("value_type", instance.value_type)

        for attr, v in validated_data.items():
            setattr(instance, attr, v)

        if value is not None:
            instance.set_value(new_value_type, value)
        elif "value_type" in validated_data:
            # If they changed type but didn't supply value, that's usually an error
            raise serializers.ValidationError({"value": "Required when changing value_type."})

        instance.save()
        return instance


class FHIRObservationSerializer(serializers.ModelSerializer):

    # top-level fields not in table
    resource_type = serializers.CharField()
    id = serializers.CharField()  # cast as string as per spec
    meta = serializers.JSONField()
    identifier = serializers.JSONField(required=False)
    # status in model
    subject = serializers.JSONField()
    code = serializers.JSONField()
    value_attachment = serializers.JSONField()

    class Meta:
        model = Observation
        fields = [
            "resource_type",
            "id",
            "meta",
            "identifier",
            "status",
            "subject",
            "code",
            "value_attachment",
        ]

    def to_representation(self, record):
        as_dict = super().to_representation(record)
        # validate
        try:
            FHIRObservation.parse_obj(humps.camelize(as_dict))
        except Exception as e:
            raise BadRequest(e)
        return as_dict


class FHIRBundledObservationSerializer(serializers.Serializer):
    # TBD: full_url = serializers.CharField()
    resource = FHIRObservationSerializer(required=False, read_only=True, source="*")

    def to_representation(self, record):
        # the raw query in `fhir_search` doesn't deserialize everything
        # (e.g. jsonb are still strings)
        # so we need to deserialize some by hand
        # Extra handling if list can potentially contain nulls
        record.meta = json.loads(record.meta)
        record.identifier = list(filter(lambda item: item is not None, json.loads(record.identifier)))
        if len(record.identifier) == 0:
            del record.identifier
        record.subject = json.loads(record.subject)
        record.code = json.loads(record.code)
        record.value_attachment = json.loads(record.value_attachment)
        return super().to_representation(record)


class FHIRPatientSerializer(serializers.ModelSerializer):

    # top-level fields not in table
    resource_type = serializers.CharField()
    id = serializers.CharField()  # cast as string as per spec
    meta = serializers.JSONField()
    identifier = serializers.JSONField(required=False)
    name = serializers.JSONField()
    # birth_date in model
    telecom = serializers.JSONField()

    class Meta:
        model = Patient
        fields = [
            "resource_type",
            "id",
            "meta",
            "identifier",
            "name",
            "birth_date",
            "telecom",
        ]

    def to_representation(self, record):
        as_dict = super().to_representation(record)
        # validate
        try:
            FHIRPatient.parse_obj(humps.camelize(as_dict))
        except Exception as e:
            raise BadRequest(e)
        return as_dict


class FHIRBundledPatientSerializer(serializers.Serializer):
    # full_url = serializers.CharField()
    resource = FHIRPatientSerializer(required=False, read_only=True, source="*")

    def to_representation(self, record):
        # jsonb in raw is not automagically cast
        record.meta = json.loads(record.meta)
        record.identifier = json.loads(record.identifier)
        if len(record.identifier) == 0:
            del record.identifier
        record.name = json.loads(record.name)
        record.telecom = json.loads(record.telecom)
        return super().to_representation(record)


class FHIRBundleSerializer(serializers.Serializer):
    _ = serializers.JSONField()
