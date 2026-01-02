import logging

from rest_framework.viewsets import ModelViewSet

from core.admin_pagination import CustomPageNumberPagination
from core.models import JheSetting
from core.permissions import IfUserCan
from core.serializers import (
    JheSettingSerializer,
)

logger = logging.getLogger(__name__)


class JheSettingViewSet(ModelViewSet):
    serializer_class = JheSettingSerializer
    queryset = JheSetting.objects.all() 

    # def get_permissions(self):
    #     """
    #     Instantiates and returns the list of permissions that this view requires.
    #     """
    #     if self.action in ["create", "destroy", "update", "partial_update"]:
    #         return [IfUserCan("jhe_setting.manage")()]
    #     return [permission() for permission in self.permission_classes]

    # this will never be large
    # def get_queryset(self):
    #     if self.detail:
    #         # Detail view must return a queryset
    #         return JheSetting.objects.all()
    #     else:
    #         return JheSetting.data_sources_with_scopes()

    # @action(detail=False, methods=["GET"])
    # def all_scopes(self, request):
    #     codeable_concepts = CodeableConcept.objects.order_by("text")
    #     serializer = CodeableConceptSerializer(codeable_concepts, many=True)
    #     return Response(serializer.data)

    # @action(detail=True, methods=["GET", "POST", "DELETE"])
    # def supported_scopes(self, request, pk):
    #     if request.method == "GET":
    #         scopes = JheSettingSupportedScope.objects.filter(data_source_id=pk).order_by("id")
    #         serializer = JheSettingSupportedScopeSerializer(scopes, many=True)
    #         return Response(serializer.data)
    #     else:
    #         response = None
    #         if request.method == "POST":
    #             response = JheSettingSupportedScope.objects.create(
    #                 data_source_id=pk, scope_code_id=request.data["scope_code_id"]
    #             )
    #         else:
    #             response = JheSettingSupportedScope.objects.filter(
    #                 data_source_id=pk, scope_code_id=request.data["scope_code_id"]
    #             ).delete()

    #         return Response(JheSettingSupportedScopeSerializer(response, many=False).data)
