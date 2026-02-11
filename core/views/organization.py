import logging

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.admin_pagination import CustomPageNumberPagination
from core.models import JheUser
from core.models import Organization, PractitionerOrganization, PatientOrganization
from core.permissions import IfUserCan
from core.serializers import (
    OrganizationSerializer,
    OrganizationUsersSerializer,
    StudySerializer,
    PractitionerOrganizationSerializer,
    PatientOrganizationSerializer,
)

logger = logging.getLogger(__name__)


class OrganizationViewSet(ModelViewSet):
    """
    This viewset automatically provides `list`, `create`, `retrieve`,
    `update` and `destroy` actions.
    """

    serializer_class = OrganizationSerializer
    model_class = Organization
    pagination_class = CustomPageNumberPagination

    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        """
        if self.action in ["create", "destroy", "update", "partial_update"]:
            return [IfUserCan("organization.manage_for_practitioners")()]
        return [permission() for permission in self.permission_classes]

    def get_queryset(self):
        param_part_of = self.request.query_params.get("part_of")
        if param_part_of:
            return Organization.objects.filter(part_of=param_part_of).order_by("name")
        else:
            return Organization.objects.order_by("name")

    def create(self, request, *args, **kwargs):
        is_sub_organization = bool(request.data.get("part_of"))
        if (not is_sub_organization) and (not request.user.is_superuser):
            raise PermissionDenied("You don't have permission to create a top-level organization.")

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        is_sub_organization = bool(request.data.get("part_of"))
        if is_sub_organization:
            PractitionerOrganization.objects.create(
                organization_id=serializer.data.get("id"),
                practitioner=request.user.practitioner,
                role="manager",
            )
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=False, methods=["GET"])
    def types(self, request):
        return Response(Organization.ORGANIZATION_TYPES)

    @action(detail=True, methods=["GET"])
    def tree(self, request, pk):
        parent = self.get_object()
        Organization.collect_children(parent)
        return Response(self.get_serializer(parent).data)

    def collect_children(self, parent):
        children = self.get_children(parent.id)
        for child in children:
            parent.children.append(child)
            self.collect_children(child)

    def get_children(self, parent_id):
        return Organization.objects.filter(part_of=parent_id).order_by("name")

    @action(detail=True, methods=["GET"])
    def users(self, request, pk):
        organization = self.get_object()
        users = organization.users.filter(user_type="practitioner").order_by("last_name")
        serializer = OrganizationUsersSerializer(users, many=True, context={"organization_id": organization.id})
        return Response(serializer.data)

    @action(
        detail=True,
        methods=["POST"],
        permission_classes=[IfUserCan("organization.manage_for_practitioners")],
    )
    def user(self, request, pk):
        user_type = request.data.get("user_type", "practitioner")  # Default to practitioner if not specified
        jhe_user_id = request.data.get("jhe_user_id")
        organization_partitioner_role = request.data.get("organization_partitioner_role")

        if not jhe_user_id:
            return Response({"error": "jhe_user_id is required"}, status=400)

        jhe_user = get_object_or_404(JheUser, pk=jhe_user_id)

        if user_type.lower() == "patient":
            relation = PatientOrganization.objects.create(organization_id=pk, patient_id=jhe_user_id)
            serializer = PatientOrganizationSerializer(relation)
        else:  # practitioner
            practitioner = jhe_user.practitioner
            if not practitioner:
                return Response({"error": "Practitioner not found"}, status=404)
            relation = PractitionerOrganization.objects.create(
                organization_id=pk,
                practitioner=practitioner,
                role=organization_partitioner_role,
            )
            serializer = PractitionerOrganizationSerializer(relation)
        return Response(serializer.data)

    @action(
        detail=True,
        methods=["DELETE"],
        permission_classes=[IfUserCan("organization.manage_for_practitioners")],
    )
    def remove_user(self, request, pk):
        user_type = request.data.get("user_type", "practitioner")  # Default to practitioner if not specified
        jhe_user_id = request.data.get("jhe_user_id")
        if not jhe_user_id:
            return Response({"error": "jhe_user_id is required"}, status=400)

        if user_type.lower() == "patient":
            PatientOrganization.objects.filter(organization_id=pk, patient_id=jhe_user_id).delete()
            return Response(status=204)
        else:
            jhe_user = get_object_or_404(JheUser, pk=jhe_user_id)
            practitioner = jhe_user.practitioner
            if not practitioner:
                return Response({"error": "Practitioner not found"}, status=404)
            PractitionerOrganization.objects.filter(organization_id=pk, practitioner=practitioner).delete()
            return Response(status=204)

    @action(detail=True, methods=["GET"])
    def studies(self, request, pk):
        organization = self.get_object()
        studies = organization.study_set.order_by("name")
        serializer = StudySerializer(studies, many=True)
        return Response(serializer.data)
