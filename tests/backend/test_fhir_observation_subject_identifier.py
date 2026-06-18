"""Issue #602: the FHIR Observation output carries the patient's jheUserId as a
subject.identifier, distinct from subject.reference (which is the Patient record id)."""

from fhir.resources.observation import Observation as FHIRObservation

from core.models import JheUser, Observation, Patient
from core.serializers import FHIRObservationSerializer

from .utils import Code, add_observations

JHE_USER_ID_SYSTEM = "https://jupyterhealth.org/fhir/identifier/jhe-user-id"


def _render_one(patient):
    add_observations(patient=patient, code=Code.HeartRate, n=1)
    obs = Observation.objects.filter(subject_patient=patient).first()
    return FHIRObservationSerializer(obs).data


def test_fhir_observation_exposes_jhe_user_id_as_subject_identifier(db):
    user = JheUser.objects.create_user(email="obs-602@example.org", password="x", user_type="patient")
    patient = user.patient

    data = _render_one(patient)

    assert data["subject"]["reference"] == f"Patient/{patient.id}"
    identifier = data["subject"]["identifier"]
    assert identifier["system"] == JHE_USER_ID_SYSTEM
    # FHIR identifier.value is a string; the value is the jheUserId (account id), not the record id.
    assert identifier["value"] == str(patient.jhe_user_id)
    assert isinstance(identifier["value"], str)
    # The whole resource must remain valid FHIR R5.
    FHIRObservation.parse_obj(data)


def test_fhir_observation_omits_identifier_when_patient_has_no_user(db):
    patient = Patient.objects.create(name_family="No", name_given="User")

    data = _render_one(patient)

    assert data["subject"]["reference"] == f"Patient/{patient.id}"
    assert "identifier" not in data["subject"]
