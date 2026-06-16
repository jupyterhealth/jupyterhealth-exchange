from rest_framework.test import APIClient

from core.models import JheSetting


def test_list_settings_pagination_is_ordered(superuser, recwarn):
    # The settings list is paginated, so its queryset must have a stable order;
    # otherwise DRF emits an UnorderedObjectListWarning and pages can skip/repeat rows (issue #589).
    for i in range(15):
        setting = JheSetting(key=f"order.test.{i:02d}", value_type="string")
        setting.set_value("string", "v")
        setting.save()
    api_client = APIClient()
    api_client.default_format = "json"
    api_client.force_authenticate(superuser)
    r = api_client.get("/api/v1/jhe_settings", {"pageSize": 10})
    assert r.status_code == 200, r.text
    unordered = [w for w in recwarn.list if w.category.__name__ == "UnorderedObjectListWarning"]
    assert not unordered, [str(w.message) for w in unordered]
    ids = [row["id"] for row in r.json()["results"]]
    assert ids == sorted(ids)
