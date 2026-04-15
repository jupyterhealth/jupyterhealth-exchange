from django.contrib import admin

from core.models import (
    CodeableConcept,
    DataSource,
    JheSetting,
    JheUser,
    Observation,
    Organization,
    Patient,
    Practitioner,
    Study,
)


@admin.register(JheUser)
class JheUserAdmin(admin.ModelAdmin):
    list_display = ("email", "first_name", "last_name", "identifier", "is_staff", "is_active")
    search_fields = ("email", "first_name", "last_name", "identifier")
    list_filter = ("is_staff", "is_active", "is_superuser")


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "part_of", "id")
    search_fields = ("name",)
    list_filter = ("type",)


@admin.register(Practitioner)
class PractitionerAdmin(admin.ModelAdmin):
    list_display = ("__str__", "email", "identifier", "id")
    search_fields = ("name_given", "name_family", "jhe_user__email")

    @admin.display(description="Email")
    def email(self, obj):
        return obj.jhe_user.email if obj.jhe_user else None


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("__str__", "email", "identifier", "birth_date", "id")
    search_fields = ("name_given", "name_family", "jhe_user__email", "identifier")

    @admin.display(description="Email")
    def email(self, obj):
        return obj.jhe_user.email if obj.jhe_user else None


@admin.register(CodeableConcept)
class CodeableConceptAdmin(admin.ModelAdmin):
    list_display = ("text", "coding_code", "coding_system", "id")
    search_fields = ("coding_code", "text")


@admin.register(Study)
class StudyAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "id")
    search_fields = ("name",)


@admin.register(DataSource)
class DataSourceAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "id")
    search_fields = ("name",)
    list_filter = ("type",)


@admin.register(Observation)
class ObservationAdmin(admin.ModelAdmin):
    list_display = ("id", "patient_name", "scope", "source_name", "status", "ow_key_short", "last_updated")
    search_fields = ("ow_key", "subject_patient__name_family", "subject_patient__name_given")
    list_filter = ("status", "codeable_concept", "data_source")
    raw_id_fields = ("subject_patient", "codeable_concept", "data_source")

    @admin.display(description="Patient")
    def patient_name(self, obj):
        p = obj.subject_patient
        return f"{p.name_family}, {p.name_given}"

    @admin.display(description="Scope")
    def scope(self, obj):
        return obj.codeable_concept.text or obj.codeable_concept.coding_code

    @admin.display(description="Source")
    def source_name(self, obj):
        return obj.data_source.name if obj.data_source else None

    @admin.display(description="OW Key")
    def ow_key_short(self, obj):
        if obj.ow_key:
            return "..." + obj.ow_key[-40:]
        return None


@admin.register(JheSetting)
class JheSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "setting_id", "value_type", "last_updated")
    search_fields = ("key",)
    list_filter = ("value_type",)
