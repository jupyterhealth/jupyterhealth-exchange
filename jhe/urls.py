"""
URL configuration for jhe project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

import django_saml2_auth.views
from django.contrib import admin
from django.contrib.auth import views as auth_views  # noqa
from django.urls import include, path, re_path
from drf_spectacular.views import SpectacularRedocView, SpectacularSwaggerView

urlpatterns = [
    path("", include("core.urls")),
    path("admin/", admin.site.urls),
    path("o/", include("oauth2_provider.urls", namespace="oauth2_provider")),
    path("accounts/", include("django.contrib.auth.urls")),
    path("email_auth/", include("allauth.urls")),
    path(
        "api/schema/swagger-ui/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/schema/redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
    path("sso/", include("django_saml2_auth.urls")),
    re_path(r"^saml/login/$", django_saml2_auth.views.signin, name="saml_signin"),
]
