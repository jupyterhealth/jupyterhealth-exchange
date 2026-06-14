import base64
import json
import logging

from django.core.exceptions import BadRequest, PermissionDenied
from django.db import models
from django.db.models import F, Q
from django.db.utils import IntegrityError
from django.shortcuts import get_object_or_404
from fhir.resources.observation import Observation as FHIRObservation

from core.fhir.scope import authorize_practitioner_scope, resolve_fhir_user
from core.utils import code_to_schema, validate_with_registry

from .codeable_concept import CodeableConcept
from .data_source import DataSource
from .patient import Patient
from .practitioner import Practitioner

logger = logging.getLogger(__name__)


# Observation per record: https://stackoverflow.com/a/61484800 (author worked at ONC)
class Observation(models.Model):
    subject_patient = models.ForeignKey("Patient", on_delete=models.CASCADE)
    # The Django Observation model holds OMH observations only (code system
    # https://w3id.org/openmhealth). Any other FHIR Observation is stored in FhirAuxResource.
    codeable_concept = models.ForeignKey("CodeableConcept", on_delete=models.PROTECT, null=True)
    data_source = models.ForeignKey("DataSource", on_delete=models.SET_NULL, null=True)
    omh_data = models.JSONField(null=True)
    last_updated = models.DateTimeField(auto_now=True)
    ow_key = models.TextField(null=True, blank=True, db_index=True)

    # https://build.fhir.org/valueset-observation-status.html
    OBSERVATION_STATUSES = {
        "registered": "registered",
        "preliminary": "preliminary",
        "final": "final",
        "amended": "amended",
        "corrected": "corrected",
        "appended": "appended",
        "cancelled": "cancelled",
        "entered-in-error": "Entered in Error",
        "unknown": "Unknown",
    }

    status = models.CharField(choices=list(OBSERVATION_STATUSES.items()), null=False, blank=False, default="final")

    class Meta:
        indexes = [
            # Matches the common access pattern: a patient's observations, newest first.
            models.Index(fields=["subject_patient", "-last_updated"]),
        ]

    @property
    def codeable_concepts(self):
        # An OMH Observation has exactly one CodeableConcept; expose it as a one-element
        # iterable so the data-mapping engine fans it out into the code.coding array.
        return [self.codeable_concept] if self.codeable_concept else []

    @staticmethod
    def for_practitioner_organization_study_patient(
        jhe_user_id,
        organization_id=None,
        study_id=None,
        patient_id=None,
        observation_id=None,
    ):
        practitioner = get_object_or_404(Practitioner, jhe_user_id=jhe_user_id)

        # Return the observations a practitioner is allowed to see, newest first. An
        # observation is visible only when its patient shares an organization with the
        # practitioner identified by jhe_user_id: the traversal walks Observation -> Patient
        # -> PatientOrganization -> Organization -> PractitionerOrganization -> Practitioner.
        # Keeping the patient's and the practitioner's organization lookups in one filter()
        # call matches them against the SAME organization (Django reuses the join for lookups
        # sharing the "subject_patient__organizations" prefix); an optional organization_id
        # narrows that shared organization. The result is then optionally narrowed: by study
        # (the patient must be enrolled in the study AND the observation's code must be one of
        # that study's requested scopes, both matched against the SAME study), to a single
        # patient, or to a single observation. The annotate() calls flatten columns from the
        # joined CodeableConcept and Patient rows onto each observation as plain attributes for
        # the serializer to read. distinct() collapses the duplicate observation rows produced
        # by spanning these many-to-many relationships.
        organization_filters = {"subject_patient__organizations__practitioners": practitioner}
        if organization_id:
            organization_filters["subject_patient__organizations__id"] = organization_id
        qs = Observation.objects.filter(**organization_filters)

        if study_id:
            qs = qs.filter(
                subject_patient__studypatient__study_id=study_id,
                codeable_concept__studyscoperequest__study_id=study_id,
            )
        if patient_id:
            qs = qs.filter(subject_patient_id=patient_id)
        if observation_id:
            qs = qs.filter(id=observation_id)

        return (
            qs.annotate(
                coding_system=F("codeable_concept__coding_system"),
                coding_code=F("codeable_concept__coding_code"),
                coding_text=F("codeable_concept__text"),
                patient_name_family=F("subject_patient__name_family"),
                patient_name_given=F("subject_patient__name_given"),
            )
            .distinct()
            .order_by("-last_updated")
        )

    @staticmethod
    def practitioner_authorized(practitioner_user_id, observation_id):
        return Observation.for_practitioner_organization_study_patient(
            practitioner_user_id, None, None, None, observation_id
        ).exists()

    @staticmethod
    def fhir_search(
        jhe_user_id,
        resource_id=None,
        organization_id=None,
        study_id=None,
        patient_id=None,
        **params,
    ):
        # Return the Observations visible to the user as a queryset of Observation instances
        # (the serializer renders them into FHIR JSON). A patient user sees only their own
        # observations and the organization/study/patient filters are ignored; a practitioner
        # sees observations whose patient shares one of their organizations -- narrowed by the
        # explicit organization/study/patient filters (each authorized up front, 403 on
        # mismatch) and, via **params, by patient identifier and coding system|code. When a
        # study is given the patient must be enrolled in it AND the observation's code must be
        # one of that study's requested scopes. resource_id selects a single observation.
        # Related rows are selected/prefetched to avoid N+1; distinct() collapses the duplicate
        # rows produced by spanning these many-to-many relationships.
        coding_system = params.get("coding_system")
        coding_code = params.get("coding_code")
        patient_identifier_value = params.get("patient_identifier_value")

        user = resolve_fhir_user(jhe_user_id)
        if user.is_patient():
            qs = Observation.objects.filter(subject_patient__jhe_user_id=jhe_user_id)
        else:
            authorize_practitioner_scope(jhe_user_id, organization_id, study_id, patient_id)
            # Anchor on the practitioner's organization membership; an optional organization_id
            # narrows that SAME shared organization (kept in one filter() so Django reuses the
            # join). A study additionally requires the patient be enrolled AND the code be one
            # of that study's requested scopes (matched against the same study).
            organization_filters = {"subject_patient__organizations__practitioners__jhe_user_id": jhe_user_id}
            if organization_id:
                organization_filters["subject_patient__organizations__id"] = organization_id
            qs = Observation.objects.filter(**organization_filters)
            if study_id:
                qs = qs.filter(
                    subject_patient__studypatient__study_id=study_id,
                    codeable_concept__studyscoperequest__study_id=study_id,
                )
            if patient_id:
                qs = qs.filter(subject_patient_id=patient_id)

        if resource_id:
            qs = qs.filter(id=resource_id)
        if patient_identifier_value:
            qs = qs.filter(subject_patient__identifiers__value=patient_identifier_value)
        if coding_system:
            qs = qs.filter(codeable_concept__coding_system=coding_system)
        if coding_code:
            qs = qs.filter(codeable_concept__coding_code=coding_code)

        return (
            qs.select_related("subject_patient", "codeable_concept")
            .prefetch_related("identifiers")
            .distinct()
            .order_by("-last_updated")
        )

    # Get the binary data eg https://www.rapidtables.com/convert/number/string-to-binary.html (delimiter=none)
    # base64 it eg https://cryptii.com/pipes/binary-to-base64
    @staticmethod
    def fhir_create(data, user):
        # Persist an OMH Observation (code system https://w3id.org/openmhealth) onto the Django
        # Observation model: the value attachment is decoded into the omh_data column, the code
        # must be a known, consented scope, and a Device is required. The FHIR view routes
        # non-OMH (or code-less) Observations to FhirAuxResource instead, so this method always
        # handles the OMH path.
        import humps

        camelized = humps.camelize(data)
        try:
            fhir_observation = FHIRObservation.parse_obj(camelized)
        except Exception as e:
            raise (BadRequest(e))  # TBD: move to view

        # Subject -- the structural link to the Patient.
        if (
            not fhir_observation.subject
            or not fhir_observation.subject.reference
            or not fhir_observation.subject.reference.startswith("Patient/")
        ):
            raise (
                BadRequest("Subject is required and must be a reference to a Patient ID and start with 'Patient/'")
            )  # TBD: move to view
        subject_patient_id = fhir_observation.subject.reference.split("/")[1]
        try:
            subject_patient = Patient.objects.get(pk=subject_patient_id)
        except Patient.DoesNotExist:
            raise (BadRequest(f"Patient id={subject_patient_id} can not be found."))  # TBD: move to view

        if user.is_practitioner():
            if not subject_patient.practitioner_authorized(user.pk, subject_patient.id):
                raise PermissionDenied("Current user doesn't have access to the Patient.")
            user_patient = subject_patient
        else:
            user_patient = user.get_patient()
        if user_patient is None:
            raise PermissionDenied("Current user is not a Patient.")
        if subject_patient.id != user_patient.id:
            raise PermissionDenied("The Subject Patient does not match the current user.")

        data_source = Observation._resolve_device(fhir_observation)

        # Reject duplicate identifiers up front so we don't create an orphan observation
        # before the ObservationIdentifier unique constraint trips.
        for identifier in fhir_observation.identifier or []:
            if ObservationIdentifier.objects.filter(system=identifier.system, value=identifier.value).exists():
                raise IntegrityError(f"Identifier already exists: system={identifier.system} value={identifier.value}")
        codeable_concept, omh_data = Observation._omh_payload(fhir_observation, user_patient)

        observation = Observation.objects.create(
            subject_patient=subject_patient,
            data_source=data_source,
            codeable_concept=codeable_concept,
            status=fhir_observation.status,
            omh_data=omh_data,
        )

        if fhir_observation.identifier:
            for identifier in fhir_observation.identifier:
                ObservationIdentifier.objects.create(
                    observation=observation,
                    system=identifier.system,
                    value=identifier.value,
                )

        return observation

    @staticmethod
    def _resolve_device(fhir_observation):
        reference = fhir_observation.device.reference if fhir_observation.device else None
        if not reference or not reference.startswith("Device/"):
            raise BadRequest("Device is required and must be a reference to a Data Source ID and start with 'Device/'")
        device_id = reference.split("/")[1]
        try:
            return DataSource.objects.get((Q(type="personal_device") | Q(type="device")), id=device_id)
        except DataSource.DoesNotExist:
            raise (BadRequest(f"Device Data Source id={device_id} can not be found."))

    @staticmethod
    def _omh_payload(fhir_observation, user_patient):
        """Resolve the (consented) CodeableConcept and decode the OMH value attachment."""
        if len(fhir_observation.code.coding) != 1:
            raise BadRequest("Exactly one Code must be provided.")  # TBD: move to view
        coding = fhir_observation.code.coding[0]

        codeable_concept = CodeableConcept.objects.filter(coding_system=coding.system, coding_code=coding.code).first()
        if codeable_concept is None:
            raise BadRequest(f"Code not found: system={coding.system} code={coding.code}")  # TBD: move to view

        if codeable_concept.id not in [scope.id for scope in user_patient.consolidated_consented_scopes()]:
            raise PermissionDenied(
                f"Observation data with coding_system={codeable_concept.coding_system}"
                f" coding_code={codeable_concept.coding_code} has not been consented for any studies by this Patient."
            )

        try:
            omh_data = json.loads(base64.b64decode(fhir_observation.valueAttachment.data).decode("ascii"))
        except Exception:
            raise BadRequest("valueAttachment.data must be Base 64 Encoded Binary JSON.")  # TBD: move to view

        return codeable_concept, omh_data

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # FHIR serialization support
        self.identifier = None
        self.resource_type = None
        self.meta = None
        self.value_attachment = None
        self.subject = None
        self.code = None

    def clean(self):
        # Django Observations are OMH; validate omh_data against the OMH schemas. Guard against
        # partially-populated instances (no omh_data/codeable_concept) defensively.
        if self.omh_data is None or self.codeable_concept is None:
            return
        try:
            omh_data = self.omh_data

            header_schema = code_to_schema("ieee:header:1.0")
            validate_with_registry(instance=omh_data.get("header"), schema=header_schema)

            body_schema = code_to_schema(self.codeable_concept.coding_code)
            validate_with_registry(instance=omh_data.get("body"), schema=body_schema)
        except Exception as error:
            raise error

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class ObservationIdentifier(models.Model):
    observation = models.ForeignKey(Observation, on_delete=models.CASCADE, related_name="identifiers")
    system = models.CharField(null=True, blank=True)
    value = models.CharField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["system", "value"],
                name="core_observationidentifier_unique_system_value",
            )
        ]
