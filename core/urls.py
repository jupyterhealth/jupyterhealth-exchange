from django.urls import include, path, re_path
from django.views.generic import TemplateView
from rest_framework.routers import DefaultRouter

from core.fhir.config import FHIR_VERSION

from . import views
from .views import common, ow, patient_access
from .views.fhir import FHIRResourceView
from .views.fhir_import import FHIRImportView


def fhir_urls(prefix):
    """Routes (batch / collection / instance) for a FHIR base path `prefix`.

    `prefix` ends in a slash (e.g. "FHIR/R5/"). The bundle-batch base is registered both
    with and without the trailing slash so POST /FHIR/R5 and POST /FHIR/R5/ both work
    (APPEND_SLASH only 301-redirects, which drops the POST body).
    """
    batch = views.FHIRBase.as_view({"post": "create"})
    return [
        path(prefix, batch, name="fhir-batch"),
        path(prefix.rstrip("/"), batch, name="fhir-batch-no-slash"),
        path(f"{prefix}<str:resource>", FHIRResourceView.as_view(), name="fhir-resource"),
        path(f"{prefix}<str:resource>/<str:id>", FHIRResourceView.as_view(), name="fhir-resource-instance"),
    ]


# https://www.django-rest-framework.org/api-guide/routers/#defaultrouter
api_router = DefaultRouter(trailing_slash=False)
api_router.register(r"jhe_settings", views.JheSettingViewSet, basename="JheSetting")
api_router.register(r"users", views.JheUserViewSet, basename="JheUser")
api_router.register(r"practitioners", views.PractitionerViewSet, basename="Practitioner")
api_router.register(r"practitioner_clients", views.PractitionerClientViewSet, basename="PractitionerClient")
api_router.register(r"organizations", views.OrganizationViewSet, basename="Organization")
api_router.register(r"patients", views.PatientViewSet, basename="Patient")
api_router.register(r"studies", views.StudyViewSet, basename="Study")
api_router.register(r"observations", views.ObservationViewSet, basename="Observation")
api_router.register(r"data_sources", views.DataSourceViewSet, basename="DataSource")
api_router.register(r"clients", views.ClientViewSet, basename="Client")
api_router.register(r"fhir_sources", views.FhirSourceViewSet, basename="FhirSource")
api_router.register(r"invitation", views.PatientInvitationViewSet, basename="PatientInvitation")


# snake_case instead of kebab-case because Djano @action decoratrors don't support hyphens
urlpatterns = [
    # Health check (no auth, no DB)
    path("health", common.health, name="health"),
    # Home
    path("", common.home, name="home"),
    # OW Portal
    path("clients/ow/", common.ow_client, name="ow_client"),
    path("clients/ow/complete", common.ow_client_complete, name="ow_client_complete"),
    # Django auth and accounts
    path("accounts/login/", common.LoginView.as_view(), name="login"),
    path("accounts/login-otp/", common.request_login_otp, name="login-otp"),
    path("accounts/signup/", common.signup, name="signup"),
    path("accounts/logout/", common.logout, name="logout"),
    path("accounts/profile/", common.profile, name="profile"),
    path("accounts/verify_email/", common.verify_email, name="verify_email"),
    path("accounts/verify_email_done", common.verify_email_done, name="verify_email_done"),
    path(r"sso/acs/", common.acs, name="acs"),
    path(
        "accounts/verify_email_confirm/<user_id_base64>/<token>/",
        common.verify_email_confirm,
        name="verify_email_confirm",
    ),
    path(
        "accounts/verify_email_complete/",
        common.verify_email_complete,
        name="verify_email_complete",
    ),
    # Client Auth
    path("auth/callback/", common.client_auth_callback, name="client_auth_callback"),
    path(
        "auth/callback_popup/",
        common.client_auth_callback_popup,
        name="client_auth_callback_popup",
    ),
    path("auth/login/", common.client_auth_login, name="client-auth-login"),
    # oauth token exchange
    path("o/token-exchange", common.token_exchange, name="token-exchange"),
    # OW Client pages
    path("clients/ow/launch", common.ow_launch, name="ow-launch"),
    path("clients/ow/complete", common.ow_complete, name="ow-complete"),
    path("clients/ow/manage", common.ow_manage, name="ow-manage"),
    # Patient Access patient EHR-records client (issue #489)
    path("clients/patient-access/", patient_access.patient_access_connect, name="patient-access-connect"),
    path("clients/patient-access/callback", patient_access.patient_access_callback, name="patient-access-callback"),
    path("api/v1/patient-access/brands", patient_access.brands_search, name="patient-access-brands"),
    path("api/v1/patient-access/identifier", patient_access.save_patient_identifier, name="patient-access-identifier"),
    # OW API proxy endpoints
    path("api/v1/ow/users", ow.create_ow_user, name="ow-create-user"),
    path("api/v1/ow/oauth/oura/authorize", ow.get_oura_auth_url, name="ow-oura-authorize"),
    path("api/v1/oauth/oura/callback", ow.oura_oauth_callback, name="ow-oura-callback"),
    path("api/v1/ow/sync", ow.sync_ow_data, name="ow-sync"),
    # Client UI
    path(
        "common/server-settings.js",
        TemplateView.as_view(template_name="common/server_settings.js", content_type="text/javascript"),
    ),
    # path('clients/jhe-admin/', common.portal, name='portal'),
    re_path(r"^clients/jhe-admin/(?P<path>([^/]+/)*)$", common.portal, name="portal"),
    # JHE Admin Client API
    path("api/v1/", include(api_router.urls)),
    # FHIR API. One unified resource endpoint; the resource type in the URL is resolved
    # against core/fhir/fhir_config.json (mapped vs auxiliary). The bundle batch lives at
    # the base (registered with and without a trailing slash). The base is FHIR/<version>/
    # (version from the config).
    *fhir_urls(f"FHIR/{FHIR_VERSION}/"),
    # R4 ingestion: convert an R4 body (or Bundle) to R5, then reuse the normal create routing.
    # The base (with and without trailing slash) takes a Bundle; the collection path takes one
    # resource. See core/views/fhir_import.py.
    path("fhir-import/R4/", FHIRImportView.as_view(), name="fhir-import-bundle"),
    path("fhir-import/R4", FHIRImportView.as_view(), name="fhir-import-bundle-no-slash"),
    path("fhir-import/R4/<str:resource>", FHIRImportView.as_view(), name="fhir-import-resource"),
]
