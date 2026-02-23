import inspect
import logging

from rest_framework import status
from rest_framework.viewsets import ModelViewSet
from core.serializers import (
    FHIRBundledObservationSerializer,
    FHIRObservationSerializer,
    ObservationSerializer,
)
from core.models import Observation, Study
from core.admin_pagination import CustomPageNumberPagination
from core.fhir_pagination import FHIRBundlePagination
from core.views.fhir_base import FHIRBase
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied

logger = logging.getLogger(__name__)


class ObservationViewSet(ModelViewSet):
    model_class = Observation
    serializer_class = ObservationSerializer
    pagination_class = CustomPageNumberPagination

    supported_query_params = {
        key
        for key in inspect.signature(Observation.for_practitioner_organization_study_patient).parameters
        if key not in {"jhe_user_id"}
    }

    def get_queryset(self):
        return Observation.for_practitioner_organization_study_patient(
            self.request.user.id,
            **{key: value for key, value in self.request.query_params.items() if key in self.supported_query_params},
        )


class FHIRObservationViewSet(ModelViewSet):
    pagination_class = FHIRBundlePagination

    def get_serializer_class(self):
        if self.request.method == "GET":
            return FHIRBundledObservationSerializer
        else:
            return FHIRObservationSerializer

    def get_queryset(self):
        # GET /Observation?patient._has:Group:member:_id=<group-id>
        study_id = self.request.GET.get("patient._has:_group:member:_id", None)
        if study_id is None:  # TBD: remove this
            study_id = self.request.GET.get("_has:_group:member:_id", None)

        patient_id = self.request.GET.get("patient", None)
        patient_identifier_system_and_value = self.request.GET.get("patient.identifier", None)
        coding_system_and_value = self.request.GET.get("code", None)

        if not (study_id or patient_id or patient_identifier_system_and_value):
            raise ValidationError(
                "Request parameter patient._has:Group:member:_id=<study_id> or"
                " patient=<patient_id> or patient.identifier=<system>|<value> must be provided."
            )

        if study_id and (not Study.practitioner_authorized(self.request.user.id, study_id)):
            raise PermissionDenied("Current User does not have authorization to access this Study.")

        if study_id and patient_id and (not Study.has_patient(study_id, patient_id)):
            raise ValidationError("The requested Patient is not part of the specified Study.")

        coding_system = None
        coding_value = None
        if coding_system_and_value:
            coding_system, _, coding_value = coding_system_and_value.partition("|")
            # TBD 400 for formatting error

        patient_identifier_system = None
        patient_identifier_value = None
        if patient_identifier_system_and_value:
            patient_identifier_system, _, patient_identifier_value = patient_identifier_system_and_value.partition("|")
            # TBD 400 for formatting error

        return Observation.fhir_search(
            self.request.user.id,
            study_id=study_id,
            patient_id=patient_id,
            patient_identifier_system=patient_identifier_system,
            patient_identifier_value=patient_identifier_value,
            coding_system=coding_system,
            coding_code=coding_value,
        )

    def create(self, request):
        observation = None
        try:
            observation = Observation.fhir_create(request.data, request.user)
            logger.debug(f"created observation: {observation}")
        # TBD: except PermissionDenied:
        except Exception as e:
            logger.error(f"error in creating observation: {e}")
            return Response(FHIRBase.error_outcome(str(e)), status=status.HTTP_400_BAD_REQUEST)

        fhir_observation = Observation.fhir_search(
            self.request.user.id, None, None, None, None, None, None, observation.id
        )[0]
        serializer = FHIRObservationSerializer(fhir_observation, many=False)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
