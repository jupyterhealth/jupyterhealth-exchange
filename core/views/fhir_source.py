from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.fhir.ref_indexing import index_fhir_source_refs
from core.models import EhrBrand, FhirSource, Patient
from core.serializers import FhirSourceSerializer


class FhirSourceViewSet(ModelViewSet):
    """CRUD for registering and managing FhirSources, by a patient or by a practitioner for a patient.

    A patient user only ever sees and edits their own sources, and ``patient`` is their own on
    create. A practitioner sees the sources of the patients they share an organization with, and
    names the patient on create with ``patient_id``. Every practitioner write (create, update,
    delete, index_refs) also needs the "patient.manage_data" permission for that patient -- the
    same pair of checks the FHIR write path applies when they upload under the source.
    """

    serializer_class = FhirSourceSerializer
    model_class = FhirSource

    def get_queryset(self):
        user = self.request.user
        if user.is_patient():
            patient = user.get_patient()
            if patient is None:
                return FhirSource.objects.none()
            sources = FhirSource.objects.filter(patient=patient)
        else:
            sources = FhirSource.objects.filter(
                patient__in=Patient.for_practitioner_organization_study(user.id).values("id")
            )
        return sources.select_related("ehr_brand_location").order_by("-last_updated")

    def _patient_for_create(self, request, patient_id):
        """The patient a new source is for: the patient user themself, or the practitioner's ``patient_id``."""
        user = request.user
        if user.is_patient():
            if patient_id is not None:
                raise ValidationError({"patient_id": "Only practitioner users can name a patient."})
            patient = user.get_patient()
            if patient is None:
                raise PermissionDenied("Current user is not a Patient.")
            return patient
        if patient_id is None:
            raise ValidationError({"patient_id": "A practitioner must name the patient the source is for."})
        self._check_can_manage(user, patient_id)
        return Patient.objects.get(pk=patient_id)

    @staticmethod
    def _check_can_manage(user, patient_id):
        # A patient that does not exist fails the access check too, so its existence is not revealed.
        if not Patient.practitioner_authorized(user.id, patient_id):
            raise PermissionDenied("Current user does not have access to this patient.")
        if not Patient.practitioner_can_manage_data(user, patient_id):
            raise PermissionDenied("Current user's role does not include permission to manage this Patient's data.")

    def _check_write(self, fhir_source):
        # Reads need only organization access (the queryset); a practitioner write also needs manage_data.
        if not self.request.user.is_patient():
            self._check_can_manage(self.request.user, fhir_source.patient_id)

    def create(self, request, *args, **kwargs):
        """Register a source, or return the patient's existing one for the same EHR brand.

        A patient has one source per EHR brand and data source, so reconnecting does not stack a second copy of every record under a fresh source -- aux rows are only unique within a source. The brand comes from the picked facility, or from ``ehr_base_url`` (the SMART ``iss``) when none was picked. A new EHR source with no label is labelled "<vendor name> - <brand name>". A source that resolves to no brand is not an EHR connection and always creates.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Not model fields: consumed here, so they must not reach serializer.save().
        base_url = serializer.validated_data.pop("ehr_base_url", "")
        patient = self._patient_for_create(request, serializer.validated_data.pop("patient_id", None))

        brand = self._resolve_brand(serializer.validated_data.get("ehr_brand_location"), base_url)
        if brand is not None and not serializer.validated_data.get("label"):
            # Patient-facing, so it names the vendor and brand rather than anything technical.
            serializer.validated_data["label"] = " - ".join(filter(None, [brand.vendor.name, brand.name]))
        if brand is not None:
            existing = (
                FhirSource.objects.filter(
                    patient=patient,
                    data_source=serializer.validated_data["data_source"],
                    ehr_brand_location__brand=brand,
                )
                .order_by("id")
                .first()
            )
            if existing is not None:
                return Response(self.get_serializer(existing).data, status=status.HTTP_200_OK)

        serializer.save(patient=patient)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @staticmethod
    def _resolve_brand(location, base_url):
        """The EhrBrand a registration is for: the picked facility's brand, else the one served at ``base_url``.

        ``base_url`` is matched with and without a trailing slash, since ``iss`` comes from the launch rather than the brands table and the two may differ only in that.
        """
        if location is not None:
            return location.brand
        base_url = (base_url or "").strip().rstrip("/")
        if not base_url:
            return None
        return (
            EhrBrand.objects.filter(Q(fhir_base_url=base_url) | Q(fhir_base_url=base_url + "/")).order_by("id").first()
        )

    def perform_update(self, serializer):
        self._check_write(serializer.instance)
        # A source never moves to another patient: every aux row under it belongs to its patient.
        if "patient_id" in serializer.validated_data:
            raise ValidationError({"patient_id": "A source's patient cannot be changed."})
        serializer.validated_data.pop("ehr_base_url", None)
        serializer.save()

    def perform_destroy(self, instance):
        self._check_write(instance)
        instance.delete()

    @action(detail=True, methods=["POST"])
    def index_refs(self, request, pk=None):
        """Rewrite this source's aux-resource references from upstream ids to JHE ids (#584).

        get_object() limits it to sources the caller can see (their own, or their organizations'
        patients'), and a practitioner also needs manage_data, since it rewrites stored records.
        Returns a summary of rows indexed and references rewritten / not found.
        """
        fhir_source = self.get_object()
        self._check_write(fhir_source)
        return Response(index_fhir_source_refs(fhir_source))
