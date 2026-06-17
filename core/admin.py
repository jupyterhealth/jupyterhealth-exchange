from django.contrib import admin

from core.models import (
    ClientDataSource,
    CodeableConcept,
    DataSource,
    DataSourceSupportedScope,
    FhirAuxResource,
    FhirSource,
    JheClient,
    JheSetting,
    JheUser,
    Observation,
    ObservationIdentifier,
    Organization,
    Patient,
    PatientIdentifier,
    PatientInvitation,
    PatientOrganization,
    Practitioner,
    PractitionerClient,
    PractitionerOrganization,
    Study,
    StudyClient,
    StudyDataSource,
    StudyPatient,
    StudyPatientScopeConsent,
    StudyScopeRequest,
)


@admin.register(JheUser)
class JheUserAdmin(admin.ModelAdmin):
    list_display = ("email", "first_name", "last_name", "identifier", "is_staff", "is_active")
    search_fields = ("email", "first_name", "last_name", "identifier")
    list_filter = ("is_staff", "is_active", "is_superuser")
    # The groups / user_permissions M2M tables were dropped (migration 0011), so the default
    # admin machinery crashes when it touches them. Hide the fields and route every delete path
    # through the model's safe JheUser.delete() (which never references those tables).
    exclude = ("groups", "user_permissions")

    def get_deleted_objects(self, objs, request):
        # Skip Django's collector (it walks the dropped M2M relations and raises
        # ProgrammingError). The cascade is handled by JheUser.delete().
        to_delete = [str(obj) for obj in objs]
        model_count = {JheUser._meta.verbose_name_plural: len(to_delete)}
        return to_delete, model_count, set(), []

    def delete_model(self, request, obj):
        obj.delete()

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            obj.delete()


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
    list_display = ("__str__", "email", "birth_date", "id")
    search_fields = ("name_given", "name_family", "jhe_user__email", "identifiers__value")

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
    list_display = ("id", "patient_name", "user_id", "scope", "source_name", "status", "ow_key_short", "last_updated")
    search_fields = ("ow_key", "subject_patient__name_family", "subject_patient__name_given")
    list_filter = ("status", "codeable_concept", "data_source")
    raw_id_fields = ("subject_patient", "codeable_concept", "data_source")

    @admin.display(description="Patient")
    def patient_name(self, obj):
        p = obj.subject_patient
        return f"{p.name_family}, {p.name_given}"

    @admin.display(description="User ID")
    def user_id(self, obj):
        # The patient's JHE-generated account id (jheUserId), distinct from the Patient record id.
        return obj.subject_patient.jhe_user_id

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


@admin.register(JheClient)
class JheClientAdmin(admin.ModelAdmin):
    list_display = ("application", "invitation_url")
    search_fields = ("application__name",)


@admin.register(PractitionerClient)
class PractitionerClientAdmin(admin.ModelAdmin):
    list_display = ("id", "practitioner", "application", "label")
    search_fields = ("label", "application__name", "practitioner__name_family", "practitioner__name_given")
    raw_id_fields = ("application", "practitioner")


@admin.register(JheSetting)
class JheSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "value_type", "last_updated")
    search_fields = ("key",)
    list_filter = ("value_type",)


# FHIR-native resources (auxiliary store + the patient-registered source records). These are the
# records we create/inspect remotely instead of asking the operator to run a SQL script.
@admin.register(FhirSource)
class FhirSourceAdmin(admin.ModelAdmin):
    list_display = ("id", "label", "patient", "data_source", "fhir_base_url", "last_updated")
    search_fields = ("label", "fhir_base_url", "patient__name_family", "patient__name_given")
    raw_id_fields = ("patient", "data_source")


@admin.register(FhirAuxResource)
class FhirAuxResourceAdmin(admin.ModelAdmin):
    list_display = ("id", "resource_type", "fhir_resource_id", "patient_fhir_id", "fhir_source", "last_updated")
    search_fields = ("resource_type", "fhir_resource_id", "patient_fhir_id")
    list_filter = ("resource_type",)
    raw_id_fields = ("fhir_source",)


@admin.register(PatientIdentifier)
class PatientIdentifierAdmin(admin.ModelAdmin):
    list_display = ("id", "patient", "system", "value")
    search_fields = ("system", "value", "patient__name_family", "patient__name_given")
    raw_id_fields = ("patient",)


@admin.register(PatientOrganization)
class PatientOrganizationAdmin(admin.ModelAdmin):
    list_display = ("id", "patient", "organization")
    search_fields = ("patient__name_family", "patient__name_given", "organization__name")
    raw_id_fields = ("patient", "organization")


@admin.register(PatientInvitation)
class PatientInvitationAdmin(admin.ModelAdmin):
    list_display = ("id", "patient", "client", "status", "last_updated")
    search_fields = ("patient__name_family", "patient__name_given")
    list_filter = ("status",)
    raw_id_fields = ("patient", "client")


@admin.register(ObservationIdentifier)
class ObservationIdentifierAdmin(admin.ModelAdmin):
    list_display = ("id", "observation", "system", "value")
    search_fields = ("system", "value")
    raw_id_fields = ("observation",)


@admin.register(PractitionerOrganization)
class PractitionerOrganizationAdmin(admin.ModelAdmin):
    list_display = ("id", "practitioner", "organization", "role")
    search_fields = ("practitioner__name_family", "practitioner__name_given", "organization__name")
    list_filter = ("role",)
    raw_id_fields = ("practitioner", "organization")


@admin.register(StudyPatient)
class StudyPatientAdmin(admin.ModelAdmin):
    list_display = ("id", "study", "patient")
    search_fields = ("study__name", "patient__name_family", "patient__name_given")
    raw_id_fields = ("study", "patient")


@admin.register(StudyPatientScopeConsent)
class StudyPatientScopeConsentAdmin(admin.ModelAdmin):
    list_display = ("id", "study_patient", "scope_code", "scope_actions", "consented", "consented_time")
    list_filter = ("consented",)
    raw_id_fields = ("study_patient", "scope_code")


@admin.register(StudyScopeRequest)
class StudyScopeRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "study", "scope_code", "scope_actions")
    search_fields = ("study__name",)
    raw_id_fields = ("study", "scope_code")


@admin.register(StudyDataSource)
class StudyDataSourceAdmin(admin.ModelAdmin):
    list_display = ("id", "study", "data_source")
    search_fields = ("study__name", "data_source__name")
    raw_id_fields = ("study", "data_source")


@admin.register(StudyClient)
class StudyClientAdmin(admin.ModelAdmin):
    list_display = ("id", "study", "client")
    search_fields = ("study__name",)
    raw_id_fields = ("study", "client")


@admin.register(DataSourceSupportedScope)
class DataSourceSupportedScopeAdmin(admin.ModelAdmin):
    list_display = ("id", "data_source", "scope_code")
    search_fields = ("data_source__name",)
    raw_id_fields = ("data_source", "scope_code")


@admin.register(ClientDataSource)
class ClientDataSourceAdmin(admin.ModelAdmin):
    list_display = ("id", "client", "data_source")
    search_fields = ("data_source__name",)
    raw_id_fields = ("client", "data_source")
