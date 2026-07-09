"""
Tests for the `ow_poll` management command (normalized + raw modes).
"""

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.models import (
    CodeableConcept,
    JheSetting,
    Observation,
    ObservationIdentifier,
)
from core.services.jhe_settings import get_setting
from core.utils import generate_observation_value_attachment_data

HR_CODE = "omh:heart-rate:2.0"
NORMALIZED_SYSTEM = "ow:normalized"
OW_API_URL = "https://ow.example.test"
OW_API_KEY = "test-key"


def _set_jhe_setting(key, value, value_type="bool"):
    setting, _ = JheSetting.objects.update_or_create(key=key, defaults={"value_type": value_type})
    setting.set_value(value_type, value)
    setting.save()
    from django.core.cache import cache

    cache.delete(f"jhe_setting:{key}")


def _clear_sync_lock():
    """Reset ow.sync_in_progress to the unlocked value (empty string)."""
    _set_jhe_setting("ow.sync_in_progress", "", value_type="string")


def _hold_sync_lock(acquired_at=None):
    """Set ow.sync_in_progress to a recent ISO timestamp (lock held)."""
    acquired_at = acquired_at or timezone.now()
    _set_jhe_setting("ow.sync_in_progress", acquired_at.isoformat(), value_type="string")


@pytest.fixture(autouse=True)
def _ow_settings(db):
    _set_jhe_setting("ow.api_url", OW_API_URL, value_type="string")
    _set_jhe_setting("ow.api_key", OW_API_KEY, value_type="string")


@pytest.fixture(autouse=True)
def _clear_jhe_setting_cache():
    """JheSetting values are cached process-wide; clear between tests so a
    leftover key (e.g. ow.ingest_mode='bogus' from test_unknown_mode_aborts)
    cannot leak into the next test whose DB row was already rolled back."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def hr_concept(db):
    cc, _ = CodeableConcept.objects.update_or_create(
        coding_system="https://w3id.org/openmhealth",
        coding_code=HR_CODE,
        text=HR_CODE,
    )
    return cc


@pytest.fixture
def patient_with_consent(hr_study, patient, hr_concept):
    """Reuses upstream `hr_study` fixture which adds patient with HR consent."""
    return patient


@pytest.fixture
def ow_user(patient_with_consent):
    user = patient_with_consent.jhe_user
    user.identifier = "ow:user-123"
    user.save(update_fields=["identifier"])
    return user


def _fake_omh_record(uuid_value="rec-1"):
    record = generate_observation_value_attachment_data(HR_CODE)
    record["header"]["uuid"] = uuid_value
    return record


def test_skipped_when_module_disabled(db, ow_user, hr_concept):
    _set_jhe_setting("module.ow", False)
    out = StringIO()
    call_command("ow_poll", stdout=out)
    assert "module.ow=false" in out.getvalue()
    assert Observation.objects.count() == 0


def test_skipped_when_lock_held(db, ow_user, hr_concept):
    _set_jhe_setting("module.ow", True)
    _hold_sync_lock()
    out = StringIO()
    call_command("ow_poll", stdout=out)
    assert "sync_in_progress" in out.getvalue()
    # Lock should remain set (we didn't clear it - we never acquired it)
    assert get_setting("ow.sync_in_progress")


def test_creates_observation_for_consented_patient(db, ow_user, patient_with_consent, hr_concept):
    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()

    fake_record = {"timestamp": "2024-01-01T00:00:00Z", "value": 72}

    with (
        patch("core.management.commands.ow_poll.requests.get") as mock_get,
        patch("core.management.commands.ow_poll.convert") as mock_convert,
    ):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"data": [fake_record]}
        mock_convert.return_value = _fake_omh_record(uuid_value="abc-123")

        call_command("ow_poll", stdout=StringIO())

    obs = Observation.objects.filter(subject_patient=patient_with_consent)
    assert obs.count() == 1
    assert obs.first().codeable_concept == hr_concept
    assert ObservationIdentifier.objects.filter(
        observation=obs.first(), system=NORMALIZED_SYSTEM, value="abc-123"
    ).exists()
    # Lock cleared after run
    assert not get_setting("ow.sync_in_progress")


def test_dedupes_via_observation_identifier(db, ow_user, patient_with_consent, hr_concept):
    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()

    with (
        patch("core.management.commands.ow_poll.requests.get") as mock_get,
        patch("core.management.commands.ow_poll.convert") as mock_convert,
    ):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"data": [{"x": 1}]}
        mock_convert.return_value = _fake_omh_record(uuid_value="same-uuid")

        call_command("ow_poll", stdout=StringIO())
        call_command("ow_poll", stdout=StringIO())

    assert ObservationIdentifier.objects.filter(system=NORMALIZED_SYSTEM, value="same-uuid").count() == 1
    assert Observation.objects.count() == 1


def test_skips_user_without_ow_identifier(db, patient_with_consent, hr_concept):
    """JheUser whose identifier doesn't start with 'ow:' must not trigger any OW API call."""
    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()
    patient_with_consent.jhe_user.identifier = ""
    patient_with_consent.jhe_user.save(update_fields=["identifier"])

    with patch("core.management.commands.ow_poll.requests.get") as mock_get:
        call_command("ow_poll", stdout=StringIO())

    mock_get.assert_not_called()
    assert Observation.objects.count() == 0


def test_skips_patient_without_hr_consent(db, organization, hr_concept):
    """A JheUser with an OW identifier but no HR scope consent must be skipped."""
    from core.models import JheUser

    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()

    user = JheUser.objects.create_user(
        email="no-consent@example.org",
        password="x",
        identifier="ow:user-no-consent",
        user_type="patient",
    )
    user.patient.organizations.add(organization)

    with patch("core.management.commands.ow_poll.requests.get") as mock_get:
        call_command("ow_poll", stdout=StringIO())

    mock_get.assert_not_called()
    assert Observation.objects.count() == 0


def test_lock_released_on_exception(db, ow_user, hr_concept):
    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()

    with patch("core.management.commands.ow_poll.requests.get", side_effect=RuntimeError("boom")):
        # Per-user errors are swallowed by the loop (logger.exception). Lock
        # should still be released by the outer try/finally.
        call_command("ow_poll", stdout=StringIO())

    assert not get_setting("ow.sync_in_progress")


def test_aborts_when_settings_missing(db, ow_user, hr_concept):
    _set_jhe_setting("ow.api_url", "", value_type="string")
    _set_jhe_setting("ow.api_key", "", value_type="string")
    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()

    err = StringIO()
    call_command("ow_poll", stdout=StringIO(), stderr=err)

    assert "ow.api_url" in err.getvalue()
    assert Observation.objects.count() == 0
    assert not get_setting("ow.sync_in_progress")


def test_raw_mode_creates_observation_and_dedupes(db, ow_user, patient_with_consent, hr_concept):
    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()
    _set_jhe_setting("ow.ingest_mode", "raw", value_type="string")

    from collections import namedtuple

    S3Obj = namedtuple("S3Obj", ["key", "last_modified"])
    fake_obj = S3Obj(
        key="raw-payloads/oura/api_response/v2/usercollection/heartrate/user-123/1.json",
        last_modified=None,
    )

    with (
        patch("core.management.commands.ow_poll.list_new_objects", return_value=[fake_obj]) as mock_list,
        patch(
            "core.management.commands.ow_poll.read_object",
            return_value={"data": [{"x": 1}]},
        ),
        patch(
            "core.management.commands.ow_poll.convert",
            return_value=_fake_omh_record(uuid_value="raw-uuid-1"),
        ),
    ):
        call_command("ow_poll", stdout=StringIO())
        # Second tick should be a no-op (dedup via ow:raw + uuid).
        call_command("ow_poll", stdout=StringIO())

    assert mock_list.called
    assert ObservationIdentifier.objects.filter(system="ow:raw", value="raw-uuid-1").count() == 1
    assert Observation.objects.count() == 1


def test_raw_mode_skips_non_heartrate_keys(db, ow_user, patient_with_consent, hr_concept):
    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()
    _set_jhe_setting("ow.ingest_mode", "raw", value_type="string")

    from collections import namedtuple

    S3Obj = namedtuple("S3Obj", ["key", "last_modified"])
    not_hr = S3Obj(
        key="raw-payloads/oura/api_response/v2/usercollection/sleep/user-123/1.json",
        last_modified=None,
    )

    with (
        patch("core.management.commands.ow_poll.list_new_objects", return_value=[not_hr]),
        patch("core.management.commands.ow_poll.read_object") as mock_read,
        patch("core.management.commands.ow_poll.convert") as mock_convert,
    ):
        call_command("ow_poll", stdout=StringIO())

    mock_read.assert_not_called()
    mock_convert.assert_not_called()
    assert Observation.objects.count() == 0


def test_unknown_mode_aborts(db, ow_user, hr_concept):
    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()
    _set_jhe_setting("ow.ingest_mode", "bogus", value_type="string")

    err = StringIO()
    call_command("ow_poll", stdout=StringIO(), stderr=err)

    assert "unknown ow.ingest_mode" in err.getvalue()
    assert Observation.objects.count() == 0


def test_stale_lock_is_force_released(db, ow_user, patient_with_consent, hr_concept):
    """A lock older than LOCK_STALE_AFTER is reclaimed by the next tick."""
    from datetime import timedelta

    from core.management.commands.ow_poll import LOCK_STALE_AFTER

    _set_jhe_setting("module.ow", True)
    _set_jhe_setting("ow.ingest_mode", "normalized", value_type="string")
    # Held longer than the stale window -> treat as abandoned (crashed worker).
    stale_at = timezone.now() - (LOCK_STALE_AFTER + timedelta(minutes=1))
    _hold_sync_lock(acquired_at=stale_at)

    with (
        patch("core.management.commands.ow_poll.requests.get") as mock_get,
        patch("core.management.commands.ow_poll.convert") as mock_convert,
    ):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"data": [{"x": 1}]}
        mock_convert.return_value = _fake_omh_record(uuid_value="stale-recover")

        call_command("ow_poll", stdout=StringIO())

    # Force-reclaim: poll ran, observation persisted, lock cleared on exit.
    assert Observation.objects.count() == 1
    assert not get_setting("ow.sync_in_progress")
