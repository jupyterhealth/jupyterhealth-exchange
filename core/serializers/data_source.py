from rest_framework import serializers

from core.models import CodeableConcept, DataSource, DataSourceSupportedScope


class CodeableConceptSerializer(serializers.ModelSerializer):
    class Meta:
        model = CodeableConcept
        fields = ["id", "coding_system", "coding_code", "text"]


class DataSourceSerializer(serializers.ModelSerializer):
    # name is nullable on the model but required when creating a data source via the API
    name = serializers.CharField()
    supported_scopes = CodeableConceptSerializer(many=True, read_only=True)

    class Meta:
        model = DataSource
        fields = ["id", "name", "type", "supported_scopes"]


class DataSourceSupportedScopeSerializer(serializers.ModelSerializer):
    class Meta:
        model = DataSourceSupportedScope
        fields = ["id", "data_source", "scope_code"]
        depth = 1
