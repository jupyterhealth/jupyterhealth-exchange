from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.models import EmailAddress
from allauth.core.exceptions import SignupClosedException
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.utils.translation import gettext_lazy as _

from core.services.jhe_settings import get_setting


class JheAccountAdapter(DefaultAccountAdapter):
    """Rewords allauth's bundled account error messages so they read consistently.

    allauth raises these via `adapter.validation_error(code)`, which looks the
    text up in `error_messages[code]`. We only override the keys surfaced by the
    email one-time-code login; everything else falls back to allauth's defaults
    through the dict merge. Add more keys here (see
    allauth.account.adapter.DefaultAccountAdapter.error_messages) to reword others.
    """

    error_messages = {
        **DefaultAccountAdapter.error_messages,
        "incorrect_code": _("That sign-in code is incorrect. Please try again."),
        "too_many_login_attempts": _("Too many incorrect attempts. Please request a new code and try again."),
        "unknown_email": _("We couldn't find an account for that email address."),
        "rate_limited": _("You're making requests too quickly. Please wait a moment and try again."),
    }


class JheSocialAccountAdapter(DefaultSocialAccountAdapter):
    """SAML SSO glue: IdP-asserted users are provisioned as practitioners,
    optionally restricted to email domains in the `auth.sso.valid_domains`
    JheSetting (comma-separated; empty allows any). The domain gate applies to
    first-time provisioning only — existing accounts reached via
    email-authentication, and already-linked SocialAccounts, never pass through
    it; deactivate the user to revoke access. `user_type`/`identifier` are
    likewise set at first-time provisioning only. Rejections render allauth's
    "Sign Up Closed" page.
    """

    def pre_social_login(self, request, sociallogin):
        # SAML SSO is a practitioner entrance: don't let an existing patient
        # account (same IdP-asserted email, or previously linked) into a
        # practitioner portal session.
        if sociallogin.is_existing and sociallogin.user.is_patient():
            raise SignupClosedException()
        # When allauth matched this login to an existing user by verified
        # IdP-asserted email (_did_authenticate_by_email, set in lookup()),
        # its anti-pre-registration guard would wipe the user's password in
        # _accept_login unless a *verified* allauth EmailAddress row exists —
        # and JHE practitioners created via our own signup/admin paths have
        # none. Backfill one: the IdP's email trust (the same decision that
        # enabled email-authentication for this SocialApp) vouches for it.
        email = getattr(sociallogin, "_did_authenticate_by_email", None)
        if email and sociallogin.user.pk:
            address, _ = EmailAddress.objects.get_or_create(
                user=sociallogin.user,
                email=email.lower(),
                defaults={"verified": True, "primary": True},
            )
            if not address.verified:
                address.verified = True
                address.save(update_fields=["verified"])

    def is_open_for_signup(self, request, sociallogin):
        email = (sociallogin.user.email or "").lower()
        if not email:
            # No email asserted (misconfigured IdP attribute mapping): fail
            # closed rather than fall through to allauth's type-any-email
            # signup form, which would mint an unverified practitioner.
            return False
        valid_domains = get_setting("auth.sso.valid_domains", "")
        if not valid_domains:
            return True
        allowed = {d.strip().lstrip("@").lower() for d in valid_domains.split(",") if d.strip()}
        # rsplit, not partition: the domain is what follows the LAST "@" (RFC
        # 5321 quoted local parts may contain "@"), matching Django's
        # EmailValidator parse.
        return email.rsplit("@", 1)[-1] in allowed

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        user.user_type = "practitioner"
        user.identifier = sociallogin.account.uid
        # Mirrors the verified allauth EmailAddress row this login records —
        # the IdP asserted the email, so JHE's own flag agrees with it.
        user.email_is_verified = True
        return user
