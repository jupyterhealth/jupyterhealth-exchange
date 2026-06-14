import inspect
import logging
from datetime import datetime

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import get_random_string
from oauth2_provider.models import get_application_model
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.models import (
    CodeableConcept,
    JheUser,
    Observation,
    Organization,
    Patient,
    PatientIdentifier,
    PatientInvitation,
    PatientOrganization,
    Practitioner,
    PractitionerOrganization,
    Study,
    StudyPatient,
    StudyPatientScopeConsent,
)
from core.pagination import CustomPageNumberPagination
from core.permissions import IfUserCan
from core.serializers import (
    ClientSerializer,
    CodeableConceptSerializer,
    PatientInvitationSerializer,
    PatientSerializer,
    StudyConsentsSerializer,
    StudyPatientScopeConsentSerializer,
    StudyPendingConsentsSerializer,
)


class PatientViewSet(ModelViewSet):
    model_class = Patient
    serializer_class = PatientSerializer
    pagination_class = CustomPageNumberPagination

    supported_query_params = {
        key
        for key in inspect.signature(Patient.for_practitioner_organization_study).parameters
        if key not in {"jhe_user_id"}
    }

    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        """
        if self.action in ["create", "destroy", "update", "partial_update"]:
            return [IfUserCan("patient.manage_for_organization")()]
        return [permission() for permission in self.permission_classes]

    def get_queryset(self):
        if self.detail:
            # if this is any practitioner (they don't need to be authorized just to view Patient details) or if this is
            # the patient accessing themselves
            if self.request.user.is_practitioner() or (
                self.request.user.get_patient() and self.request.user.get_patient().id == int(self.kwargs["pk"])
            ):
                return Patient.objects.filter(pk=self.kwargs["pk"])
            else:
                raise PermissionDenied("Current User does not have authorization to access this Patient.")
        else:
            return Patient.for_practitioner_organization_study(
                self.request.user.id,
                **{
                    key: value for key, value in self.request.query_params.items() if key in self.supported_query_params
                },
            )

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        if hasattr(request.user, "practitioner_profile"):
            practitioner = request.user.practitioner_profile
            if organization_id := request.query_params.get("organization_id"):
                practitioner.save_setting("current_organization_id", int(organization_id))
            if study_id := request.query_params.get("study_id"):
                practitioner.save_setting("current_study_id", int(study_id))
            else:
                practitioner.delete_setting("current_study_id")
        return response

    def create(self, request, *args, **kwargs):
        # Validate input up front so the user gets a clear, field-keyed 400 (rendered in the
        # modal banner) instead of a KeyError/IntegrityError 500 or a blank error (issue #521).
        # Messages are passed as a list of plain sentences so the modal banner renders them
        # cleanly (displayModalValidationError joins a list; a field-keyed dict shows an ugly
        # "field - " prefix and truncates a non-list value to its first character).
        email = (request.data.get("telecom_email") or "").strip()
        if not email:
            raise ValidationError(["Email is required to create a patient."])
        try:
            validate_email(email)
        except DjangoValidationError:
            raise ValidationError(["Enter a valid email address."])

        birth_date = request.data.get("birth_date")
        if birth_date:
            try:
                datetime.strptime(birth_date, "%Y-%m-%d")
            except (ValueError, TypeError):
                raise ValidationError(["Enter a valid birth date (YYYY-MM-DD)."])

        del request.data["telecom_email"]
        identifiers = request.data.pop("identifiers", None)

        # Reject an identifier already used by another patient up front, naming the exact
        # system|value so the admin knows which one conflicts. Doing this here (instead of
        # only catching the DB IntegrityError) gives a precise, database-agnostic message;
        # the atomic block below still guards the rare create-time race (issue #521).
        for item in identifiers or []:
            system, value = item.get("system"), item.get("value")
            if system and value and PatientIdentifier.objects.filter(system=system, value=value).exists():
                raise ValidationError(
                    [f"The external identifier {system}|{value} is already in use by another patient."]
                )

        try:
            # Wrap user + patient + identifier creation in one transaction so a conflict
            # rolls back the whole create, leaving no orphan JheUser/Patient.
            with transaction.atomic():
                jhe_users = JheUser.objects.filter(email=email)
                if jhe_users:
                    jhe_user = jhe_users[0]
                else:
                    jhe_user = JheUser(email=email)
                    jhe_user.set_password(get_random_string(length=16))
                    jhe_user.save()
                request.data["jhe_user_id"] = jhe_user.id
                patient = Patient.objects.create(**request.data)
                if identifiers is not None:
                    self._replace_patient_identifiers(patient, identifiers)
        except IntegrityError:
            # Safety net for a concurrent insert slipping past the pre-check above. Kept
            # generic so a non-identifier integrity error is not mislabeled.
            raise ValidationError(
                ["Could not save the patient because of a data conflict. Please check the values and try again."]
            )

        serializer = PatientSerializer(patient)
        return Response(serializer.data)

    def update(self, request, *args, **kwargs):
        return self._update_with_identifiers(request, partial=False, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        return self._update_with_identifiers(request, partial=True, *args, **kwargs)

    def _update_with_identifiers(self, request, partial=False, *args, **kwargs):
        identifiers = request.data.pop("identifiers", None) if hasattr(request.data, "pop") else None
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        if identifiers is not None:
            self._replace_patient_identifiers(instance, identifiers)
        instance.refresh_from_db()
        return Response(PatientSerializer(instance).data)

    @staticmethod
    def _replace_patient_identifiers(patient, identifiers):
        PatientIdentifier.objects.filter(patient=patient).delete()
        for item in identifiers:
            system = item.get("system")
            value = item.get("value")
            if system is None or value is None:
                continue
            PatientIdentifier.objects.create(patient=patient, system=system, value=value)

    def destroy(self, request, pk=None, *args, **kwargs):
        if organization_id := request.query_params.get("organization_id"):
            patient = self.get_object()
            PatientOrganization.objects.filter(patient=patient, organization_id=organization_id).delete()

            StudyPatientScopeConsent.objects.filter(
                study_patient__patient=patient,
                study_patient__study__organization_id=organization_id,
            ).delete()

            StudyPatient.objects.filter(patient=patient, study__organization_id=organization_id).delete()

            if not PatientOrganization.objects.filter(patient=patient).exists():
                Observation.objects.filter(subject_patient=patient).delete()
                patient.delete()

                if user := patient.jhe_user:
                    if not Practitioner.objects.filter(jhe_user_id=user.id).exists():
                        user.delete()

            return Response({"success": True})
        return Response({"detail": "organizationId required"}, status=status.HTTP_400_BAD_REQUEST)

    # These global methods (no premission checks) are for adding an existing patient
    # to another Organization (the exact email must be known)
    @action(detail=False, methods=["GET"])
    def global_lookup(self, request):
        email = request.GET.get("email")
        if not email:
            raise ValidationError("email parameter required")
        patients = Patient.objects.filter(jhe_user__email=email)
        return Response(PatientSerializer(patients, many=True).data, status=200)

    @action(detail=True, methods=["PATCH"])
    def global_add_organization(self, request, pk):
        organization_id = request.GET.get("organization_id")
        if not organization_id:
            raise ValidationError("organizationId parameter required")
        patient = self.get_object()
        organization = Organization.objects.get(pk=organization_id)
        if not organization:
            raise ValidationError("Organization could not be found")
        if PatientOrganization.objects.filter(organization_id=organization.id, patient_id=patient.id).exists():
            raise ValidationError("This patient is already a member of this organization.")
        PatientOrganization.objects.create(organization_id=organization.id, patient_id=patient.id)
        return Response(PatientSerializer(patient, many=False).data, status=200)

    @action(detail=True, methods=["GET"])
    def consolidated_clients(self, request, pk):
        Client = get_application_model()

        patient_clients = list(Client.objects.filter(studies__study__studypatient__patient_id=pk).distinct())

        invitations_by_client = {}
        for inv in PatientInvitation.objects.filter(patient_id=pk).order_by("-last_updated"):
            invitations_by_client.setdefault(inv.client_id, []).append(inv)

        data = []
        for client in patient_clients:
            client_data = ClientSerializer(client).data
            client_data["patient_invitations"] = PatientInvitationSerializer(
                invitations_by_client.get(client.id, []), many=True
            ).data
            data.append(client_data)

        return Response(data)

    @action(detail=False, methods=["GET"])
    def me(self, request):
        """GET /api/v1/patients/me - Returns the authenticated patient's data."""
        patient = request.user.get_patient()
        if not patient:
            raise PermissionDenied("Current user is not a patient.")
        return Response(PatientSerializer(patient).data)

    @action(detail=True, methods=["GET"], url_path="wearable-status")
    def wearable_status(self, request, pk):
        """GET /api/v1/patients/{id}/wearable-status - Check OW connection status."""
        if (not request.user.is_practitioner()) and (int(pk) != request.user.get_patient().id):
            raise PermissionDenied("The Patient does not match the current patient user.")
        patient = self.get_object()
        jhe_user = patient.jhe_user
        if not jhe_user.identifier or not jhe_user.identifier.startswith("ow:"):
            return Response({"connections": [], "connected": False})

        ow_user_id = jhe_user.identifier.removeprefix("ow:")
        ow_api_url = settings.OW_API_URL
        ow_api_key = settings.OW_API_KEY
        if not ow_api_url or not ow_api_key:
            return Response({"error": "OW integration not configured"}, status=500)

        import requests as http_requests

        try:
            ow_response = http_requests.get(
                f"{ow_api_url}/api/v1/users/{ow_user_id}/connections",
                headers={"X-Open-Wearables-API-Key": ow_api_key},
                timeout=10,
            )
        except http_requests.RequestException as e:
            logging.getLogger(__name__).warning("OW wearable-status check failed: %s", e)
            return Response({"connections": [], "connected": False, "error": "OW unreachable"})

        if ow_response.status_code != 200:
            return Response({"connections": [], "connected": False})

        connections = (
            ow_response.json() if isinstance(ow_response.json(), list) else ow_response.json().get("connections", [])
        )
        return Response({"connections": connections, "connected": len(connections) > 0})

    @action(detail=True, methods=["GET", "POST", "PATCH", "DELETE"])
    def consents(self, request, pk):
        # if this is a patient, check they are accessing their own consents
        if (not request.user.is_practitioner()) and (int(pk) != request.user.get_patient().id):
            raise PermissionDenied("The Patient does not match the current patient user.")
        patient = self.get_object()
        if request.method == "GET":
            # if this is a practitioner, check they're authorized
            if (request.user.is_practitioner()) and not Patient.practitioner_authorized(request.user.id, int(pk)):
                raise PermissionDenied("This Practitioner not authorized to access this Patient")
            if self.request.GET.get("reset") == "true":  # used for dev an testing
                reset_count = 0
                for study_patient in StudyPatient.objects.filter(patient_id=int(pk)):
                    for study_patient_scope_consent in StudyPatientScopeConsent.objects.filter(
                        study_patient_id=study_patient.id
                    ):
                        study_patient_scope_consent.delete()
                        reset_count += 1
                return Response({"reset_count": reset_count})
            patient_serializer = PatientSerializer(patient, many=False)
            studies_pending_serializer = StudyPendingConsentsSerializer(
                Study.studies_with_scopes(int(pk), True), many=True
            )
            studies_serializer = StudyConsentsSerializer(Study.studies_with_scopes(int(pk), False), many=True)
            codeable_concept_serializer = CodeableConceptSerializer(patient.consolidated_consented_scopes(), many=True)
            return Response(
                {
                    "patient": patient_serializer.data,
                    "consolidated_consented_scopes": codeable_concept_serializer.data,
                    "studies_pending_consent": studies_pending_serializer.data,
                    "studies": studies_serializer.data,
                }
            )
        else:
            # if the user is the patient; or
            # the user is a practitioner and a member or manager of the organization that owns the study and patient; or
            # the user is a super admin

            responses = []
            consented_time = timezone.now()
            patient_user = request.user.get_patient()
            is_patient_user = bool(patient_user and int(pk) == patient_user.id)
            for study_scope_consent in request.data["study_scope_consents"]:
                study_patient = StudyPatient.objects.filter(
                    study_id=study_scope_consent["study_id"], patient_id=patient.id
                ).first()
                if not request.user.is_superuser and not is_patient_user:
                    if request.user.is_practitioner():
                        if not Patient.practitioner_authorized(
                            request.user.id, int(pk), organization_id=study_patient.study.organization.id
                        ):
                            raise PermissionDenied("Practitioner doesn't have right now for patient.")
                        practitioner_org = PractitionerOrganization.objects.filter(
                            organization=study_patient.study.organization.id,
                            practitioner=request.user.practitioner_profile,
                        ).first()
                        if practitioner_org.role not in ["manager", "member"]:
                            raise PermissionDenied("Practitioner role is not valid.")
                    else:
                        raise PermissionDenied("Only Patient users can update their own consents.")

                for scope_consent in study_scope_consent["scope_consents"]:
                    scope_coding_system = scope_consent["coding_system"]
                    scope_coding_code = scope_consent["coding_code"]
                    scope_code_id = CodeableConcept.objects.get(
                        coding_system=scope_coding_system, coding_code=scope_coding_code
                    ).id

                    if request.method == "POST":
                        responses.append(
                            StudyPatientScopeConsent.objects.create(
                                study_patient_id=study_patient.id,
                                scope_code_id=scope_code_id,
                                consented=scope_consent["consented"],
                                consented_time=consented_time,
                            )
                        )
                    elif request.method == "PATCH":
                        spsc = StudyPatientScopeConsent.objects.get(
                            study_patient_id=study_patient.id,
                            scope_code_id=scope_code_id,
                        )
                        spsc.consented = scope_consent["consented"]
                        spsc.consented_time = consented_time
                        spsc.save()
                        responses.append(spsc)
                    elif request.method == "DELETE":
                        StudyPatientScopeConsent.objects.filter(
                            study_patient_id=study_patient.id,
                            scope_code_id=scope_code_id,
                        ).delete()

            # After processing all consent changes, check if any study now has
            # ALL scopes revoked. If so, disconnect the OW vendor connection
            # (best-effort - don't block the response on OW failures).
            if request.method in ("PATCH", "DELETE"):
                self._revoke_ow_connection_if_fully_unconsented(patient, request.data["study_scope_consents"])

            return Response({"study_scope_consents": StudyPatientScopeConsentSerializer(responses, many=True).data})

    def _revoke_ow_connection_if_fully_unconsented(self, patient, study_scope_consents):
        """
        After a PATCH/DELETE, check whether ALL scopes for a given study are
        now consented=false. If so, revoke the OW vendor connection (best-effort).
        """
        logger = logging.getLogger(__name__)
        jhe_user = patient.jhe_user
        if not jhe_user.identifier or not jhe_user.identifier.startswith("ow:"):
            return

        for entry in study_scope_consents:
            study_id = entry["study_id"]
            study_patient = StudyPatient.objects.filter(study_id=study_id, patient_id=patient.id).first()
            if not study_patient:
                continue
            # Check if ANY scope in this study is still consented
            still_consented = StudyPatientScopeConsent.objects.filter(
                study_patient_id=study_patient.id, consented=True
            ).exists()
            if still_consented:
                continue

            # All scopes revoked for this study - disconnect OW vendor connection
            ow_user_id = jhe_user.identifier.removeprefix("ow:")
            ow_api_url = settings.OW_API_URL
            ow_api_key = settings.OW_API_KEY
            if not ow_api_url or not ow_api_key:
                logger.warning("Cannot revoke OW connection: OW integration not configured")
                return

            import requests as http_requests

            try:
                resp = http_requests.delete(
                    f"{ow_api_url}/api/v1/users/{ow_user_id}/connections/oura",
                    headers={"X-Open-Wearables-API-Key": ow_api_key},
                    timeout=10,
                )
                if resp.status_code < 300:
                    logger.info("Revoked OW connection for user %s (study %s)", ow_user_id, study_id)
                else:
                    logger.warning("OW revoke returned %s: %s", resp.status_code, resp.text)
            except http_requests.RequestException as e:
                logger.warning("Failed to revoke OW connection: %s", e)
