import logging

from pydantic import ValidationError
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from rest_framework import status

from core.admin_pagination import AdminListMixin
from core.models import Patient, Study, StudyClient, StudyDataSource, StudyPatient, StudyScopeRequest
from core.permissions import IfUserCan
from core.serializers import (
    PatientSerializer,
    StudyClientSerializer,
    StudyDataSourceSerializer,
    StudyPatientSerializer,
    StudyScopeRequestSerializer,
    StudySerializer,
    StudyOrganizationSerializer,
)

logger = logging.getLogger(__name__)


class StudyViewSet(AdminListMixin, ModelViewSet):

    model_class = Study
    serializer_class = StudyOrganizationSerializer
    admin_query_method = Study.__dict__["for_practitioner_organization"]
    admin_count_method = Study.__dict__["count_for_practitioner_organization"]

    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        """
        if self.action in ["create", "destroy", "update", "partial_update"]:
            return [IfUserCan("study.manage_for_organization")()]
        return [permission() for permission in self.permission_classes]

    def get_serializer_class(self):
        if self.request.method == "GET":
            return StudyOrganizationSerializer
        else:
            return StudySerializer

    def get_queryset(self):
        if self.detail:
            if Study.practitioner_authorized(self.request.user.id, self.kwargs["pk"]):
                return Study.objects.filter(pk=self.kwargs["pk"])
            else:
                raise PermissionDenied("Current User does not have authorization to access this Study.")
        else:
            return Study.for_practitioner_organization(
                self.request.user.id, self.request.GET.get("organization_id", None)
            )

    @action(detail=True, methods=["GET", "POST", "DELETE"])
    def patients(self, request, pk):

        if request.method == "GET":
            serializer = PatientSerializer(Patient.for_study(self.request.user.id, pk), many=True)
            return Response(serializer.data)
        else:
            responses = []
            for patient_id in request.data["patient_ids"]:
                if request.method == "POST":
                    study = Study.objects.get(pk=pk)
                    patient = Patient.objects.get(pk=patient_id)
                    patient_organization_links = patient.organization_links.all()
                    if study.organization_id not in patient_organization_links.values_list(
                        "organization_id", flat=True
                    ):
                        raise ValidationError("Patient and study must be from the same Organization")
                    responses.append(StudyPatient.objects.create(study_id=pk, patient_id=patient_id))
                else:
                    responses.append(StudyPatient.objects.filter(study_id=pk, patient_id=patient_id).delete())

            return Response({"study_patients": StudyPatientSerializer(responses, many=True).data})

    @action(detail=True, methods=["GET", "POST", "DELETE"])
    def scope_requests(self, request, pk):

        if request.method == "GET":
            scopes = StudyScopeRequest.objects.filter(study_id=pk).order_by("id")
            serializer = StudyScopeRequestSerializer(scopes, many=True)
            return Response(serializer.data)
        else:
            response = None
            if request.method == "POST":
                response = StudyScopeRequest.objects.create(study_id=pk, scope_code_id=request.data["scope_code_id"])
            else:
                response = StudyScopeRequest.objects.filter(
                    study_id=pk, scope_code_id=request.data["scope_code_id"]
                ).delete()

            return Response(StudyScopeRequestSerializer(response, many=False).data)

    @action(detail=True, methods=["GET", "POST", "DELETE"])
    def clients(self, request, pk):

        if request.method == "GET":
            study_clients = StudyClient.objects.filter(study_id=pk).order_by("id")
            serializer = StudyClientSerializer(study_clients, many=True)
            return Response(serializer.data)
        # djangorestframework-camel-case doesn't work for this endpoint for some reason
        else:
            if request.method == "POST":
                logger.info(request.data.keys())
                StudyClient.objects.create(
                    study_id=pk, client_id=request.data.get("client_id") or request.data.get("clientId")
                )
            else:
                StudyClient.objects.filter(
                    study_id=pk, client_id=request.data.get("client_id") or request.data.get("clientId")
                ).delete()

            return Response(status=status.HTTP_200_OK)

    @action(detail=True, methods=["GET", "POST", "DELETE"])
    def data_sources(self, request, pk):

        if request.method == "GET":
            study_data_sources = StudyDataSource.objects.filter(study_id=pk).order_by("id")
            serializer = StudyDataSourceSerializer(study_data_sources, many=True)
            return Response(serializer.data)
        else:
            if request.method == "POST":
                logger.info(request.data.keys())
                StudyDataSource.objects.create(study_id=pk, data_source_id=request.data["data_source_id"])
            else:
                StudyDataSource.objects.filter(study_id=pk, data_source_id=request.data["data_source_id"]).delete()

            return Response(status=status.HTTP_200_OK)
