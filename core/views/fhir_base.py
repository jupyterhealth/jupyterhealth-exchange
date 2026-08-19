import copy
import http
import logging
import traceback

import humps
from django.core.exceptions import BadRequest, PermissionDenied
from django.db.utils import IntegrityError
from djangorestframework_camel_case.render import CamelCaseJSONRenderer
from fhir.resources.bundle import Bundle
from fhir.resources.operationoutcome import OperationOutcome
from fhir.resources.resource import Resource
from rest_framework import status as http_status
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from core.models import Observation
from core.serializers import FHIRBundleSerializer

logger = logging.getLogger(__name__)


class FHIRJSONRenderer(CamelCaseJSONRenderer):
    """The formal FHIR JSON media type (application/fhir+json).

    Generic FHIR tooling sends ``Accept: application/fhir+json``; without this
    renderer DRF's content negotiation 406s such requests. Registered alongside
    CamelCaseJSONRenderer so plain application/json keeps working, and responses
    negotiated through it carry the FHIR content type.
    """

    media_type = "application/fhir+json"


FHIR_RENDERER_CLASSES = [CamelCaseJSONRenderer, FHIRJSONRenderer]


class FHIRBase(viewsets.GenericViewSet):
    serializer_class = FHIRBundleSerializer
    renderer_classes = FHIR_RENDERER_CLASSES

    def create(self, request):
        # first validate the entire bundle
        bundle_data = copy.deepcopy(request.data)
        for entry in bundle_data["entry"]:
            if (
                "value_attachment" not in entry["resource"]
                or "data" not in entry["resource"]["value_attachment"]
                or entry["resource"]["value_attachment"]["data"] is None
            ):
                raise ValidationError("resource.valueAttachment.data must be not null.")
        fhir_bundle = Bundle.parse_obj(humps.camelize(request.data))  # noqa
        # then create each record
        response_entries = []
        for entry in request.data["entry"]:
            if entry["resource"]["resource_type"] != "Observation":
                response_entries.append(
                    FHIRBase.bundle_create_response_entry(
                        http_status.HTTP_400_BAD_REQUEST,
                        FHIRBase.error_outcome("Only Observation resourceType supported."),
                    )
                )
            if entry["request"]["method"] != "POST":
                response_entries.append(
                    FHIRBase.bundle_create_response_entry(
                        http_status.HTTP_400_BAD_REQUEST,
                        FHIRBase.error_outcome("Only POST/Create method supported."),
                    )
                )

            try:
                observation = FHIRBase._bundle_create_observation(entry["resource"], request)
                response_entries.append(
                    FHIRBase.bundle_create_response_entry(http_status.HTTP_201_CREATED, None, observation)
                )

            except IntegrityError as e:
                response_entries.append(
                    FHIRBase.bundle_create_response_entry(http_status.HTTP_409_CONFLICT, FHIRBase.error_outcome(str(e)))
                )
            except (PermissionDenied, DRFPermissionDenied) as e:
                response_entries.append(
                    FHIRBase.bundle_create_response_entry(
                        http_status.HTTP_403_FORBIDDEN, FHIRBase.error_outcome(str(e))
                    )
                )
            except (BadRequest, ValidationError) as e:
                response_entries.append(
                    FHIRBase.bundle_create_response_entry(
                        http_status.HTTP_400_BAD_REQUEST, FHIRBase.error_outcome(str(e))
                    )
                )
            except Exception as e:
                print(traceback.format_exc())
                response_entries.append(
                    FHIRBase.bundle_create_response_entry(
                        http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                        FHIRBase.error_outcome(str(e)),
                    )
                )
        return Response(
            FHIRBase.bundle_batch_response(response_entries),
            status=http_status.HTTP_200_OK,
        )

    @staticmethod
    def _bundle_create_observation(resource, request):
        # Route a bundled Observation the same way the single-resource endpoint does: an OMH
        # Observation (code system https://w3id.org/openmhealth) is persisted onto the Django
        # Observation model; any other Observation is stored in FhirAuxResource, linked to the
        # FhirSource named by the X-JHE-FHIR-Source-ID header (authoritative) or the entry's own
        # meta.source (and its patient).
        from core.fhir.config import mapped_criteria
        from core.fhir.engine import matches_criteria
        from core.views.fhir import create_aux_resource, resolve_fhir_source_context

        user = request.user
        camelized = humps.camelize(resource)
        criteria = mapped_criteria("Observation")
        if criteria is None or matches_criteria(camelized, criteria):
            return Observation.fhir_create(resource, user)

        _, fhir_source = resolve_fhir_source_context(request, user, camelized)
        return create_aux_resource("Observation", camelized, fhir_source)

    @staticmethod
    def error_outcome(message):
        data = {"issue": [{"severity": "error", "code": "processing", "diagnostics": message}]}
        return OperationOutcome(**data).dict()

    @staticmethod
    def bundle_batch_response(entries):
        data = {"type": "batch-response", "entry": entries}
        return Bundle(**data).dict()

    @staticmethod
    def bundle_create_response_entry(status, outcome=None, obj=None):
        entry = {"response": {"status": str(status) + " " + http.HTTPStatus(status).phrase}}
        if obj:
            entry["resource"] = Resource(id=str(obj.id))
        if outcome:
            entry["response"]["outcome"] = outcome
        return entry
