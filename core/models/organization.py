from django.db import models
from django.db.models import Q

from .patient import PatientOrganization
from .practitioner import PractitionerOrganization


class Organization(models.Model):
    # https://build.fhir.org/valueset-organizations-type.html
    ORGANIZATION_TYPES = {
        "root": "ROOT",
        "prov": "Healthcare Provider",
        "dept": "Hospital Department",
        "team": "Organizational team",
        "govt": "Government",
        "ins": "Insurance Company",
        "pay": "Payer",
        "edu": "Educational Institute",
        "reli": "Religious Institution",
        "crs": "Clinical Research Sponsor",
        "cg": "Community Group",
        "bus": "Non-Healthcare Business or Corporation",
        "other": "Other",
        "laboratory": "Laboratory",
        "imaging": "Imaging Center",
        "pharmacy": "Pharmacy",
        "health-information-network": "Health Information Network",
        "health-data-aggregator": "Health Data Aggregator",
    }

    name = models.CharField()
    type = models.CharField(choices=list(ORGANIZATION_TYPES.items()), null=False, blank=False)
    part_of = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True)

    def __str__(self):
        return self.name or f"Organization {self.pk}"

    # Helper method to return all users in this organization
    @property
    def users(self):
        from .jhe_user import JheUser  # lazy import to avoid circular with jhe_user → organization

        patient_user_ids = (
            PatientOrganization.objects.filter(organization=self)
            .select_related("patient__jhe_user")
            .values_list("patient__jhe_user_id", flat=True)
        )

        practitioner_user_ids = (
            PractitionerOrganization.objects.filter(organization=self)
            .select_related("practitioner__jhe_user")
            .values_list("practitioner__jhe_user_id", flat=True)
        )

        # Combine the IDs and get all of the users
        return JheUser.objects.filter(Q(id__in=patient_user_ids) | Q(id__in=practitioner_user_ids))

    @staticmethod
    def collect_children(parent):
        children = Organization.get_children(parent.id)
        for child in children:
            parent.children.append(child)
            Organization.collect_children(child)

    @staticmethod
    def get_children(parent_id):
        return Organization.objects.filter(part_of=parent_id).order_by("name")

    @staticmethod
    def for_practitioner(practitioner_user_id):
        q = """
            SELECT core_organization.*
            FROM core_organization
            JOIN core_practitionerorganization ON core_practitionerorganization.organization_id=core_organization.id
            JOIN core_practitioner ON core_practitioner.id=core_practitionerorganization.practitioner_id
            WHERE core_practitioner.jhe_user_id=%(practitioner_user_id)s
            """

        return Organization.objects.raw(q, {"practitioner_user_id": practitioner_user_id})

    @staticmethod
    def for_patient(patient_user_id):
        q = """
            SELECT core_organization.*
            FROM core_organization
            JOIN core_patientorganization ON core_patientorganization.organization_id=core_organization.id
            JOIN core_patient ON core_patient.id=core_patientorganization.patient_id
            WHERE core_patient.jhe_user_id=%(patient_user_id)s
            """

        return Organization.objects.raw(q, {"patient_user_id": patient_user_id})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.children = []
