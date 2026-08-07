from django.db import migrations


def delete_superseded_idp_metadata_url_setting(apps, schema_editor):
    # IdP config moved to an allauth SocialApp row (#697); nothing
    # reads this key anymore, so drop it from already-seeded databases where it
    # would otherwise linger as an editable-but-dead settings-admin row. The
    # seeded value was always "" (SAML was never operable), so nothing is lost.
    JheSetting = apps.get_model("core", "JheSetting")
    JheSetting.objects.filter(key="auth.sso.idp_metadata_url").delete()


class Migration(migrations.Migration):
    # Numbered 0045: #681 ships 0043/0044 on the same 0042 parent. Whichever
    # PR merges second flips this dependency to the other's leaf (e.g.
    # "0044_aux_upstream_uniqueness") — one line, no rename needed.
    dependencies = [
        ("core", "0042_ehrbrand_ehrbrandlocation"),
    ]

    operations = [
        migrations.RunPython(
            delete_superseded_idp_metadata_url_setting,
            migrations.RunPython.noop,
        ),
    ]
