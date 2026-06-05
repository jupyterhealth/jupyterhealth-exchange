from django.db import migrations, models


def delete_superseded_client_invitation_url_settings(apps, schema_editor):
    # 0027 moved each client's value into JheClient.invitation_url. With setting_id
    # dropped and key made globally unique, these per-client rows would violate the
    # new unique(key) constraint, so remove the now-redundant rows.
    JheSetting = apps.get_model("core", "JheSetting")
    JheSetting.objects.filter(key="client.invitation_url").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0029_rename_aux_data_to_aux_fhir_data"),
    ]

    operations = [
        migrations.RunPython(
            delete_superseded_client_invitation_url_settings,
            migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name="jhesetting",
            name="core_jhesetting_unique_key_setting_id",
        ),
        migrations.RemoveField(
            model_name="jhesetting",
            name="setting_id",
        ),
        migrations.AlterField(
            model_name="jhesetting",
            name="key",
            field=models.CharField(unique=True),
        ),
    ]
