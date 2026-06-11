import logging

from django.shortcuts import render
from oauth2_provider.models import get_application_model
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.models import DataSource, PatientIdentifier

logger = logging.getLogger(__name__)
Application = get_application_model()

MYCHART_CLIENT_NAME = "MyChart"
MYCHART_DATA_SOURCE_NAME = "Epic MyChart"


def _mychart_aux_data():
    """Epic config from the seeded MyChart JheClient.aux_data (iss/client_id/scopes)."""
    app = Application.objects.filter(name=MYCHART_CLIENT_NAME).select_related("jhe_client").first()
    if app is None or getattr(app, "jhe_client", None) is None:
        return {}
    return app.jhe_client.aux_data or {}


def _config_context():
    aux = _mychart_aux_data()
    # The FhirSource the browser registers for the pulled Labs needs a DataSource id;
    # expose the seeded "Epic MyChart" DataSource so the client can send it.
    data_source = DataSource.objects.filter(name=MYCHART_DATA_SOURCE_NAME).first()
    return {
        "mychart_iss": aux.get("iss", ""),
        "mychart_client_id": aux.get("client_id", ""),
        "mychart_scopes": aux.get("scopes", ""),
        "mychart_data_source_id": data_source.id if data_source else "",
    }


def mychart_connect(request):
    """Patient-facing start page: invitation -> JHE token -> Epic authorize."""
    return render(request, "clients/mychart/connect.html", _config_context())


def mychart_callback(request):
    """Return page: FHIR.oauth2.ready() -> store id -> pull Labs -> write to JHE."""
    return render(request, "clients/mychart/callback.html", _config_context())


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def save_patient_identifier(request):
    """
    POST /api/v1/mychart/identifier  {system, value}
    Additively attach an external identifier (the Epic patient id) to the
    authenticated patient. get_or_create keeps it idempotent and never replaces
    the patient's other identifiers (unlike the practitioner PATCH path).
    """
    patient = request.user.get_patient()
    if patient is None:
        return Response({"error": "Authenticated user is not a patient"}, status=400)

    system = request.data.get("system")
    value = request.data.get("value")
    if not system or not value:
        return Response({"error": "system and value are required"}, status=400)

    PatientIdentifier.objects.get_or_create(system=system, value=value, defaults={"patient": patient})
    return Response({"system": system, "value": value})
