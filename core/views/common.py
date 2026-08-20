import logging
import secrets
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
from allauth.account.models import EmailAddress
from allauth.account.views import RequestLoginCodeView
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login
from django.contrib.auth import logout as django_logout
from django.contrib.auth.views import LoginView as BaseLoginView
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from oauth2_provider.models import get_access_token_model
from oauth2_provider.views import TokenView
from oauthlib.common import Request

from core.auth import IdTokenError, JheOAuth2Validator, account_activation_token, parse_fhir_user, verify_id_token
from core.models import JheUser
from core.services.jhe_settings import get_setting

from ..forms import UserRegistrationForm

logger = logging.getLogger(__name__)

User = get_user_model()
AccessToken = get_access_token_model()


def health(request):
    """Lightweight liveness probe — no DB, no auth."""
    return JsonResponse({"status": "ok", "version": settings.JHE_VERSION})


def _well_known_file(filename):
    """Serve a ``core/data`` file verbatim as JSON — no auth, no redirect."""
    body = (settings.BASE_DIR / "core" / "data" / filename).read_bytes()
    return HttpResponse(body, content_type="application/json")


def apple_app_site_association(request):
    """iOS Universal Links association file. Apple requires ``application/json``."""
    return _well_known_file("apple-app-site-association.json")


def assetlinks(request):
    """Android App Links association file (Digital Asset Links)."""
    return _well_known_file("assetlinks.json")


def home(request):
    return render(request, "home/home.html")


def ow_client(request):
    return render(request, "clients/ow/launch.html")


def ow_client_complete(request):
    return render(request, "clients/ow/complete.html")


class LoginView(BaseLoginView):
    def get(self, request, *args, **kwargs):
        otp_redirect = _patient_access_otp_redirect(request)
        if otp_redirect is not None:
            return otp_redirect
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


def _patient_access_otp_redirect(request):
    """Route patient-access OAuth clients to the email one-time-code login.

    DOT sends unauthenticated users to /accounts/login/?next=<authorize URL>. When
    that authorize URL's client_id is listed in the `auth.patient_access_clients`
    JheSetting, send the user to the allauth email-code flow instead of the
    password form, preserving `next` so OAuth resumes after login. Returns a
    redirect response, or None to fall through to the default password form.
    """
    if request.user.is_authenticated:
        return None
    next_url = request.GET.get("next")
    if not next_url:
        return None
    client_ids = parse_qs(urlparse(next_url).query).get("client_id")
    if not client_ids:
        return None
    patient_access_clients = get_setting("auth.patient_access_clients", []) or []
    if client_ids[0] not in patient_access_clients:
        return None
    return redirect(f"{reverse('login-otp')}?{urlencode({'next': next_url})}")


class JheRequestLoginCodeView(RequestLoginCodeView):
    """allauth's "request a login code" view, mounted at /accounts/login-otp/.

    NextRedirectMixin carries `next` through the confirm step and on to the
    original OAuth authorize URL after login.

    JHE patients are created outside allauth (e.g. the invitation flow) and so
    have no allauth EmailAddress row. With ACCOUNT_EMAIL_VERIFICATION="mandatory"
    that would stall login at the confirm-email stage, because allauth only marks
    the email verified when entering the code if such a row already exists
    (verify_email_indirectly is otherwise a no-op). Creating an unverified row
    here lets the entered code verify the address, completing login. Entering the
    emailed code is itself proof the user controls the address.
    """

    def form_valid(self, form):
        user = getattr(form, "_user", None)
        email = form.cleaned_data.get("email")
        if user is not None and email:
            EmailAddress.objects.get_or_create(
                user=user,
                email=email,
                defaults={"verified": False, "primary": True},
            )
        return super().form_valid(form)


request_login_otp = JheRequestLoginCodeView.as_view()


def logout(request):
    django_logout(request)
    return redirect("home")


def profile(request):
    return redirect("/clients/jhe-admin/")


def client_auth_callback(request):
    return render(request, "common/auth/callback.html")


def client_auth_callback_popup(request):
    return render(request, "common/auth/callback_popup.html")


def client_auth_login(request):
    return render(request, "common/auth/login.html")


def signup(request):
    if request.method == "POST":
        next = request.GET.get("next")
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            password = form.cleaned_data.get("password")
            # Get user type from form, default to 'practitioner' if not set
            user_type = form.cleaned_data.get("user_type") or "practitioner"
            user.set_password(password)
            user.first_name = "NONE"
            user.last_name = "NONE"
            user.user_type = user_type  # Set user type (patient or practitioner)
            user.save()
            new_user = authenticate(email=user.email, password=password)
            login(request, new_user)
            if new_user.email_is_verified is not True:
                new_user.send_email_verificaion()
            if next:
                return redirect(next)
            else:
                return redirect("/clients/jhe-admin/")
    else:
        # Get user_type from URL parameter, default to 'practitioner' if not specified
        user_type = request.GET.get("user_type", "practitioner")
        form = UserRegistrationForm(initial={"user_type": user_type})
    context = {"form": form}
    return render(request, "registration/signup.html", context)


def verify_email(request):
    if request.method == "POST":
        if request.user.email_is_verified is not True:
            request.user.send_email_verificaion()
            return redirect("verify_email_done")
        else:
            return redirect("verify_email_complete")
    return render(request, "registration/verify_email.html")


def verify_email_confirm(request, user_id_base64, token):
    try:
        user_id = force_str(urlsafe_base64_decode(user_id_base64))
        user = User.objects.get(pk=user_id)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None
    if user is not None and account_activation_token.check_token(user, token):
        user.email_is_verified = True
        user.save()
        messages.success(request, "Your email has been verified.")
        return redirect("verify_email_complete")
    else:
        messages.warning(request, "The link is invalid.")
    return render(request, "registration/verify_email_confirm.html")


def verify_email_done(request):
    return render(request, "registration/verify_email_done.html")


def verify_email_complete(request):
    return render(request, "registration/verify_email_complete.html")


def portal(request, path):
    return render(request, "clients/jhe_admin/portal.html", {"foo": "bar"})


def ow_launch(request):
    return render(request, "clients/ow/launch.html")


def ow_complete(request):
    return render(request, "clients/ow/complete.html")


def ow_manage(request):
    return render(request, "clients/ow/manage.html")


def json_error(msg, status_code=400):
    """Return a JSON error message"""
    response = JsonResponse({"error": msg})
    response.status_code = status_code
    return response


class JheTokenView(TokenView):
    """OAuth2 token endpoint that returns JSON on unexpected errors.

    DOT's TokenView lets exceptions (eg. a missing/invalid OIDC_RSA_PRIVATE_KEY,
    which breaks id_token signing) bubble up to an HTML 500. The front-end OIDC
    client only parses JSON responses, so the real reason never reached the user
    (#192). Wrapping post() returns a standard OAuth2 JSON error instead; the
    detail is included only when DEBUG is on to avoid leaking internals in prod.
    """

    def post(self, request, *args, **kwargs):
        try:
            return super().post(request, *args, **kwargs)
        except Exception as exc:
            logger.exception("Token endpoint error")
            detail = (
                str(exc) if settings.DEBUG else "The authorization server encountered an error processing the request."
            )
            return JsonResponse(
                {"error": "server_error", "error_description": detail},
                status=500,
            )


@csrf_exempt
@require_POST
def token_exchange(request: HttpRequest):
    """RFC 8693 token exchange: trade an EHR-issued OIDC id_token for a JHE token.

    The id_token is verified offline against the EHR's JWKS (cross-vendor, relies
    only on ONC g(10) capabilities). The provider is identified by the fhirUser
    claim and mapped to a JHE Practitioner.
    Ref: https://datatracker.ietf.org/doc/html/rfc8693
    """
    _id_token_type = "urn:ietf:params:oauth:token-type:id_token"
    _access_token_type = "urn:ietf:params:oauth:token-type:access_token"

    for name in ("audience", "requested_token_type", "subject_token_type", "subject_token", "grant_type"):
        if not request.POST.get(name):
            return json_error(f"Missing required argument: {name}")

    site_url = get_setting("site.url", settings.SITE_URL)
    requested_audience = request.POST.get("audience")
    requested_token_type = request.POST.get("requested_token_type")
    subject_token_type = request.POST.get("subject_token_type")
    subject_token = request.POST.get("subject_token")
    grant_type = request.POST.get("grant_type")
    scope = request.POST.get("scope", "openid")

    if grant_type != "urn:ietf:params:oauth:grant-type:token-exchange":
        return json_error(f"grant_type must be token-exchange, not {grant_type}")
    if subject_token_type != _id_token_type:
        return json_error(f"subject_token_type must be {_id_token_type}, not {subject_token_type}")
    if requested_token_type != _access_token_type:
        return json_error(f"requested_token_type must be {_access_token_type}, not {requested_token_type}")
    if requested_audience != site_url:
        return json_error(f"audience must be {site_url}, not {requested_audience}")
    if scope != "openid":
        return json_error(f"Only 'openid' scope is supported, not {scope}")

    # Authenticate the calling client (RFC 6749 client authentication) against the
    # registered confidential "SoF EHR Launch" Application. Credentials may be sent as
    # HTTP Basic or as client_id/client_secret form fields. authenticate_client sets
    # oauth_request.client on success; we reuse that request to link the Application to
    # the issued access token below.
    validator = JheOAuth2Validator()
    oauth_request = Request(
        uri=request.build_absolute_uri(),
        http_method="POST",
        body=request.POST.urlencode(),
        # django-oauth-toolkit's _extract_basic_auth reads the WSGI-style
        # "HTTP_AUTHORIZATION" key, not "Authorization".
        headers={"HTTP_AUTHORIZATION": request.META.get("HTTP_AUTHORIZATION", "")},
    )
    if not validator.authenticate_client(oauth_request):
        return json_error("Client authentication failed", status_code=401)

    trusted_issuers = get_setting("auth.sof.trusted_issuers", []) or []
    expected_audience = get_setting("auth.sof.trusted_audience", None)
    if not trusted_issuers or not expected_audience:
        return json_error("Token exchange is not configured.", status_code=500)

    # Derive the issuer from the token's own `iss` so the exact value -- including
    # any trailing slash (e.g. MedPlum's) -- is what we verify against.
    try:
        unverified = jwt.decode(subject_token, options={"verify_signature": False})
    except jwt.InvalidTokenError:
        return json_error("subject_token is not a valid JWT", status_code=400)
    token_issuer = unverified.get("iss")
    if not token_issuer or token_issuer.rstrip("/") not in {i.rstrip("/") for i in trusted_issuers}:
        logger.warning("Token exchange: rejected untrusted issuer %r", token_issuer)
        return json_error("Issuer not trusted", status_code=403)

    try:
        claims = verify_id_token(subject_token, issuer=token_issuer, audience=expected_audience)
        fhir_user = claims.get("fhirUser")
        if not fhir_user:
            return json_error("id_token missing fhirUser claim", status_code=400)
        resource_type, identifier = parse_fhir_user(fhir_user)
    except IdTokenError as e:
        return json_error(str(e), status_code=e.status_code)

    if resource_type != "Practitioner":
        return json_error("fhirUser is not a Practitioner", status_code=403)

    # Bare fhirUser id (not an issuer-scoped composite): each JHE instance trusts
    # exactly one EHR, so no other issuer can assert these Practitioner ids.
    try:
        user = JheUser.objects.get(identifier=identifier)
    except JheUser.DoesNotExist:
        return json_error("Practitioner not found", status_code=404)
    except JheUser.MultipleObjectsReturned:
        logger.error("Multiple JheUsers share identifier %r", identifier)
        return json_error("Practitioner not found", status_code=404)
    if not user.practitioner:
        return json_error("User is not a Practitioner", status_code=403)

    # Issue a JHE access token, linked to the authenticated client (oauth_request.client
    # was set by authenticate_client above) and bound to the resolved Practitioner.
    access_token = secrets.token_urlsafe(32)
    oauth_request.user = user
    validator.save_bearer_token({"access_token": access_token, "scope": "openid"}, oauth_request)

    token_model = AccessToken.objects.get(token=access_token)
    expires_in = int((token_model.expires - timezone.now()).total_seconds())
    logger.info(
        "Token exchange: issued JHE token for Practitioner %r from issuer %s to client %r",
        identifier,
        token_issuer,
        getattr(oauth_request.client, "client_id", None),
    )
    return JsonResponse(
        {
            "access_token": access_token,
            "issued_token_type": _access_token_type,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "scope": scope,
        }
    )
