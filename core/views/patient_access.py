import logging

from django.db.models import Q
from django.shortcuts import render
from oauth2_provider.models import get_application_model
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.models import DataSource, EhrBrandLocation, PatientIdentifier

logger = logging.getLogger(__name__)
Application = get_application_model()

PATIENT_ACCESS_CLIENT_NAME = "Patient Access"
PATIENT_ACCESS_DATA_SOURCE_NAME = "Patient Access API"
BRANDS_DEFAULT_LIMIT = 25
BRANDS_MAX_LIMIT = 100


def _patient_access_aux_data():
    """EHR config from the seeded Patient Access JheClient.aux_data (client_id/scopes)."""
    app = Application.objects.filter(name=PATIENT_ACCESS_CLIENT_NAME).select_related("jhe_client").first()
    if app is None or getattr(app, "jhe_client", None) is None:
        return {}
    return app.jhe_client.aux_data or {}


def _config_context():
    aux = _patient_access_aux_data()
    # The FhirSource the browser registers for the pulled Labs needs a DataSource id;
    # expose the seeded "Patient Access API" DataSource so the client can send it.
    data_source = DataSource.objects.filter(name=PATIENT_ACCESS_DATA_SOURCE_NAME).first()
    return {
        "patient_access_client_id": aux.get("client_id", ""),
        "patient_access_scopes": aux.get("scopes", ""),
        "patient_access_data_source_id": data_source.id if data_source else "",
    }


def patient_access_connect(request):
    """Patient-facing start page: invitation -> JHE token -> Epic authorize."""
    return render(request, "clients/patient-access/connect.html", _config_context())


def patient_access_callback(request):
    """Return page: FHIR.oauth2.ready() -> store id -> pull Labs -> write to JHE."""
    return render(request, "clients/patient-access/callback.html", _config_context())


def _parse_limit(raw):
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        return BRANDS_DEFAULT_LIMIT
    return max(1, min(limit, BRANDS_MAX_LIMIT))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def brands_search(request):
    """
    GET /api/v1/patient-access/brands?q=&state=&postal=&limit=
    Search hospital facilities for the Patient Access picker. `q` matches facility name,
    city, or brand name; `state`/`postal` filter those columns. Each result carries
    the brand's fhir_base_url (the SMART `iss`) the browser authorizes against.
    """
    qs = EhrBrandLocation.objects.select_related("brand")

    q = (request.query_params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(city__icontains=q) | Q(brand__name__icontains=q))
    state = (request.query_params.get("state") or "").strip()
    if state:
        qs = qs.filter(state__iexact=state)
    postal = (request.query_params.get("postal") or "").strip()
    if postal:
        qs = qs.filter(postal_code__istartswith=postal)

    limit = _parse_limit(request.query_params.get("limit"))
    qs = qs.order_by("brand__name", "name")[:limit]

    results = [
        {
            "facility_name": loc.name,
            "address_text": loc.address_text,
            "city": loc.city,
            "state": loc.state,
            "postal_code": loc.postal_code,
            "brand_name": loc.brand.name,
            "fhir_base_url": loc.brand.fhir_base_url,
        }
        for loc in qs
    ]
    return Response({"results": results})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def save_patient_identifier(request):
    """
    POST /api/v1/patient-access/identifier  {system, value}
    Additively attach an external identifier (the EHR patient id) to the
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

    identifier, created = PatientIdentifier.objects.get_or_create(
        system=system, value=value, defaults={"patient": patient}
    )
    # (system, value) is globally unique, so the existing row may belong to someone else.
    # Reporting 200 there would tell the caller it was attached when it was not.
    if not created and identifier.patient_id != patient.id:
        return Response({"error": "Identifier is already assigned to another patient"}, status=409)
    return Response({"system": system, "value": value})
