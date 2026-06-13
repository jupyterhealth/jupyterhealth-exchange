from rest_framework import serializers

from core.models import Practitioner

from .organization import OrganizationSerializer


class PractitionerSerializer(serializers.ModelSerializer):
    telecom_email = serializers.SerializerMethodField()
    organizations = serializers.SerializerMethodField()

    def get_telecom_email(self, obj):
        return obj.jhe_user.email

    def get_organizations(self, obj):
        organizations = obj.organizations.all()
        return OrganizationSerializer(organizations, many=True).data

    class Meta:
        model = Practitioner
        fields = [
            "id",
            "jhe_user_id",
            "name_family",
            "name_given",
            "telecom_email",
            "organizations",
        ]
