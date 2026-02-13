import logging
import secrets
import urllib
from typing import Optional

import jwt
import requests
from dictor import dictor  # type: ignore
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth import login
from django.contrib.auth import logout as django_logout
from django.contrib.auth.views import LoginView as BaseLoginView
from django.http import HttpRequest, HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.template import TemplateDoesNotExist
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views.decorators.csrf import csrf_exempt
from django_saml2_auth.errors import INACTIVE_USER, USER_MISMATCH
from django_saml2_auth.exceptions import SAMLAuthError
from django_saml2_auth.saml import (
    decode_saml_response,
    extract_user_identity,
    get_default_next_url,
)
from django_saml2_auth.user import (
    create_custom_or_default_jwt,
    decode_custom_or_default_jwt,
    get_user_id,
)
from django_saml2_auth.utils import (
    exception_handler,
    is_jwt_well_formed,
    run_hook,
)
from oauth2_provider.oauth2_validators import OAuth2Validator
from oauth2_provider.models import get_access_token_model
from oauthlib.common import Request

from core.models import JheUser
from core.utils import get_or_create_user
from ..forms import UserRegistrationForm
from ..tokens import account_activation_token

logger = logging.getLogger(__name__)

User = get_user_model()
AccessToken = get_access_token_model()


def home(request):
    return render(request, "home/home.html")


class LoginView(BaseLoginView):
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


def logout(request):
    django_logout(request)
    return redirect("home")


def profile(request):
    return redirect("/portal/")


def client_auth_callback(request):
    return render(request, "client/client_auth/callback.html")


def client_auth_callback_popup(request):
    return render(request, "client/client_auth/callback_popup.html")


def client_auth_login(request):
    return render(request, "client/client_auth/login.html")


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
                return redirect("/portal/")
    else:
        # Get user_type from URL parameter, default to 'practitioner' if not specified
        user_type = request.GET.get("user_type", "practitioner")
        form = UserRegistrationForm(initial={"user_type": user_type})
    context = {"form": form}
    return render(request, "registration/signup.html", context)


def verify_email(request):
    if request.method == "POST":
        if request.user.email_is_verified is not True:
            request.user.send_email_verificaion
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
    return render(request, "client/portal.html", {"foo": "bar"})


def smart_launch(request):
    raise NotImplementedError()
    # TBD: this is an incomplete implementation,
    # remove all hardcoded values in favor of settings
    SMART_CLIENT_ID = "jhe1234"
    SMART_REDIRECT_URI = settings.SITE_URL + "/smart/callback"
    SMART_SCOPES = "openid fhirUser launch launch/patient online_access patient/*.rs observation/*.rs"
    # https://build.fhir.org/ig/HL7/smart-app-launch/scopes-and-launch-context.html

    # 1) Initial launch URL is provided to EHR out of band
    # 2) User clicks to launch app which sends GET request to this view:
    # 3) /smart/launch?iss=https%3A%2F%2Flaunch.smarthealthit.org%2Fv%2Fr4%2Ffhir&launch=WzAsIiIsIiIsIkFVVE8
    logger.info(f"smart_launch request: {request.GET}")
    iss = request.GET.get("iss")
    launch = request.GET.get("launch")
    logger.info(f"iss: {iss}; launch: {launch}")
    # 4) We need to retrieve the public config from .well-known
    smart_config_response = requests.get(
        f"{iss}/.well-known/smart-configuration", headers={"Accept": "application/json"}
    )
    smart_config_data = smart_config_response.json()
    logger.info(f"smart_config: {smart_config_data}")
    # 5) Now we need to get the authroization token - we use config to construct the request
    #    See https://build.fhir.org/ig/HL7/smart-app-launch/app-launch.html#request-4

    # code_verifier, code_challenge = new_pkce_challenge()

    auth_code_params = {
        "response_type": "code",  # fixed
        "client_id": SMART_CLIENT_ID,
        "redirect_uri": SMART_REDIRECT_URI,
        "launch": launch,
        "scope": SMART_SCOPES,
        "state": "jheState1",  # TBD map to user session - this is client-provided
        "aud": iss,
        # "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    # authorization_code = requests.get(smart_config_data.get('authorization_endpoint'),
    # headers={'Accept': 'application/json'})
    smart_config_auth_endpoint = smart_config_data.get("authorization_endpoint")
    return redirect(f"{smart_config_auth_endpoint}?{urllib.parse.urlencode(auth_code_params)}")


def smart_callback(request):
    raise NotImplementedError()
    # TBD: this is an incomplete implementation,
    # remove all hardcoded values in favor of settings
    auth_code = request.GET.get("code")
    state = request.GET.get("state")  # noqa

    SMART_REDIRECT_URI = settings.SITE_URL + "/smart/callback"

    smart_config_token_endpoint = (
        "https://launch.smarthealthit.org/v/r4/auth/token"  # from above smart_config_data.get('authorization_endpoint')
    )
    code_verifier = request.session.get("pkce_verifier")
    if code_verifier is not None:
        # FIXME: raise the right request error
        raise

    token_params = {
        "grant_type": "authorization_code",  # fixed
        "code": auth_code,
        "redirect_uri": SMART_REDIRECT_URI,
        "code_verifier": code_verifier,
    }

    token_response = requests.post(
        smart_config_token_endpoint,
        data=token_params,
        headers={"Accept": "application/json"},
    )
    token_data = token_response.json()

    logger.info(f"token_data: {token_data}")

    decoded_id_token = jwt.decode(str(token_data.get("id_token")), options={"verify_signature": False})

    logger.info(f"decoded_id_token: {decoded_id_token}")

    fhirUserId = decoded_id_token.get("fhirUser")

    user = User.objects.filter(identifier=fhirUserId).first()

    logger.info(f"Logging in user: {user}")

    # =========== Provisioning ===========
    # user = User(username='x')
    # set_unusable_password()
    # user.save()

    login(request, user, backend=settings.AUTHENTICATION_BACKENDS[0])

    logger.info(f'Save to server state, patient_id: {token_data.get("patient")}')

    return redirect("client-auth-login")


@csrf_exempt
@exception_handler
def acs(request: HttpRequest):
    """Assertion Consumer Service is SAML terminology for the location at a ServiceProvider that
    accepts <samlp:Response> messages (or SAML artifacts) for the purpose of establishing a session
    based on an assertion. Assertion is a signed authentication request from identity provider (IdP)
    to acs endpoint.

    Args:
        request (HttpRequest): Incoming request from identity provider (IdP) for authentication

    Exceptions:
        SAMLAuthError: The target user is inactive.

    Returns:
        HttpResponseRedirect: Redirect to various endpoints: denied, welcome or next_url (e.g.
            the front-end app)

    Notes:
        https://wiki.shibboleth.net/confluence/display/CONCEPT/AssertionConsumerService
    """
    saml2_auth_settings = settings.SAML2_AUTH

    authn_response = decode_saml_response(request, acs)
    # decode_saml_response() will raise SAMLAuthError if the response is invalid,
    # so we can safely ignore the type check here.
    user = extract_user_identity(authn_response)  # type: ignore

    next_url = request.session.get("login_next_url")

    # A RelayState is an HTTP parameter that can be included as part of the SAML request
    # and SAML response; usually is meant to be an opaque identifier that is passed back
    # without any modification or inspection, and it is used to specify additional information
    # to the SP or the IdP.
    # If RelayState params is passed, it could be JWT token that identifies the user trying to
    # login via sp_initiated_login endpoint, or it could be a URL used for redirection.
    RELAYSTATE_NULL_LITERALS = {"undefined", "null", "none", "", None}

    relay_state = request.POST.get("RelayState")
    relay_state = None if relay_state in RELAYSTATE_NULL_LITERALS else relay_state

    relay_state_is_token = is_jwt_well_formed(relay_state) if relay_state else False
    if next_url is None and relay_state and not relay_state_is_token:
        next_url = relay_state
    elif next_url is None:
        next_url = get_default_next_url()

    if relay_state and relay_state_is_token:
        redirected_user_id = decode_custom_or_default_jwt(relay_state)

        # This prevents users from entering an email on the SP, but use a different email on IdP
        if get_user_id(user) != redirected_user_id:
            raise SAMLAuthError(
                "The user identifier doesn't match.",
                extra={
                    "exc_type": ValueError,
                    "error_code": USER_MISMATCH,
                    "reason": "User identifier mismatch.",
                    "status_code": 403,
                },
            )

    is_new_user, target_user = get_or_create_user(user)

    before_login_trigger = dictor(saml2_auth_settings, "TRIGGER.BEFORE_LOGIN")
    if before_login_trigger:
        run_hook(before_login_trigger, user)  # type: ignore

    request.session.flush()

    if target_user.is_active:
        # Try to load from the `AUTHENTICATION_BACKENDS` setting in settings.py
        if hasattr(settings, "AUTHENTICATION_BACKENDS") and settings.AUTHENTICATION_BACKENDS:
            model_backend = settings.AUTHENTICATION_BACKENDS[0]
        else:
            model_backend = "django.contrib.auth.backends.ModelBackend"

        login(request, target_user, model_backend)

        after_login_trigger = dictor(saml2_auth_settings, "TRIGGER.AFTER_LOGIN")
        if after_login_trigger:
            run_hook(after_login_trigger, request.session, user)  # type: ignore
    else:
        raise SAMLAuthError(
            "The target user is inactive.",
            extra={
                "exc_type": Exception,
                "error_code": INACTIVE_USER,
                "reason": "User is inactive.",
                "status_code": 500,
            },
        )

    use_jwt = dictor(saml2_auth_settings, "USE_JWT", False)
    if use_jwt:
        # Create a new JWT token for IdP-initiated login (acs)
        jwt_token = create_custom_or_default_jwt(target_user)
        custom_token_query_trigger = dictor(saml2_auth_settings, "TRIGGER.CUSTOM_TOKEN_QUERY")
        if custom_token_query_trigger:
            query = run_hook(custom_token_query_trigger, jwt_token)
        else:
            query = f"?token={jwt_token}"

        # Use JWT auth to send token to frontend
        frontend_url = dictor(saml2_auth_settings, "FRONTEND_URL", next_url)
        custom_frontend_url_trigger = dictor(saml2_auth_settings, "TRIGGER.GET_CUSTOM_FRONTEND_URL")
        if custom_frontend_url_trigger:
            frontend_url = run_hook(custom_frontend_url_trigger, relay_state)  # type: ignore

        return HttpResponseRedirect(frontend_url + query)

    def redirect(redirect_url: Optional[str] = None) -> HttpResponseRedirect:
        """Redirect to the redirect_url or the root page.

        Args:
            redirect_url (str, optional): Redirect URL. Defaults to None.

        Returns:
            HttpResponseRedirect: Redirect to the redirect_url or the root page.
        """
        if redirect_url:
            return HttpResponseRedirect(redirect_url)
        else:
            return HttpResponseRedirect("/")

    if is_new_user:
        try:
            return render(request, "django_saml2_auth/welcome.html", {"user": request.user})
        except TemplateDoesNotExist:
            return redirect(next_url)
    else:
        return redirect(next_url)


def json_error(msg, status_code=400):
    """Return a JSON error message"""
    response = JsonResponse({"error": msg})
    response.status_code = status_code
    return response


@csrf_exempt
def token_exchange(request: HttpRequest):
    """
    RFC 8693: OAuth 2.0 Token Exchange

    Extensions to spec:

    - `iss` parameter identifies issuing Identity Provider (IdP)

    Requires setting:
    - TRUSTED_TOKEN_IDP: OIDC base URL

    Ref: https://datatracker.ietf.org/doc/html/rfc8693
    """

    if not request.POST:
        return json_error("Method not allowed", status_code=405)
    for name in (
        "audience",
        "requested_token_type",
        "subject_token_type",
        "subject_token",
        "grant_type",
    ):
        if not request.POST.get(name):
            return json_error(f"Missing required argument: {name}")

    # standard arguments:
    audience = request.POST.get("audience")
    requested_token_type = request.POST.get("requested_token_type")
    subject_token_type = request.POST.get("subject_token_type")
    subject_token = request.POST.get("subject_token")
    grant_type = request.POST.get("grant_type")
    scope = request.POST.get("scope", "openid")

    # argument validation
    if grant_type != "urn:ietf:params:oauth:grant-type:token-exchange":
        return json_error(f"grant_type must be urn:ietf:params:oauth:grant-type:token-exchange, not {grant_type}")
    _access_token_type = "urn:ietf:params:oauth:token-type:access_token"
    if subject_token_type != _access_token_type:
        return json_error(f"subject_token_type must be {_access_token_type}, not {subject_token_type}")
    if requested_token_type != _access_token_type:
        return json_error(f"requested_token_type must be {_access_token_type}, not {requested_token_type}")
    if audience != settings.SITE_URL:
        return json_error(f"audience must be {settings.SITE_URL}, not {audience}")
    if scope != "openid":
        return json_error(f"Only 'openid' scope is supported, not {scope}")

    # lookup via userinfo/introspection
    # sample SMART-on-FHIR doesn't have userinfo
    # curl -X POST -H "Authorization: Bearer $token" -d "token=$token" $introspection
    trusted_idp = settings.TRUSTED_TOKEN_IDP
    r = requests.get(
        f"{trusted_idp}/.well-known/openid-configuration",
        headers={"Accept": "application/json"},
    )
    r.raise_for_status()
    openid_config = r.json()

    # TODO: do we need config to select external id claim? Currently hardcoded 'sub'
    external_id_claim = "sub"

    if "userinfo_endpoint" in openid_config:
        # fetch userinfo
        url = openid_config["userinfo_endpoint"]
        logger.info("Looking up token via userinfo %s", url)
        r = requests.get(url, headers={"Authorization": f"Bearer {subject_token}"})
        if r.status_code >= 400:
            logger.warning("Failed to lookup subject_token %s: %s", r.status_code, r.text)
            return json_error(f"Token not found in {trusted_idp}")
        user_info = r.json()
        if external_id_claim not in user_info:
            logger.error("%s not in %s", external_id_claim, user_info)
            return json_error("Error retrieving user info for access token", status_code=500)
        identifier = user_info[external_id_claim]
    elif "introspection_endpoint" in openid_config:
        url = openid_config["introspection_endpoint"]
        logger.info("Looking up token via introspection %s", url)
        r = requests.post(url, data={"token": subject_token}, headers={"Authorization": f"Bearer {subject_token}"})
        if r.status_code >= 400:
            logger.warning("Failed to lookup subject_token %s: %s", r.status_code, r.text)
            return json_error(f"Token not found in {trusted_idp}")
        token_info = r.json()
        # introspection must always set 'active'
        if not token_info["active"]:
            logger.warning("subject_token not active")
            return json_error(f"Token not found in {trusted_idp}")

        if external_id_claim in token_info:
            identifier = token_info[external_id_claim]
        else:
            logger.error("%s not in %s", external_id_claim, token_info)
            if "fhirUser" in token_info:
                kind, _, fhir_id = token_info["fhirUser"].partition("/")
                if kind == "Practitioner":
                    identifier = fhir_id
                else:
                    return json_error("Error introspecting access token", status_code=500)
            else:
                return json_error("Error introspecting access token", status_code=500)

    try:
        user = JheUser.objects.get(identifier=identifier)
    except JheUser.DoesNotExist:
        return json_error(f"Practitioner not found for {identifier}", status_code=404)

    # only allowed for Practitioners
    if not user.practitioner:
        return json_error(f"Practitioner not found for {identifier}", status_code=404)
    # issue token
    access_token = secrets.token_urlsafe(32)
    oauth_request = Request("")
    oauth_request.user = user
    validator = OAuth2Validator()
    validator.save_bearer_token(
        {
            "access_token": access_token,
            "scope": "openid",
        },
        oauth_request,
    )

    # get record from db
    token_model = AccessToken.objects.get(token=access_token)
    expires_in = int((token_model.expires - timezone.now()).total_seconds())

    return JsonResponse(
        {
            "access_token": access_token,
            "issued_token_type": _access_token_type,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "scope": scope,
        }
    )
