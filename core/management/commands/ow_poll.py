"""
Open Wearables polling pipeline.

Cron-driven sidecar command that pulls observations from Open Wearables and
ingests them as JHE Observations.

Modes (selected via the ``ow.ingest_mode`` JheSetting):

* ``normalized`` (default): query OW's ``/api/v1/users/<id>/timeseries``
  endpoint, convert each sample with ``omh_shim.convert(source="ow_normalized")``
  and persist as Observations. Dedup is enforced by a paired
  ``ObservationIdentifier`` row with ``system="ow:normalized"`` and
  ``value=<omh-uuid>``.

* ``raw``: walks the OW S3/MinIO bucket and converts via
  ``omh_shim.convert(source="oura_raw")``. Dedup uses the same pattern with
  ``system="ow:raw"``.

The command no-ops in two situations:

1. ``module.ow`` JheSetting is false (operator-controlled master switch).
2. ``ow.sync_in_progress`` holds a recent ISO timestamp (a previous tick is
   still running). Locks older than ``LOCK_STALE_AFTER`` are treated as
   abandoned (e.g. crashed worker) and force-reclaimed.

OW connection config (``OW_API_URL``, ``OW_API_KEY``) is read from
``django.conf.settings`` and ultimately from environment variables, matching
``core/views/ow.py``.
"""

import logging
from datetime import datetime, timedelta

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction
from django.utils import timezone
from omh_shim import convert

from core.jhe_settings.service import get_setting
from core.models import (
    CodeableConcept,
    DataSource,
    JheSetting,
    JheUser,
    Observation,
    ObservationIdentifier,
)
from core.services.ow_ingest.raw_payload_reader import list_new_objects, read_object

logger = logging.getLogger(__name__)

POLL_OVERLAP = timedelta(minutes=5)
POLL_WINDOW = timedelta(days=1)
HEART_RATE_CODE = "omh:heart-rate:2.0"
NORMALIZED_SYSTEM = "ow:normalized"
RAW_SYSTEM = "ow:raw"
RAW_TRACE_ID_HEART_RATE = "/v2/usercollection/heartrate"
_SYNC_LOCK_KEY = "ow.sync_in_progress"
# A lock older than this is considered abandoned (worker crashed mid-poll)
# and is force-reclaimed by the next tick. Sized at ~2x the default cron
# interval (15 min) so a healthy long-running poll is never preempted.
LOCK_STALE_AFTER = timedelta(minutes=30)


def _write_sync_lock(value: str) -> None:
    """Write the ow.sync_in_progress JheSetting and bust the cache."""
    with transaction.atomic():
        setting, _ = JheSetting.objects.select_for_update().update_or_create(
            key=_SYNC_LOCK_KEY,
            setting_id=None,
            defaults={"value_type": "string"},
        )
        setting.set_value("string", value)
        setting.save()
    cache.delete(f"jhe_setting:{_SYNC_LOCK_KEY}")


class Command(BaseCommand):
    help = "Poll Open Wearables for new observations."

    def add_arguments(self, parser):
        parser.add_argument(
            "--patient-id",
            type=int,
            default=None,
            help="Poll only the specified patient (by Patient.id). For debugging/backfill.",
        )

    def handle(self, *args, **options):
        if not bool(get_setting("module.ow", False)):
            self.stdout.write("ow_poll skipped: module.ow=false")
            return

        if not self._acquire_lock():
            return

        try:
            self._run_poll(options)
        finally:
            self._release_lock()

    def _acquire_lock(self) -> bool:
        """Acquire ow.sync_in_progress (ISO timestamp). Return False if held.

        Atomic check-and-set under ``select_for_update`` so two concurrent
        cron ticks can't both win. A lock whose timestamp is older than
        ``LOCK_STALE_AFTER`` is treated as abandoned and reclaimed with a
        warning so a crashed previous tick auto-heals.
        """
        now = timezone.now()
        with transaction.atomic():
            setting, _ = JheSetting.objects.select_for_update().get_or_create(
                key=_SYNC_LOCK_KEY,
                setting_id=None,
                defaults={"value_type": "string", "value_string": ""},
            )
            current = setting.get_value() or ""
            if current:
                acquired_at = self._parse_lock_timestamp(current)
                if acquired_at is not None and (now - acquired_at) < LOCK_STALE_AFTER:
                    self.stdout.write(
                        f"ow_poll skipped: ow.sync_in_progress since {current}"
                    )
                    return False
                logger.warning(
                    "ow_poll: reclaiming stale ow.sync_in_progress lock (acquired_at=%s)",
                    current,
                )
            setting.set_value("string", now.isoformat())
            setting.save()
        cache.delete(f"jhe_setting:{_SYNC_LOCK_KEY}")
        return True

    def _release_lock(self) -> None:
        _write_sync_lock("")

    @staticmethod
    def _parse_lock_timestamp(value: str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _run_poll(self, options):
        mode = str(get_setting("ow.ingest_mode", "normalized") or "normalized").lower()
        if mode not in ("normalized", "raw"):
            self.stderr.write(f"ow_poll aborted: unknown ow.ingest_mode '{mode}'")
            return

        ow_api_url = (settings.OW_API_URL or "").rstrip("/")
        ow_api_key = settings.OW_API_KEY
        if mode == "normalized" and (not ow_api_url or not ow_api_key):
            self.stderr.write("ow_poll aborted: OW_API_URL / OW_API_KEY not configured")
            return

        try:
            hr_code = CodeableConcept.objects.get(coding_code=HEART_RATE_CODE)
        except CodeableConcept.DoesNotExist:
            self.stderr.write(f"CodeableConcept '{HEART_RATE_CODE}' not found. Run seed first.")
            return

        oura_ds, _ = DataSource.objects.get_or_create(
            name="Oura", defaults={"type": "personal_device"}
        )

        # Only users linked to an OW account: identifier startswith "ow:".
        users = JheUser.objects.filter(identifier__startswith="ow:")
        patient_id = options.get("patient_id")
        if patient_id is not None:
            users = users.filter(patient_profile__id=patient_id)

        total_created = 0
        for user in users:
            patient = getattr(user, "patient_profile", None)
            if patient is None:
                continue
            consented_codes = {s.coding_code for s in patient.consolidated_consented_scopes()}
            if HEART_RATE_CODE not in consented_codes:
                continue

            try:
                if mode == "normalized":
                    created = self._poll_user_normalized(
                        user, patient, oura_ds, hr_code, ow_api_url, ow_api_key
                    )
                else:
                    created = self._poll_user_raw(user, patient, oura_ds, hr_code)
                total_created += created
            except Exception:
                logger.exception("ow_poll failed for jhe_user_id=%s", user.id)

        self.stdout.write(
            self.style.SUCCESS(f"OW poll complete (mode={mode}). Created {total_created} observations.")
        )

    def _poll_user_normalized(self, user, patient, data_source, hr_code, ow_api_url, ow_api_key):
        ow_user_id = user.identifier.removeprefix("ow:")
        end_time = timezone.now()
        start_time = end_time - POLL_WINDOW

        # Resume from the most recent successfully ingested record so we
        # don't refetch the entire window every tick.
        last_obs = (
            Observation.objects.filter(
                subject_patient=patient,
                codeable_concept=hr_code,
                observationidentifier__system=NORMALIZED_SYSTEM,
            )
            .order_by("-last_updated")
            .first()
        )
        if last_obs:
            start_time = max(start_time, last_obs.last_updated - POLL_OVERLAP)

        try:
            resp = requests.get(
                f"{ow_api_url}/api/v1/users/{ow_user_id}/timeseries",
                params={
                    "types": "heart_rate",
                    "start_time": start_time.isoformat(),
                    "end_time": end_time.isoformat(),
                },
                headers={"X-Open-Wearables-API-Key": ow_api_key},
                timeout=30,
            )
        except requests.RequestException as e:
            logger.error("OW timeseries request failed for user=%s: %s", user.id, e)
            return 0

        if resp.status_code != 200:
            logger.error(
                "OW timeseries error for user=%s: %s %s",
                user.id,
                resp.status_code,
                resp.text[:300],
            )
            return 0

        data = resp.json()
        records = data.get("data", data) if isinstance(data, dict) else data
        if not isinstance(records, list):
            logger.warning("OW timeseries returned non-list payload for user=%s", user.id)
            return 0

        created = 0
        for record in records:
            try:
                omh_record = convert(source="ow_normalized", data_type="heart_rate", sample=record)
            except Exception:
                logger.warning("Skipping unconvertible record for user=%s", user.id, exc_info=True)
                continue

            uuid_value = omh_record.get("header", {}).get("uuid")
            if not uuid_value:
                continue

            # Dedup: paired ObservationIdentifier row with (system, value) unique.
            if ObservationIdentifier.objects.filter(
                system=NORMALIZED_SYSTEM, value=uuid_value
            ).exists():
                continue

            try:
                with transaction.atomic():
                    obs = Observation.objects.create(
                        subject_patient=patient,
                        codeable_concept=hr_code,
                        data_source=data_source,
                        value_attachment_data=omh_record,
                        status="final",
                    )
                    ObservationIdentifier.objects.create(
                        observation=obs,
                        system=NORMALIZED_SYSTEM,
                        value=uuid_value,
                    )
                created += 1
            except IntegrityError:
                # Lost a race with a concurrent tick; treat as already-ingested.
                continue
            except Exception:
                logger.warning(
                    "Failed to persist observation for patient=%s uuid=%s",
                    patient.id,
                    uuid_value,
                    exc_info=True,
                )

        logger.info(
            "Poll completed for jhe_user=%s patient=%s created=%d", user.id, patient.id, created
        )
        return created

    def _poll_user_raw(self, user, patient, data_source, hr_code):
        """Raw S3-backed ingest. Walks the OW MinIO bucket for new objects.

        Each individual record (not each S3 object) is deduped via
        ``ObservationIdentifier(system="ow:raw", value=<omh-header.uuid>)``,
        mirroring the normalized path so that idempotency is guaranteed even
        if the same payload is re-uploaded under a different S3 key.
        """
        ow_user_id = user.identifier.removeprefix("ow:")
        end_time = timezone.now()
        start_time = end_time - POLL_WINDOW

        last_obs = (
            Observation.objects.filter(
                subject_patient=patient,
                codeable_concept=hr_code,
                observationidentifier__system=RAW_SYSTEM,
            )
            .order_by("-last_updated")
            .first()
        )
        if last_obs:
            start_time = max(start_time, last_obs.last_updated - POLL_OVERLAP)

        try:
            objects = list_new_objects(ow_user_id, start_time)
        except Exception as e:
            logger.error("OW raw S3 list failed for user=%s: %s", user.id, e)
            return 0

        created = 0
        for obj in objects:
            # Only heart-rate keys for now; other endpoints are a follow-up.
            if RAW_TRACE_ID_HEART_RATE not in obj.key:
                continue

            try:
                payload = read_object(obj.key)
            except Exception:
                logger.warning("Skipping unreadable raw object %s", obj.key, exc_info=True)
                continue

            for record in payload.get("data", []):
                try:
                    omh_record = convert(source="oura_raw", data_type="heart_rate", sample=record)
                except Exception:
                    logger.warning(
                        "Skipping unconvertible raw record key=%s", obj.key, exc_info=True
                    )
                    continue

                uuid_value = omh_record.get("header", {}).get("uuid")
                if not uuid_value:
                    continue

                if ObservationIdentifier.objects.filter(
                    system=RAW_SYSTEM, value=uuid_value
                ).exists():
                    continue

                try:
                    with transaction.atomic():
                        obs = Observation.objects.create(
                            subject_patient=patient,
                            codeable_concept=hr_code,
                            data_source=data_source,
                            value_attachment_data=omh_record,
                            status="final",
                        )
                        ObservationIdentifier.objects.create(
                            observation=obs,
                            system=RAW_SYSTEM,
                            value=uuid_value,
                        )
                    created += 1
                except IntegrityError:
                    continue
                except Exception:
                    logger.warning(
                        "Failed to persist raw observation patient=%s uuid=%s",
                        patient.id,
                        uuid_value,
                        exc_info=True,
                    )

        logger.info(
            "Raw poll completed for jhe_user=%s patient=%s created=%d",
            user.id,
            patient.id,
            created,
        )
        return created
