from django.urls import include, path, re_path
from django.views.generic import TemplateView
from rest_framework.routers import DefaultRouter

from . import views
from .views import common

# https://www.django-rest-framework.org/api-guide/routers/#defaultrouter
api_router = DefaultRouter(trailing_slash=False)
api_router.register(r"jhe_settings", views.JheSettingViewSet, basename="JheSetting")
api_router.register(r"users", views.JheUserViewSet, basename="JheUser")
api_router.register(r"practitioners", views.PractitionerViewSet, basename="Practitioner")
api_router.register(r"organizations", views.OrganizationViewSet, basename="Organization")
api_router.register(r"patients", views.PatientViewSet, basename="Patient")
api_router.register(r"studies", views.StudyViewSet, basename="Study")
api_router.register(r"observations", views.ObservationViewSet, basename="Observation")
api_router.register(r"data_sources", views.DataSourceViewSet, basename="DataSource")
api_router.register(r"clients", views.ClientViewSet, basename="Client")

fhir_router = DefaultRouter(trailing_slash=False)
fhir_router.register(r"Observation", views.FHIRObservationViewSet, basename="FHIRObservation")
fhir_router.register(r"Patient", views.FHIRPatientViewSet, basename="FHIRPatient")
fhir_router.register(r"", views.FHIRBase, basename="FHIRBase")

# snake_case instead of kebab-case because Djano @action decoratrors don't support hyphens
urlpatterns = [
    # Home
    path("", common.home, name="home"),
    # Django auth and accounts
    path("accounts/login/", common.LoginView.as_view(), name="login"),
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
    # Smart Launch
    # path("smart/launch", common.smart_launch, name="smart-launch"),
    # path("smart/callback/", common.smart_callback, name="smart-callback"),
    # Client UI
    path(
        "portal/client_settings.js",
        TemplateView.as_view(template_name="client/client_settings.js", content_type="text/javascript"),
    ),
    # path('portal/', common.portal, name='portal'),
    re_path(r"^portal/(?P<path>([^/]+/)*)$", common.portal, name="portal"),
    # Admin API
    path("api/v1/", include(api_router.urls)),
    # FHIR API
    path("fhir/r5/", include(fhir_router.urls)),
]
