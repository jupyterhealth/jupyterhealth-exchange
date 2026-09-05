from django.db.models import Prefetch
from oauth2_provider.models import get_application_model

from core.models import ClientDataSource
from core.services.jhe_settings import get_setting

Application = get_application_model()


def _expected_resource_types(scopes):
    """Resource types named by the client's patient/<Type>.read SMART scopes; [] for a client without them."""
    types = {
        s.removeprefix("patient/").removesuffix(".read")
        for s in scopes.split()
        if s.startswith("patient/") and s.endswith(".read")
    }
    # patient/*.read names no type, so it would otherwise show up as a "*" row on the receipt.
    return sorted(types - {"*"})


def patient_portal_config(client_name, client_key, page_url):
    """The PATIENT_PORTAL_CONFIG a client page injects: its OAuth config and the DataSources linked through ClientDataSource."""
    app = (
        Application.objects.filter(name=client_name)
        .select_related("jhe_client")
        .prefetch_related(
            Prefetch("data_sources", queryset=ClientDataSource.objects.select_related("data_source").order_by("id"))
        )
        .first()
    )
    jhe_client = getattr(app, "jhe_client", None) if app else None
    aux = (jhe_client.aux_data if jhe_client else None) or {}
    links = list(app.data_sources.all()) if app else []
    return {
        "client": client_key,
        "clientId": aux.get("client_id", ""),
        "scope": aux.get("scopes", ""),
        "dataSourceIds": [link.data_source_id for link in links],
        "sourceLabels": {str(link.data_source_id): link.data_source.name for link in links},
        "expectedResourceTypes": _expected_resource_types(aux.get("scopes", "")),
        "siteTitle": get_setting("site.ui.title"),
        "pageUrl": page_url,
    }
