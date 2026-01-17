from datetime import datetime

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.utils.crypto import get_random_string
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from urllib.parse import quote
from oauth2_provider.models import get_application_model

from core.admin_pagination import AdminListMixin
from core.context_processors import _get_oidc_client_id
from core.fhir_pagination import FHIRBundlePagination
from core.models import (
    JheUser,
    CodeableConcept,
    Patient,
    StudyPatient,
    StudyPatientScopeConsent,
    Study,
    Organization,
    PatientOrganization,
    Observation,
    Practitioner,
    PractitionerOrganization,
)
from core.permissions import IfUserCan
from core.serializers import (
    CodeableConceptSerializer,
    FHIRBundledPatientSerializer,
    PatientSerializer,
    StudyPendingConsentsSerializer,
    StudyConsentsSerializer,
    StudyPatientScopeConsentSerializer,
)


class PatientViewSet(AdminListMixin, ModelViewSet):
    model_class = Patient
    serializer_class = PatientSerializer
    admin_query_method = Patient.__dict__["for_practitioner_organization_study"]
    admin_count_method = Patient.__dict__["count_for_practitioner_organization_study"]

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

    def create(self, request, *args, **kwargs):
        patient = None
        jhe_user = None
        if request.data["telecom_email"]:
            jhe_users = JheUser.objects.filter(email=request.data["telecom_email"])
            if jhe_users:
                jhe_user = jhe_users[0]
            else:
                jhe_user = JheUser(email=request.data["telecom_email"])
                jhe_user.set_password(get_random_string(length=16))
                jhe_user.save()
            request.data["jhe_user_id"] = jhe_user.id
            del request.data["telecom_email"]
            patient = Patient.objects.create(**request.data)
        else:
            raise ValidationError

        serializer = PatientSerializer(patient)
        return Response(serializer.data)

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
        PatientOrganization.objects.create(organization_id=organization.id, patient_id=patient.id)
        return Response(PatientSerializer(patient, many=False).data, status=200)

    @action(detail=True, methods=["GET"])
    def invitation_link(self, request, pk):
        send_email = request.query_params.get("send_email") == "true"
        patient = self.get_object()
        Application = get_application_model()
        qs = Application.objects.filter(authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE)
        application = (
            qs.filter(client_type=Application.CLIENT_PUBLIC).order_by("id").first() or qs.order_by("id").first()
        )
        if not application:
            raise ValidationError(
                "No OAuth Application configured for Authorization Code grant; create a PUBLIC application in Django Admin."
            )

        registered_redirect_uris = set((application.redirect_uris or "").split())
        if settings.OIDC_CLIENT_REDIRECT_URI not in registered_redirect_uris:
            raise ValidationError(
                "OAuth Application redirect URIs must include the JHE callback URL: "
                f"{settings.OIDC_CLIENT_REDIRECT_URI}"
            )
        grant = patient.jhe_user.create_authorization_code(application.id, settings.OIDC_CLIENT_REDIRECT_URI)

        # Build invitation link with all required OAuth parameters
        # Format: https://pgd.tcp.org/?invite=<hostname>&client_id=<id>&code_verifier=<verifier>&code=<auth_code>
        base_url = settings.CH_INVITATION_LINK_PREFIX

        # Extract hostname from SITE_URL (e.g., "localhost:8000" or "jhe.ucsf.edu")
        hostname = settings.SITE_URL.split("/")[2]

        # Use the same OAuth application used to create the Grant
        client_id = application.client_id
        code_verifier = settings.PATIENT_AUTHORIZATION_CODE_VERIFIER

        # Build the invitation link with query parameters
        # Check if base_url already has query params
        separator = "&" if "?" in base_url else "?"

        if settings.CH_INVITATION_LINK_EXCLUDE_HOST:
            # Legacy format without hostname
            invitation_link = f"{base_url}{separator}code={grant.code}"
        else:
            # New multi-host format with all OAuth params
            invitation_link = (
                f"{base_url}{separator}"
                f"invite={quote(hostname, safe='')}&"
                f"client_id={quote(client_id, safe='')}&"
                f"code_verifier={quote(code_verifier, safe='')}&"
                f"code={grant.code}"
            )

        if send_email:
            message = render_to_string(
                "registration/invitation_email.html",
                {
                    "patient_name": patient.name_given,
                    "invitation_link": invitation_link,
                },
            )
            email = EmailMessage("JHE Invitation", message, to=[patient.jhe_user.email])
            email.content_subtype = "html"
            email.send()
        return Response({"invitation_link": invitation_link})

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
                if not study_patient:
                    raise ValidationError(
                        f"No StudyPatient exists for patient_id={patient.id} and study_id={study_scope_consent['study_id']}"
                    )
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

                    consent_qs = StudyPatientScopeConsent.objects.filter(
                        study_patient_id=study_patient.id,
                        scope_code_id=scope_code_id,
                    )

                    if request.method in ["POST", "PATCH"]:
                        # Idempotent: if this consent already exists, update it.
                        consent_obj, _created = StudyPatientScopeConsent.objects.update_or_create(
                            study_patient_id=study_patient.id,
                            scope_code_id=scope_code_id,
                            defaults={
                                "consented": scope_consent["consented"],
                                "consented_time": consented_time,
                            },
                        )
                        responses.append(consent_obj)
                    elif request.method == "DELETE":
                        consent_obj = consent_qs.first()
                        if not consent_obj:
                            raise ValidationError("Consent record does not exist for this study_patient/scope_code.")
                        consent_qs.delete()
                        responses.append(consent_obj)

            return Response({"study_scope_consents": StudyPatientScopeConsentSerializer(responses, many=True).data})


class FHIRPatientViewSet(ModelViewSet):
    serializer_class = FHIRBundledPatientSerializer
    pagination_class = FHIRBundlePagination

    def get_queryset(self):

        patient_identifier_system_and_value = self.request.GET.get("identifier", None)

        # GET /Patient?_has:Group:member:_id=<group-id>
        study_id = self.request.GET.get("_has:_group:member:_id", None)

        if not (study_id or patient_identifier_system_and_value):
            raise ValidationError(
                "Request parameter _has:Group:member:_id=<study_id> or"
                " patient.identifier=<system>|<value> must be provided."
            )

        patient_identifier_system = None
        patient_identifier_value = None
        if patient_identifier_system_and_value:
            patient_identifier_split = patient_identifier_system_and_value.split("|")  # TBD 400 for formatting error
            patient_identifier_system = patient_identifier_split[0]
            patient_identifier_value = patient_identifier_split[1]

        if study_id and (not Study.practitioner_authorized(self.request.user.id, study_id)):
            raise PermissionDenied("Current User does not have authorization to access this Study.")

        if patient_identifier_system_and_value and (
            not Patient.practitioner_authorized(self.request.user.id, None, None, patient_identifier_value)
        ):
            raise PermissionDenied("Current User does not have authorization to access this Patient.")

        return Patient.fhir_search(
            self.request.user.id,
            study_id,
            patient_identifier_system,
            patient_identifier_value,
        )
