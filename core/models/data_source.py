from django.conf import settings
from django.db import models

from .codeable_concept import CodeableConcept


class DataSource(models.Model):
    DATA_SOURCE_TYPES = {"medical_device": "Medical Device", "personal_device": "Personal Device"}
    name = models.CharField(null=True, blank=False)
    type = models.CharField(
        choices=list(DATA_SOURCE_TYPES.items()),
        null=False,
        blank=False,
        default="personal_device",
    )

    def __str__(self):
        return self.name or f"DataSource {self.pk}"

    # this will never be large
    @staticmethod
    def data_sources_with_scopes(data_source_id=None, study_id=None):
        # Explicitly cast to ints so no injection vulnerability
        sql_where = ""
        sql_join = ""
        if data_source_id:
            sql_where = f"WHERE core_datasource.id={int(data_source_id)}"
        elif study_id:
            sql_join = "JOIN core_studydatasource ON core_studydatasource.data_source_id=core_datasource.id"
            sql_where = f"WHERE core_studydatasource.study_id={int(study_id)}"

        q = f"""
            SELECT core_datasource.*
            FROM core_datasource
            {sql_join}
            {sql_where}
            ORDER BY core_datasource.name
            """

        data_sources = list(DataSource.objects.raw(q))

        q = """
            SELECT core_codeableconcept.*
            FROM core_codeableconcept
            JOIN core_datasourcesupportedscope ON core_datasourcesupportedscope.scope_code_id=core_codeableconcept.id
            WHERE core_datasourcesupportedscope.data_source_id=%(data_source_id)s
            ORDER BY text
            """

        for data_source in data_sources:
            for scope in CodeableConcept.objects.raw(q, {"data_source_id": data_source.id}):
                data_source.supported_scopes.append(scope)

        return data_sources

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.supported_scopes = []


class DataSourceSupportedScope(models.Model):
    data_source = models.ForeignKey(DataSource, on_delete=models.CASCADE)
    scope_code = models.ForeignKey("CodeableConcept", on_delete=models.CASCADE)


class ClientDataSource(models.Model):
    client = models.ForeignKey(
        settings.OAUTH2_PROVIDER_APPLICATION_MODEL,
        on_delete=models.CASCADE,
        related_name="data_sources",
    )
    data_source = models.ForeignKey(
        DataSource,
        on_delete=models.CASCADE,
        related_name="client_applications",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["client", "data_source"],
                name="core_clientdatasource_unique_client_id_data_source_id",
            )
        ]
