from django.core.mail import EmailMessage
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from oauth2_provider.models import get_application_model
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.models import Patient, PatientInvitation
from core.models.patient_invitation import InvitationCancelled, InvitationConflict, InvitationExpired
from core.serializers import PatientInvitationSerializer

Application = get_application_model()


class PatientInvitationViewSet(ModelViewSet):
    serializer_class = PatientInvitationSerializer

    def get_permissions(self):
        if self.action == "redeem":
            return [AllowAny()]
        return super().get_permissions()

    def get_queryset(self):
        return PatientInvitation.objects.all()

    def create(self, request, *args, **kwargs):
        patient_id = request.data.get("patient_id")
        client_id = request.data.get("client_id")
        send_email = request.data.get("send_email") == True  # noqa: E712

        errors = {}
        if not patient_id:
            errors["patient_id"] = ["This field is required."]
        if not client_id:
            errors["client_id"] = ["This field is required."]
        if errors:
            raise ValidationError(errors)

        patient = get_object_or_404(Patient, id=patient_id)
        client = get_object_or_404(Application, id=client_id)

        invitation, link = PatientInvitation.build_link(patient, client)

        if send_email:
            message = render_to_string(
                "registration/invitation_email.html",
                {
                    "patient_name": patient.name_given,
                    "invitation_link": link,
                },
            )
            email = EmailMessage("JHE Invitation", message, to=[patient.jhe_user.email])
            email.content_subtype = "html"
            email.send()

        data = PatientInvitationSerializer(invitation).data
        data["invitation_link"] = link
        return Response(data)

    # Token is used both for lookup and as PKCE code verifier
    @action(detail=False, methods=["POST"], url_path=r"(?P<token>[A-Za-z0-9]{32})")
    def redeem(self, request, token):
        try:
            _, grant = PatientInvitation.redeem(token)
        except PatientInvitation.DoesNotExist:
            raise NotFound("Invitation not found or already used.")
        except InvitationExpired:
            return Response({"detail": "Invitation has expired."}, status=410)
        except InvitationCancelled:
            return Response({"detail": "Invitation has been cancelled."}, status=403)
        except InvitationConflict:
            return Response({"detail": "Invitation has already been used."}, status=409)
        except ValueError as e:
            raise ValidationError(str(e))

        return Response(
            {
                "grant": {
                    "grant_type": "authorization_code",
                    "redirect_uri": grant.redirect_uri,
                    "client_id": grant.application.client_id,
                    "code": grant.code,
                },
                "token_endpoint": request.build_absolute_uri(reverse("oauth2_provider:token")),
                "expires": grant.expires,
            }
        )
