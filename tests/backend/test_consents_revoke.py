"""Revoking a patient's last consented scope in a study disconnects their Oura connection in Open Wearables."""

from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

from core.models import JheUser

from .utils import Code, add_patient_to_study, create_study


@pytest.fixture
def ow_patient(organization):
    """A patient whose JheUser carries the ``ow:<id>`` identifier the disconnect is keyed on."""
    user = JheUser.objects.create_user(
        email="ow-consent@example.org",
        password="testpass123",
        identifier="ow:abc",
        user_type="patient",
    )
    user.patient.organizations.add(organization)
    return user.patient


def _ow_setting(key, default=None):
    return {"ow.api_url": "http://ow.test", "ow.api_key": "key"}.get(key, default)


def _revoke_payload(study, code):
    return {
        "study_scope_consents": [
            {
                "study_id": study.id,
                "scope_consents": [
                    {
                        "coding_system": Code.OpenMHealth.value,
                        "coding_code": code.value,
                        "consented": False,
                    }
                ],
            }
        ]
    }


def _patch_consents(patient, payload):
    client = APIClient()
    client.force_authenticate(patient.jhe_user)
    response = client.patch(f"/api/v1/patients/{patient.id}/consents", data=payload, format="json")
    assert response.status_code == 200, response.content
    return response


@patch("core.services.ow_ingest.requests.delete")
@patch("core.services.ow_ingest.get_setting", side_effect=_ow_setting)
def test_revoking_the_last_consented_scope_disconnects_oura(_get_setting, mock_delete, organization, ow_patient):
    mock_delete.return_value = MagicMock(status_code=204, text="")
    study = create_study(organization=organization, codes=[Code.HeartRate])
    add_patient_to_study(ow_patient, study)

    _patch_consents(ow_patient, _revoke_payload(study, Code.HeartRate))

    mock_delete.assert_called_once()
    assert mock_delete.call_args[0][0].endswith("/api/v1/users/abc/connections/oura")


@patch("core.services.ow_ingest.requests.delete")
@patch("core.services.ow_ingest.get_setting", side_effect=_ow_setting)
def test_no_disconnect_while_another_scope_stays_consented(_get_setting, mock_delete, organization, ow_patient):
    study = create_study(organization=organization, codes=[Code.HeartRate, Code.BloodPressure])
    add_patient_to_study(ow_patient, study)

    _patch_consents(ow_patient, _revoke_payload(study, Code.HeartRate))

    mock_delete.assert_not_called()
