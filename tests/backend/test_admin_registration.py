"""
Every concrete model in the ``core`` app must be registered in the Django admin, so the team can
debug and support deployed instances remotely (create/inspect any record, including the FHIR ones,
without a SQL script). See docs/specs/2026-06-12-django-admin-full-model-exposure-design.md.
"""

from django.apps import apps
from django.contrib import admin


def test_all_core_models_registered_in_admin():
    registered = set(admin.site._registry.keys())
    core_models = list(apps.get_app_config("core").get_models())

    missing = sorted(m.__name__ for m in core_models if m not in registered)

    assert not missing, f"core models not registered in Django admin: {missing}"
