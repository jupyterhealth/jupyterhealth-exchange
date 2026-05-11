import base64
import hashlib
import json
import secrets
from datetime import timedelta
from random import SystemRandom
from urllib.parse import quote, urlparse

from django.conf import settings
from django.db import models
from django.utils import timezone
from oauth2_provider.models import get_grant_model

from core.jhe_settings.service import get_setting

from .jhe_setting import JheSetting
from .patient import Patient

_TOKEN_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_TOKEN_LENGTH = 32


class InvitationExpired(Exception):
    pass


class InvitationCancelled(Exception):
    pass


class InvitationConflict(Exception):
    pass


class PatientInvitation(models.Model):
    class Status(models.TextChoices):
        ISSUED = "issued"
        REISSUED = "reissued"
        REDEEMED = "redeemed"
        CANCELLED = "cancelled"
        EXPIRED = "expired"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="invitations")
    client = models.ForeignKey(
        settings.OAUTH2_PROVIDER_APPLICATION_MODEL,
        on_delete=models.CASCADE,
        related_name="patient_invitations",
    )
    token_hash = models.CharField(max_length=43, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_updated"]

    @staticmethod
    def _hash_token(token):
        return base64.urlsafe_b64encode(hashlib.sha256(token.encode()).digest()).rstrip(b"=").decode()

    @staticmethod
    def issue(patient, client):
        PatientInvitation.objects.filter(patient=patient, client=client, status=PatientInvitation.Status.ISSUED).update(
            status=PatientInvitation.Status.REISSUED
        )

        token = "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(_TOKEN_LENGTH))
        token_hash = PatientInvitation._hash_token(token)
        obj = PatientInvitation.objects.create(
            patient=patient,
            client=client,
            token_hash=token_hash,
            status=PatientInvitation.Status.ISSUED,
        )
        obj.token = token
        return obj

    @staticmethod
    def build_link(patient, client):
        invitation_url_setting = JheSetting.objects.filter(setting_id=client.id, key="client.invitation_url").first()

        if not invitation_url_setting:
            raise ValueError("Missing JheSetting: client.invitation_url")

        invitation = PatientInvitation.issue(patient, client)

        site_url = get_setting("site.url", settings.SITE_URL)
        host = urlparse(site_url).netloc
        code = quote(f"{host}_{invitation.token}", safe="_")
        link = invitation_url_setting.get_value().replace("CODE", code)

        return invitation, link

    # https://github.com/jazzband/django-oauth-toolkit/blob/102c85141ec44549e17080c676292e79e5eb46cc/oauth2_provider/oauth2_validators.py#L675
    @staticmethod
    def redeem(token):
        invitation = PatientInvitation.objects.get(
            token_hash=PatientInvitation._hash_token(token),
        )

        recently_redeemed = False

        if invitation.status == PatientInvitation.Status.ISSUED:
            expiration_days = get_setting("auth.patient.invitation_expiration_days", 7)
            if (timezone.now() - invitation.last_updated).days >= expiration_days:
                invitation.status = PatientInvitation.Status.EXPIRED
                invitation.save()
                raise InvitationExpired()
        elif invitation.status == PatientInvitation.Status.REDEEMED:
            redemption_window_hours = get_setting("auth.patient.invitation_redemption_window_hours", 12)
            elapsed_hours = (timezone.now() - invitation.last_updated).total_seconds() / 3600
            if elapsed_hours <= redemption_window_hours:
                recently_redeemed = True
            else:
                raise InvitationConflict()
        elif invitation.status == PatientInvitation.Status.EXPIRED:
            raise InvitationExpired()
        elif invitation.status == PatientInvitation.Status.CANCELLED:
            raise InvitationCancelled()
        else:  # REISSUED
            raise InvitationConflict()

        if not invitation.patient.jhe_user_id:
            raise ValueError("Patient has no associated user account.")

        jhe_user = invitation.patient.jhe_user
        code_verifier = base64.urlsafe_b64encode(token.encode()).rstrip(b"=").decode()

        jhe_user.last_login = timezone.now()
        jhe_user.save()

        Grant = get_grant_model()
        Grant.objects.filter(user_id=jhe_user.id, application_id=invitation.client_id).delete()

        # https://github.com/oauthlib/oauthlib/blob/f9a07c6c07d0ddac255dd322ef5fc54a7a46366d/oauthlib/common.py#L188
        UNICODE_ASCII_CHARACTER_SET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        authorization_code = "".join(SystemRandom().choice(UNICODE_ASCII_CHARACTER_SET) for _ in range(30))

        grant = Grant.objects.create(
            application_id=invitation.client_id,
            user_id=jhe_user.id,
            code=authorization_code,
            expires=timezone.now() + timedelta(seconds=settings.PATIENT_AUTHORIZATION_CODE_EXPIRE_SECONDS),
            redirect_uri=get_setting("site.url", settings.SITE_URL) + settings.OAUTH2_CALLBACK_PATH,
            scope="openid email",
            # https://github.com/oauthlib/oauthlib/blob/f9a07c6c07d0ddac255dd322ef5fc54a7a46366d/oauthlib/oauth2/rfc6749/grant_types/authorization_code.py#L18
            code_challenge=base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .rstrip(b"=")
            .decode(),
            code_challenge_method="S256",
            nonce="",
            claims=json.dumps({}),
        )

        if not recently_redeemed:
            invitation.status = PatientInvitation.Status.REDEEMED
        invitation.save()

        return invitation, grant
