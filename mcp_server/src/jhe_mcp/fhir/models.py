from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from pydantic import BaseModel


class StudyMeta(BaseModel):
    study_id: str
    name: str
    description: str | None = None
    organization_id: str | None = None
    organization_name: str | None = None

    @classmethod
    def from_admin(cls, data: dict[str, Any]) -> StudyMeta:
        org = data.get("organization") or {}
        return cls(
            study_id=str(data["id"]),
            name=data["name"],
            description=data.get("description"),
            organization_id=str(org["id"]) if "id" in org else None,
            organization_name=org.get("name"),
        )


class Demographics(BaseModel):
    patient_id: str
    given_name: str | None = None
    family_name: str | None = None
    birth_date: str | None = None

    @classmethod
    def from_admin(cls, data: dict[str, Any]) -> Demographics:
        """Build from JHE Admin API `/api/v1/patients/{id}` response.

        JHE uses djangorestframework-camel-case middleware, so JSON field names
        are camelCase: `nameGiven`, `nameFamily`, `birthDate`.
        """
        return cls(
            patient_id=str(data["id"]),
            given_name=data.get("nameGiven"),
            family_name=data.get("nameFamily"),
            birth_date=data.get("birthDate"),
        )


class StudyPatient(BaseModel):
    patient_id: str
    given_name: str | None = None
    family_name: str | None = None
    email: str | None = None

    @classmethod
    def from_admin(cls, data: dict[str, Any]) -> StudyPatient:
        return cls(
            patient_id=str(data["id"]),
            given_name=data.get("nameGiven"),
            family_name=data.get("nameFamily"),
            email=data.get("telecomEmail"),
        )


class Observation(BaseModel):
    observation_id: str
    patient_id: str | None = None
    code: str | None = None
    code_system: str | None = None
    code_display: str | None = None
    effective_at: str | None = None
    omh_body: dict[str, Any] | None = None

    @classmethod
    def from_fhir_entry(cls, entry: dict[str, Any]) -> Observation:
        r = entry.get("resource") or entry
        coding = ((r.get("code") or {}).get("coding") or [{}])[0]
        subj = (r.get("subject") or {}).get("reference") or ""
        patient_id = subj.split("/", 1)[1] if subj.startswith("Patient/") else None
        omh_body = None
        attachment = r.get("valueAttachment") or {}
        encoded = attachment.get("data")
        if encoded:
            try:
                omh_payload = json.loads(base64.b64decode(encoded))
                omh_body = omh_payload.get("body")
            except (binascii.Error, ValueError):
                # Malformed attachment data: keep the Observation but omh_body stays None.
                pass
        effective_at = None
        if omh_body:
            tf = omh_body.get("effective_time_frame") or {}
            # OMH time-frame is either a point-in-time `date_time` or a
            # `time_interval` (used by sleep/activity/wearable records). Prefer
            # the interval's start, falling back to its end.
            effective_at = tf.get("date_time")
            if effective_at is None:
                interval = tf.get("time_interval") or {}
                effective_at = interval.get("start_date_time") or interval.get("end_date_time")
        return cls(
            observation_id=str(r["id"]),
            patient_id=patient_id,
            code=coding.get("code"),
            code_system=coding.get("system"),
            code_display=coding.get("display"),
            effective_at=effective_at,
            omh_body=omh_body,
        )


# OMH bodies that carry more than one measured scalar (e.g. blood pressure has
# both systolic and diastolic). A single (value, unit) can't represent these
# faithfully, so the slim view returns (None, None) and the caller fetches the
# full record (verbosity="full") rather than silently showing one of two numbers.
_MULTI_COMPONENT_KEYS: frozenset[str] = frozenset(
    {
        "systolic_blood_pressure",
        "diastolic_blood_pressure",
    }
)


def extract_value_unit(omh_body: dict[str, Any] | None) -> tuple[Any, str | None]:
    """Best-effort scalar extraction from an OMH body.

    OMH measure bodies typically nest the measure under a type key, e.g.
    ``{"blood_glucose": {"value": 92, "unit": "mg/dL"}}``. Returns the first
    ``(value, unit)`` found in a nested mapping, then falls back to a top-level
    ``value``/``unit``. Returns ``(None, None)`` when no scalar leaf exists
    (e.g. sleep episodes), so the caller can fetch the full record instead.

    Multi-component bodies (e.g. blood pressure, which carries both
    ``systolic_blood_pressure`` and ``diastolic_blood_pressure``) also return
    ``(None, None)``: surfacing only the first component would mislead, so the
    caller is steered to ``verbosity="full"`` to get all components.
    """
    if not omh_body:
        return None, None
    if any(key in omh_body for key in _MULTI_COMPONENT_KEYS):
        return None, None
    for key, v in omh_body.items():
        # effective_time_frame is metadata, never the measured scalar
        if key == "effective_time_frame":
            continue
        if isinstance(v, dict) and "value" in v:
            return v.get("value"), v.get("unit")
    if "value" in omh_body:
        return omh_body.get("value"), omh_body.get("unit")
    return None, None


class SlimObservation(BaseModel):
    observation_id: str
    patient_id: str | None = None
    type: str | None = None
    effective_at: str | None = None
    value: Any = None
    unit: str | None = None

    @classmethod
    def from_observation(cls, obs: Observation) -> SlimObservation:
        value, unit = extract_value_unit(obs.omh_body)
        return cls(
            observation_id=obs.observation_id,
            patient_id=obs.patient_id,
            type=obs.code_display or obs.code,
            effective_at=obs.effective_at,
            value=value,
            unit=unit,
        )
