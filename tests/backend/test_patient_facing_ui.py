"""The shared patient-facing chrome: stylesheet, vendored font, header, Open Wearables pages, invitation email."""

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.core.cache import cache
from django.template.loader import render_to_string

from core.context_processors import DEFAULT_LOGO
from core.models import JheSetting

STATIC = Path(settings.BASE_DIR) / "core" / "static"
CSS = (STATIC / "common" / "css" / "patient-facing.css").read_text()


def _rule(selector):
    return CSS.split(f"\n{selector} {{", 1)[1].split("}", 1)[0]


def _header(html):
    return html.split('<header class="pf-header">', 1)[1].split("</header>", 1)[0]


@pytest.mark.parametrize("weight", [400, 500, 600, 700])
def test_inter_woff2_weights_are_vendored(weight):
    font = STATIC / "common" / "css" / "fonts" / "inter" / f"inter-{weight}.woff2"
    assert font.read_bytes()[:4] == b"wOF2"
    assert font.stat().st_size > 10_000


def test_patient_facing_css_defines_tokens_and_component_hooks():
    assert "@font-face" in CSS and "fonts/inter/inter-400.woff2" in CSS
    assert "https://" not in CSS, "no external fetches allowed under strict CSP"
    for token in (
        "--pf-bg",
        "--pf-surface",
        "--pf-ink",
        "--pf-muted",
        "--pf-accent",
        "--pf-accent-ink",
        "--pf-line",
        "--pf-radius",
        "--pf-radius-lg",
        "--pf-font",
        "--pf-maxw",
        "--pf-danger-soft",
    ):
        assert token in CSS, f"missing design token {token}"
    for cls in (
        ".pf-page",
        ".pf-header",
        ".pf-eyebrow",
        ".pf-h1",
        ".pf-lede",
        ".pf-card",
        ".pf-card__badge",
        ".pf-card__icon",
        ".pf-rail",
        ".pf-callout",
        ".pf-actions",
        ".pf-back",
        ".pf-error",
        ".pf-error__msg",
        ".pf-import #out",
        ".pf-progress__bar",
        ".pf-receipt",
        ".pf-receipt__row",
        ".pf-receipt__n",
        ".pf-receipt__heading",
    ):
        assert cls in CSS, f"missing component class {cls}"


def test_patient_facing_css_layout_rules():
    assert "#hospital-search:placeholder-shown ~ #hospital-results" in CSS  # results hidden until the patient types
    check_circle = _rule(".pf-check-circle")
    assert "display: flex" in check_circle and "inline-flex" not in check_circle  # own line below the back button
    ghost = _rule(".pf-btn--ghost")
    assert "padding" not in ghost and "margin-top" not in ghost  # shares .pf-btn sizing inside a .pf-actions row


def test_patient_pages_link_the_versioned_stylesheet(db, client):
    html = client.get("/clients/ehr-patient-portal/").content.decode()
    assert f"common/css/patient-facing.css?v={settings.JHE_VERSION}" in html
    assert 'class="pf-page"' in html


def test_header_shows_the_default_lockup_without_title_text(db, client):
    assert (STATIC / DEFAULT_LOGO).stat().st_size > 5000
    header = _header(client.get("/clients/ehr-patient-portal/").content.decode())
    assert f'src="/static/{DEFAULT_LOGO}"' in header
    assert "Secure" in header
    brand = header.split('class="pf-header__brand">', 1)[1].split("</span>", 1)[0]
    assert re.sub(r"<img[^>]*>", "", brand).strip() == ""  # the lockup carries the name; no text beside it


def test_header_shows_a_custom_logo_with_the_site_title(db, client):
    setting = JheSetting(key="site.ui.logo")
    setting.set_value("string", "clients/ehr-patient-portal/images/meridian.svg")
    setting.save()
    cache.delete("jhe_setting:site.ui.logo")

    html = client.get("/clients/ehr-patient-portal/").content.decode()

    header = _header(html)
    assert 'src="/static/clients/ehr-patient-portal/images/meridian.svg"' in header
    site_title = re.search(r"<title>(.*?) - ", html).group(1)
    assert site_title in header.split("pf-header__secure", 1)[0].replace(f'alt="{site_title}"', "")
    cache.delete("jhe_setting:site.ui.logo")


def test_theme_stylesheet_setting_loads_after_the_base_stylesheet(db, client):
    setting = JheSetting(key="site.ui.theme_css")
    setting.set_value("string", "common/css/themes/acme.css")
    setting.save()
    cache.delete("jhe_setting:site.ui.theme_css")

    html = client.get("/clients/ehr-patient-portal/").content.decode()

    base = html.index("common/css/patient-facing.css")
    theme = html.index("/static/common/css/themes/acme.css")
    assert base < theme  # the theme's :root tokens win by cascade order
    cache.delete("jhe_setting:site.ui.theme_css")


def test_ow_complete_hands_off_to_the_client_page(db, client):
    assert client.get("/clients/ow/manage").status_code == 404  # legacy JS manage page retired

    resp = client.get("/clients/ow/complete?provider=oura")
    assert (resp.status_code, resp["Location"]) == (302, "/clients/ow/launch?route=done")

    resp = client.get("/clients/ow/complete?error=access_denied")
    assert resp.status_code == 302
    assert resp["Location"].startswith("/clients/ow/launch?route=error&")
    assert "message=access_denied" in resp["Location"]
    assert "title=We+couldn%27t+connect+your+wearable" in resp["Location"]


def _render_email(**extra):
    return render_to_string(
        "registration/invitation_email.html",
        {
            "patient_name": "Maria",
            "invitation_link": "https://jhe.example/redeem?code=abc",
            "site_url": "https://jhe.example",
            "site_title": "Meridian Exchange",
            "site_logo": DEFAULT_LOGO,
            **extra,
        },
    )


def test_invitation_email_is_branded_and_inline_styled():
    html = _render_email(study_name="Cardiometabolic Health Study")
    assert "Hi Maria" in html and "Meridian Exchange" in html
    assert f'src="https://jhe.example/static/{DEFAULT_LOGO}"' in html
    assert 'href="https://jhe.example/redeem?code=abc"' in html
    assert ">https://jhe.example/redeem?code=abc<" not in html  # the link is the button, not visible text
    assert "Get started" in html and "style=" in html
    assert "<strong>Cardiometabolic Health Study</strong>" in html
    assert "You received this because a study team invited you" in html


def test_invitation_email_uses_generic_copy_without_a_single_pending_study():
    html = _render_email()
    assert "invited to securely share" in html and "<strong>" not in html
