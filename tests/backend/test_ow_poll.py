"""
Tests for the `ow_poll` management command (normalized + raw modes).
"""

from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

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
        observation=obs.first(), system=NORMALIZED_SYSTEM, value="user-123:heart_rate:2024-01-01T00:00:00Z"
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
        mock_get.return_value.json.return_value = {"data": [{"timestamp": "2024-01-01T00:00:00Z", "x": 1}]}
        mock_convert.return_value = _fake_omh_record(uuid_value="same-uuid")

        call_command("ow_poll", stdout=StringIO())
        call_command("ow_poll", stdout=StringIO())

    assert (
        ObservationIdentifier.objects.filter(
            system=NORMALIZED_SYSTEM, value="user-123:heart_rate:2024-01-01T00:00:00Z"
        ).count()
        == 1
    )
    assert Observation.objects.count() == 1


def test_dedupes_when_convert_returns_a_fresh_uuid_each_time(db, ow_user, patient_with_consent, hr_concept):
    """Real omh-shim stamps a random uuid per convert, so the key cannot come from the header."""
    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()

    sample = {"timestamp": "2026-09-20T10:00:00+00:00", "type": "heart_rate", "value": 61}
    counter = {"n": 0}

    def fresh_uuid(*args, **kwargs):
        counter["n"] += 1
        return _fake_omh_record(uuid_value=f"random-{counter['n']}")

    with (
        patch("core.management.commands.ow_poll.requests.get") as mock_get,
        patch("core.management.commands.ow_poll.convert", side_effect=fresh_uuid),
    ):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"data": [sample]}

        call_command("ow_poll", stdout=StringIO())
        call_command("ow_poll", stdout=StringIO())

    assert Observation.objects.count() == 1


def test_two_patients_with_identical_samples_both_ingest(db, hr_study, hr_concept, organization):
    """The dedupe key is scoped per patient; ObservationIdentifier is unique globally."""
    from core.models import JheUser

    from .utils import add_patient_to_study

    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()

    patients = []
    for suffix in ("aaa", "bbb"):
        user = JheUser.objects.create_user(
            email=f"collide-{suffix}@example.test",
            password="x",
            identifier=f"ow:collide-{suffix}",
            user_type="patient",
        )
        patient = user.patient
        add_patient_to_study(patient=patient, study=hr_study)
        patients.append(patient)

    sample = {"timestamp": "2026-09-20T10:00:00+00:00", "type": "heart_rate", "value": 61}

    with (
        patch("core.management.commands.ow_poll.requests.get") as mock_get,
        patch("core.management.commands.ow_poll.convert") as mock_convert,
    ):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"data": [sample]}
        mock_convert.return_value = _fake_omh_record()

        call_command("ow_poll", stdout=StringIO())

    assert Observation.objects.filter(subject_patient=patients[0]).count() == 1
    assert Observation.objects.filter(subject_patient=patients[1]).count() == 1


def test_revised_record_updates_the_existing_observation(db, ow_user, patient_with_consent, hr_concept):
    """Oura revises a night after first sync, so the same key must overwrite, not skip."""
    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()

    sample = {"timestamp": "2026-09-20T10:00:00+00:00", "type": "heart_rate", "value": 61}

    first = _fake_omh_record(uuid_value="ignored-1")
    first["body"]["heart_rate"]["value"] = 61
    second = _fake_omh_record(uuid_value="ignored-2")
    second["body"]["heart_rate"]["value"] = 99

    with (
        patch("core.management.commands.ow_poll.requests.get") as mock_get,
        patch("core.management.commands.ow_poll.convert") as mock_convert,
    ):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"data": [sample]}

        mock_convert.return_value = first
        call_command("ow_poll", stdout=StringIO())
        mock_convert.return_value = second
        call_command("ow_poll", stdout=StringIO())

    assert Observation.objects.count() == 1
    assert Observation.objects.get().omh_data["body"]["heart_rate"]["value"] == 99


def test_persist_failure_does_not_abort_the_run(db, ow_user, patient_with_consent, hr_concept):
    """A row that fails to persist is skipped; later rows in the same page still land."""
    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()

    with (
        patch("core.management.commands.ow_poll.requests.get") as mock_get,
        patch("core.management.commands.ow_poll.convert") as mock_convert,
    ):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "data": [
                {"timestamp": "2024-01-01T00:00:00Z", "x": 1},
                {"timestamp": "2024-01-01T00:01:00Z", "x": 2},
            ]
        }
        mock_convert.side_effect = [
            _fake_omh_record(uuid_value="bad-1"),
            _fake_omh_record(uuid_value="good-1"),
        ]

        original_create = Observation.objects.create

        def fail_first(*args, **kwargs):
            if kwargs.get("omh_data", {}).get("header", {}).get("uuid") == "bad-1":
                raise ValueError("boom")
            return original_create(*args, **kwargs)

        with patch.object(Observation.objects, "create", side_effect=fail_first):
            call_command("ow_poll", stdout=StringIO())

    assert Observation.objects.count() == 1
    assert ObservationIdentifier.objects.filter(
        system=NORMALIZED_SYSTEM, value="user-123:heart_rate:2024-01-01T00:01:00Z"
    ).exists()


def test_invalid_revision_does_not_drop_later_records(db, ow_user, patient_with_consent, hr_concept):
    """A revised body the schema rejects is skipped like a failed create; later records still land."""
    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()

    revised = {"timestamp": "2026-09-20T10:00:00+00:00", "type": "heart_rate", "value": 61}
    new = {"timestamp": "2026-09-20T10:05:00+00:00", "type": "heart_rate", "value": 62}
    invalid = _fake_omh_record()
    del invalid["body"]["heart_rate"]

    with (
        patch("core.management.commands.ow_poll.requests.get") as mock_get,
        patch("core.management.commands.ow_poll.convert") as mock_convert,
    ):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"data": [revised]}
        mock_convert.return_value = _fake_omh_record()
        call_command("ow_poll", stdout=StringIO())

        mock_get.return_value.json.return_value = {"data": [revised, new]}
        mock_convert.side_effect = [invalid, _fake_omh_record()]
        call_command("ow_poll", stdout=StringIO())

    assert Observation.objects.count() == 2


def test_revision_never_overwrites_another_patients_row(db, ow_user, hr_concept):
    """JheUser.identifier is not unique, so a matching key on another patient's row is left alone."""
    from core.models import JheUser

    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()

    other = JheUser.objects.create_user(email="other@example.test", password="x", user_type="patient").patient
    stored = _fake_omh_record()
    stored["body"]["heart_rate"]["value"] = 61
    theirs = Observation.objects.create(
        subject_patient=other, codeable_concept=hr_concept, omh_data=stored, status="final"
    )
    ObservationIdentifier.objects.create(
        observation=theirs, system=NORMALIZED_SYSTEM, value="user-123:heart_rate:2026-09-20T10:00:00+00:00"
    )
    revised = _fake_omh_record()
    revised["body"]["heart_rate"]["value"] = 99

    with (
        patch("core.management.commands.ow_poll.requests.get") as mock_get,
        patch("core.management.commands.ow_poll.convert") as mock_convert,
    ):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "data": [{"timestamp": "2026-09-20T10:00:00+00:00", "type": "heart_rate", "value": 99}]
        }
        mock_convert.return_value = revised
        call_command("ow_poll", stdout=StringIO())

    theirs.refresh_from_db()
    assert theirs.omh_data["body"]["heart_rate"]["value"] == 61


def test_second_poll_resumes_from_the_last_observation(db, ow_user, patient_with_consent, hr_concept):
    """Once a row exists, the next poll asks OW from that row's time minus the overlap."""
    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()

    with (
        patch("core.management.commands.ow_poll.requests.get") as mock_get,
        patch("core.management.commands.ow_poll.convert") as mock_convert,
    ):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"data": [{"timestamp": "2024-01-01T00:00:00Z", "x": 1}]}
        mock_convert.return_value = _fake_omh_record(uuid_value="first-1")
        call_command("ow_poll", stdout=StringIO())

        obs = Observation.objects.get()
        mock_get.reset_mock()
        mock_convert.return_value = _fake_omh_record(uuid_value="second-1")
        call_command("ow_poll", stdout=StringIO())

    requested = _requested_start(mock_get)
    assert requested == obs.effective_date_time - timedelta(minutes=5)


def test_late_arriving_sample_is_not_skipped(db, ow_user, patient_with_consent, hr_concept):
    """A sample measured before the newest row was written must still be fetched."""
    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()

    recent = {"timestamp": "2026-09-20T10:00:00+00:00", "type": "heart_rate", "value": 61}

    with (
        patch("core.management.commands.ow_poll.requests.get") as mock_get,
        patch("core.management.commands.ow_poll.convert") as mock_convert,
    ):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"data": [recent]}
        record = _fake_omh_record()
        record["body"]["effective_time_frame"] = {"date_time": "2026-09-20T10:00:00+00:00"}
        mock_convert.return_value = record
        call_command("ow_poll", "--days", "30", stdout=StringIO())

        obs = Observation.objects.get()
        mock_get.reset_mock()
        call_command("ow_poll", "--days", "30", stdout=StringIO())

    requested = _requested_start(mock_get)
    assert requested < obs.last_updated
    assert requested == obs.effective_date_time - timedelta(minutes=5)


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
            return_value={"data": [{"timestamp": "2024-01-01T00:00:00Z", "x": 1}]},
        ),
        patch(
            "core.management.commands.ow_poll.convert",
            return_value=_fake_omh_record(uuid_value="raw-uuid-1"),
        ),
    ):
        call_command("ow_poll", stdout=StringIO())
        # Second tick should be a no-op (dedup via ow:raw + dedupe key).
        call_command("ow_poll", stdout=StringIO())

    assert mock_list.called
    assert (
        ObservationIdentifier.objects.filter(system="ow:raw", value="user-123:heart_rate:2024-01-01T00:00:00Z").count()
        == 1
    )
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
        mock_get.return_value.json.return_value = {"data": [{"timestamp": "2024-01-01T00:00:00Z", "x": 1}]}
        mock_convert.return_value = _fake_omh_record(uuid_value="stale-recover")

        call_command("ow_poll", stdout=StringIO())

    # Force-reclaim: poll ran, observation persisted, lock cleared on exit.
    assert Observation.objects.count() == 1
    assert not get_setting("ow.sync_in_progress")


def test_normalized_mode_follows_pagination(db, ow_user, patient_with_consent, hr_concept):
    """OW caps a page at 50 samples and returns a cursor; every page must be fetched."""
    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()

    page_1 = {
        "data": [{"timestamp": "2024-01-01T00:00:00Z", "value": 72}],
        "pagination": {"next_cursor": "cursor-2", "has_more": True},
    }
    page_2 = {
        "data": [{"timestamp": "2024-01-01T00:01:00Z", "value": 73}],
        "pagination": {"next_cursor": None, "has_more": False},
    }

    with (
        patch("core.management.commands.ow_poll.requests.get") as mock_get,
        patch("core.management.commands.ow_poll.convert") as mock_convert,
    ):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.side_effect = [page_1, page_2]
        mock_convert.side_effect = [_fake_omh_record("p1"), _fake_omh_record("p2")]

        call_command("ow_poll", stdout=StringIO())

    assert mock_get.call_count == 2, "second page was never requested"
    assert mock_get.call_args_list[1].kwargs["params"]["cursor"] == "cursor-2"


def test_ingests_blood_glucose_when_consented(db, ow_user, patient_with_consent):
    """Glucose reaches OW through the mobile SDK, so the poller must ask for it too."""
    from core.models import StudyPatient, StudyPatientScopeConsent, StudyScopeRequest

    bg = CodeableConcept.objects.create(
        coding_system="https://w3id.org/openmhealth",
        coding_code="omh:blood-glucose:4.0",
        text="Blood glucose",
    )
    sp = StudyPatient.objects.filter(patient=patient_with_consent).first()
    StudyScopeRequest.objects.create(study=sp.study, scope_code=bg)
    StudyPatientScopeConsent.objects.create(
        study_patient=sp, scope_code=bg, consented=True, consented_time=timezone.now()
    )

    sample = {
        "timestamp": "2026-08-31T08:00:00Z",
        "type": "blood_glucose",
        "value": 110,
        "unit": "mg_dl",
        "source": {"provider": "Stelo", "device": None},
    }

    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()

    def _by_type(*args, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        wanted = kwargs["params"]["types"]
        resp.json.return_value = {
            "data": [sample] if wanted == "blood_glucose" else [],
            "pagination": {"next_cursor": None, "has_more": False},
        }
        return resp

    with patch("core.management.commands.ow_poll.requests.get", side_effect=_by_type):
        call_command("ow_poll", stdout=StringIO())

    obs = Observation.objects.filter(subject_patient=patient_with_consent, codeable_concept=bg)
    assert obs.count() == 1
    assert obs.first().omh_data["body"]["blood_glucose"] == {"value": 110.0, "unit": "mg/dL"}
    assert not Observation.objects.filter(
        subject_patient=patient_with_consent, codeable_concept__coding_code=HR_CODE
    ).exists(), "glucose sample must not be converted as heart rate"


def test_raw_mode_skips_types_with_no_oura_converter(db, ow_user, patient_with_consent):
    """Oura exposes no glucose, so raw mode must not walk S3 for it."""
    from core.models import StudyPatient, StudyPatientScopeConsent, StudyScopeRequest

    bg = CodeableConcept.objects.create(
        coding_system="https://w3id.org/openmhealth",
        coding_code="omh:blood-glucose:4.0",
        text="Blood glucose",
    )
    sp = StudyPatient.objects.filter(patient=patient_with_consent).first()
    StudyScopeRequest.objects.create(study=sp.study, scope_code=bg)
    StudyPatientScopeConsent.objects.create(
        study_patient=sp, scope_code=bg, consented=True, consented_time=timezone.now()
    )

    _set_jhe_setting("module.ow", True)
    _set_jhe_setting("ow.ingest_mode", "raw", value_type="string")
    _clear_sync_lock()

    with patch("core.management.commands.ow_poll.list_new_objects") as mock_list:
        mock_list.return_value = []
        call_command("ow_poll", stdout=StringIO())

    called_types = [c for c in mock_list.call_args_list]
    assert len(called_types) == 1, "raw mode should only walk S3 for heart_rate"


def test_raw_mode_handles_string_source_field(db, ow_user, patient_with_consent, hr_concept):
    """Oura raw records carry source as a string ("awake"), not an object."""
    _set_jhe_setting("module.ow", True)
    _set_jhe_setting("ow.ingest_mode", "raw", value_type="string")
    _clear_sync_lock()

    obj = type("Obj", (), {"key": "/v2/usercollection/heartrate/x.json", "last_modified": timezone.now()})()

    with (
        patch("core.management.commands.ow_poll.list_new_objects") as mock_list,
        patch("core.management.commands.ow_poll.read_object") as mock_read,
        patch("core.management.commands.ow_poll.convert") as mock_convert,
    ):
        mock_list.return_value = [obj]
        mock_read.return_value = {"data": [{"bpm": 72, "source": "awake", "timestamp": "2026-01-01T00:00:00+00:00"}]}
        mock_convert.return_value = _fake_omh_record("raw-str-1")
        call_command("ow_poll", stdout=StringIO())

    assert Observation.objects.filter(subject_patient=patient_with_consent).count() == 1


def test_convert_is_called_with_a_timezone(db, ow_user, patient_with_consent, hr_concept):
    """Daily shapes raise ConversionError without tz, and the error is swallowed as a skip."""
    from datetime import UTC

    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()

    with (
        patch("core.management.commands.ow_poll.requests.get") as mock_get,
        patch("core.management.commands.ow_poll.convert") as mock_convert,
    ):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "data": [{"timestamp": "2026-09-20T10:00:00+00:00", "type": "heart_rate", "value": 61}]
        }
        mock_convert.return_value = _fake_omh_record()

        call_command("ow_poll", stdout=StringIO())

    assert mock_convert.call_args.kwargs["tz"] is UTC


def _requested_start(mock_get):
    from datetime import datetime

    return datetime.fromisoformat(mock_get.call_args[1]["params"]["start_time"])


def test_poll_window_days_setting_widens_the_first_fetch(db, ow_user, patient_with_consent, hr_concept):
    """A patient linking with existing history has nothing to resume from, so the
    window alone decides how far back OW is asked to look."""
    _set_jhe_setting("module.ow", True)
    _set_jhe_setting("ow.poll_window_days", 30, value_type="int")
    _clear_sync_lock()

    with patch("core.management.commands.ow_poll.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"data": [], "pagination": {"has_more": False}}
        call_command("ow_poll", stdout=StringIO())

    age = timezone.now() - _requested_start(mock_get)
    assert 29 <= age.days <= 30, f"expected a 30 day window, asked for {age.days}"


def test_days_flag_overrides_the_setting(db, ow_user, patient_with_consent, hr_concept):
    _set_jhe_setting("module.ow", True)
    _set_jhe_setting("ow.poll_window_days", 30, value_type="int")
    _clear_sync_lock()

    with patch("core.management.commands.ow_poll.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"data": [], "pagination": {"has_more": False}}
        call_command("ow_poll", "--days", "2", stdout=StringIO())

    age = timezone.now() - _requested_start(mock_get)
    assert 1 <= age.days <= 2, f"expected a 2 day window, asked for {age.days}"


def test_poll_window_defaults_to_one_day(db, ow_user, patient_with_consent, hr_concept):
    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()

    with patch("core.management.commands.ow_poll.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"data": [], "pagination": {"has_more": False}}
        call_command("ow_poll", stdout=StringIO())

    age = timezone.now() - _requested_start(mock_get)
    assert age.days == 1, f"expected the 1 day default, asked for {age.days}"


def test_interval_row_does_not_pin_the_watermark(db, ow_user, patient_with_consent, hr_concept):
    """An interval body leaves effective_date_time null, which sorts first on a Postgres DESC."""
    _set_jhe_setting("module.ow", True)
    _clear_sync_lock()

    interval = _fake_omh_record()
    interval["body"]["effective_time_frame"] = {
        "time_interval": {
            "start_date_time": "2020-01-01T00:00:00+00:00",
            "end_date_time": "2020-01-01T08:00:00+00:00",
        }
    }
    instant = _fake_omh_record()
    instant["body"]["effective_time_frame"] = {"date_time": "2026-09-20T10:00:00+00:00"}

    with (
        patch("core.management.commands.ow_poll.requests.get") as mock_get,
        patch("core.management.commands.ow_poll.convert") as mock_convert,
    ):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "data": [{"timestamp": "2020-01-01T00:00:00Z", "type": "heart_rate", "value": 61}]
        }
        mock_convert.return_value = interval
        call_command("ow_poll", "--days", "3000", stdout=StringIO())

        _clear_sync_lock()
        mock_get.return_value.json.return_value = {
            "data": [{"timestamp": "2026-09-20T10:00:00Z", "type": "heart_rate", "value": 62}]
        }
        mock_convert.return_value = instant
        call_command("ow_poll", "--days", "3000", stdout=StringIO())

        _clear_sync_lock()
        mock_get.reset_mock()
        call_command("ow_poll", "--days", "3000", stdout=StringIO())

    assert Observation.objects.count() == 2
    assert _requested_start(mock_get).year == 2026
