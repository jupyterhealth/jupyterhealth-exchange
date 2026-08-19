from django.db import migrations


def delete_superseded_idp_metadata_url_setting(apps, schema_editor):
    # IdP config moved to an allauth SocialApp row (#697); nothing
    # reads this key anymore, so drop it from already-seeded databases where it
    # would otherwise linger as an editable-but-dead settings-admin row. The
    # seeded value was always "" (SAML was never operable), so nothing is lost.
    JheSetting = apps.get_model("core", "JheSetting")
    JheSetting.objects.filter(key="auth.sso.idp_metadata_url").delete()


class Migration(migrations.Migration):
    # Numbered 0045: #681 (0043/0044) landed on main first, so this depends on
    # its leaf to keep the graph linear (0042 → 0043 → 0044 → 0045) and avoid a
    # "multiple leaf nodes" conflict.
    dependencies = [
        ("core", "0044_aux_upstream_uniqueness"),
    ]

    operations = [
        migrations.RunPython(
            delete_superseded_idp_metadata_url_setting,
            migrations.RunPython.noop,
        ),
    ]
