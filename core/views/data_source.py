import logging

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.models import CodeableConcept, DataSource, DataSourceSupportedScope
from core.pagination import CustomPageNumberPagination
from core.permissions import IfUserCan
from core.serializers import (
    CodeableConceptSerializer,
    DataSourceSerializer,
    DataSourceSupportedScopeSerializer,
)

logger = logging.getLogger(__name__)


class DataSourceViewSet(ModelViewSet):
    serializer_class = DataSourceSerializer
    model_class = DataSource
    pagination_class = CustomPageNumberPagination

    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        """
        if self.action in ["create", "destroy", "update", "partial_update"]:
            return [IfUserCan("data_source.manage")()]
        return [permission() for permission in self.permission_classes]

    # this will never be large
    def get_queryset(self):
        if self.detail:
            # Detail view must return a queryset
            return DataSource.objects.all()
        else:
            return DataSource.data_sources_with_scopes()

    @action(detail=False, methods=["GET"])
    def all_scopes(self, request):
        codeable_concepts = CodeableConcept.objects.order_by("text")
        serializer = CodeableConceptSerializer(codeable_concepts, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["GET", "POST", "DELETE"])
    def supported_scopes(self, request, pk):
        if request.method == "GET":
            scopes = DataSourceSupportedScope.objects.filter(data_source_id=pk).order_by("id")
            serializer = DataSourceSupportedScopeSerializer(scopes, many=True)
            return Response(serializer.data)
        else:
            scope_code_id = request.data.get("scope_code_id")
            if scope_code_id is None:
                raise ValidationError("scope_code_id is required.")

            response = None
            if request.method == "POST":
                response = DataSourceSupportedScope.objects.create(data_source_id=pk, scope_code_id=scope_code_id)
            else:
                response = DataSourceSupportedScope.objects.filter(
                    data_source_id=pk, scope_code_id=scope_code_id
                ).delete()

            if request.method == "POST":
                return Response(
                    DataSourceSupportedScopeSerializer(response, many=False).data, status=status.HTTP_201_CREATED
                )
            return Response(status=status.HTTP_204_NO_CONTENT)
