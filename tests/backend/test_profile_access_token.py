"""Issue #245: the dashboard session access token must be retrievable from a
discoverable place (the Profile page), not only the superuser-only Debug page.

The token itself is client-side (OIDC userManager), so this asserts the Profile
template ships the access-token section and its copy affordance."""

from django.urls import reverse


def test_profile_page_exposes_dashboard_access_token_section(db, client):
    resp = client.get(reverse("portal", kwargs={"path": ""}))
    assert resp.status_code == 200
    html = resp.content.decode()
    # The Profile page carries a copyable dashboard access token.
    assert 'id="dashboardAccessToken"' in html
    assert "copyDashboardAccessToken(" in html
