"""
Open Wearables polling pipeline.

Cron-driven sidecar command that pulls observations from Open Wearables and
ingests them as JHE Observations.

Modes (selected via the ``ow.ingest_mode`` JheSetting):

* ``normalized`` (default): query the OW route each type is served from
  (see ``OW_TYPE_TO_ROUTE``), convert each sample with ``omh_shim.convert(source="ow_normalized")``
  and persist as Observations. Dedup is enforced by a paired
  ``ObservationIdentifier`` row with ``system="ow:normalized"`` and
  ``value=<the fetcher's dedupe key>``.

* ``raw``: walks the OW S3/MinIO bucket and converts via
  ``omh_shim.convert(source="oura_raw")``. Dedup uses the same pattern with
  ``system="ow:raw"``.

The command no-ops in two situations:

1. ``module.ow`` JheSetting is false (operator-controlled master switch).
2. ``ow.sync_in_progress`` holds a recent ISO timestamp (a previous tick is
   still running). Locks older than ``LOCK_STALE_AFTER`` are treated as
   abandoned (e.g. crashed worker) and force-reclaimed.

OW connection config (``ow.api_url``, ``ow.api_key``) is read from JheSettings
via ``get_setting()``, matching ``core/views/ow.py``.

``OW_TYPE_TO_CODE`` maps each polled type to the CodeableConcept its
Observations are filed under, and is intersected with the patient's consented
scopes so a poll can never widen consent. A type only belongs there once
omh-shim converts it to a schema id JHE can resolve: ``core/utils.py`` resolves
the ``omh`` and ``ieee`` namespaces only, which is why ``heart_rate_variability``
is absent (omh-shim 2.0 dropped the ``local:heart-rate-variability:1.0`` schema
it used to target).

``OW_TYPE_TO_ROUTE`` names the OW route each type is fetched from; a type absent
from it is served by ``timeseries``. The three sleep event types each fetch
``events/sleep`` independently, which keeps one data type mapping to one code.

``OW_TYPE_TO_SHIM_TYPE`` covers the keys that are not omh-shim data types:
``workout`` converts as ``physical_activity`` and files under the same code as the
daily activity summary, and ``resting_heart_rate`` converts as ``heart_rate``. Each
needs its own key for its own OW request and dedupe key.

``OW_TYPE_TO_SERIES`` exists because most keys of ``OW_TYPE_TO_CODE`` are omh-shim
data types, not OW series names, and the two namespaces are not the same. They
happen to agree for most types, which is what makes the disagreement easy to miss:
omh-shim calls them ``body_weight`` and ``body_height`` while OW's SeriesType enum
calls them ``weight`` and ``height``. The ``types`` query parameter and the
timeseries dedupe key use the OW name; the ``convert()`` call keeps the omh-shim name.

``RAW_SUPPORTED_TYPES`` stays heart rate only. Raw mode has never ingested
anything (#746), so it is not widened alongside the normalized types.

``ow.poll_window_days`` (default 1) sets how far back OW is asked for samples.
It only bounds a patient's first poll: once they have an Observation the resume
watermark is the later bound, so a wide window costs nothing afterwards. Raise
it when patients link with device history already recorded, or pass ``--days``
for a one-off backfill.

``ow.sleep_lookback_days`` (default 7) sets how far back the ``LOOKBACK_TYPES``
are refetched on every poll. See ``Fetcher.lookback``.
"""

import logging
from collections import namedtuple
from datetime import UTC, datetime, timedelta

import requests
from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction
from django.db.models.functions import Coalesce
from django.utils import timezone
from omh_shim import convert

from core.models import (
    CodeableConcept,
    DataSource,
    JheSetting,
    JheUser,
    Observation,
    ObservationIdentifier,
)
from core.services.jhe_settings import get_setting
from core.services.ow_ingest import list_new_objects, read_object

logger = logging.getLogger(__name__)

POLL_OVERLAP = timedelta(minutes=5)
DEFAULT_POLL_WINDOW_DAYS = 1
DEFAULT_SLEEP_LOOKBACK_DAYS = 7
PAGE_LIMIT = 100
MAX_PAGES = 200
HEART_RATE_CODE = "omh:heart-rate:2.0"
BLOOD_GLUCOSE_CODE = "omh:blood-glucose:4.0"

OW_TYPE_TO_CODE = {
    "heart_rate": HEART_RATE_CODE,
    "resting_heart_rate": HEART_RATE_CODE,
    "blood_glucose": BLOOD_GLUCOSE_CODE,
    "oxygen_saturation": "omh:oxygen-saturation:2.0",
    "respiratory_rate": "omh:respiratory-rate:2.0",
    "body_weight": "omh:body-weight:3.0",
    "body_height": "omh:body-height:2.0",
    "sleep_episode": "ieee:sleep-episode:1.0",
    "sleep_stage_summary": "ieee:sleep-stage-summary:1.0",
    "time_in_bed": "ieee:time-in-bed:1.0",
    "sleep_duration": "ieee:total-sleep-time:1.0",
    "physical_activity": "ieee:physical-activity:1.0",
    "workout": "ieee:physical-activity:1.0",
}

OW_TYPE_TO_ROUTE = {
    "sleep_episode": "events/sleep",
    "sleep_stage_summary": "events/sleep",
    "time_in_bed": "events/sleep",
    "sleep_duration": "summaries/sleep",
    "physical_activity": "summaries/activity",
    "workout": "events/workouts",
}
OW_TYPE_TO_SERIES = {
    "body_weight": "weight",
    "body_height": "height",
}
OW_TYPE_TO_SHIM_TYPE = {
    "workout": "physical_activity",
    "resting_heart_rate": "heart_rate",
}
LOOKBACK_TYPES = {
    "sleep_episode",
    "sleep_stage_summary",
    "time_in_bed",
    "sleep_duration",
    "workout",
    "resting_heart_rate",
    "respiratory_rate",
}
NORMALIZED_SYSTEM = "ow:normalized"
RAW_SYSTEM = "ow:raw"
RAW_TRACE_ID_HEART_RATE = "/v2/usercollection/heartrate"
RAW_SUPPORTED_TYPES = {"heart_rate"}
_SYNC_LOCK_KEY = "ow.sync_in_progress"
# A lock older than this is considered abandoned (worker crashed mid-poll)
# and is force-reclaimed by the next tick. Sized at ~2x the default cron
# interval (15 min) so a healthy long-running poll is never preempted.
LOCK_STALE_AFTER = timedelta(minutes=30)

Fetcher = namedtuple("Fetcher", "system shim_source records dedupe_key lookback", defaults=(None,))
"""One source of samples.

``system`` is the ObservationIdentifier system its rows are filed under,
``shim_source`` the omh-shim source name, ``records`` a callable taking
(start_time, end_time) and yielding raw sample dicts, and ``dedupe_key`` a
callable taking one record and returning the string that identifies it, or
None when the record cannot be identified and must be skipped.

``lookback`` replaces the resume watermark with a fixed window when a source
revises records after first publishing them. Oura can update a sleep record
after it is first created, and the watermark would already have advanced past
it, so it would never be fetched again and the upsert would never fire.
Workouts use it too: they sync as late as sleep, and they share a code with the
daily activity summary, whose rows would push the watermark past a late workout.
Resting heart rate does for the same reason: OW stamps it at the start of the
night's sleep, hours behind the heart rate rows that share its code.
Respiratory rate does because OW files it once per night at the start of sleep
and rewrites that sample in place when Oura revises the night.
A source that only ever appends leaves this None and resumes from the watermark.
"""


def _write_sync_lock(value: str) -> None:
    """Write the ow.sync_in_progress JheSetting and bust the cache."""
    with transaction.atomic():
        setting, _ = JheSetting.objects.select_for_update().update_or_create(
            key=_SYNC_LOCK_KEY,
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
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Override the ow.poll_window_days setting for this run. For backfill.",
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
                defaults={"value_type": "string", "value_string": ""},
            )
            current = setting.get_value() or ""
            if current:
                acquired_at = self._parse_lock_timestamp(current)
                if acquired_at is not None and (now - acquired_at) < LOCK_STALE_AFTER:
                    self.stdout.write(f"ow_poll skipped: ow.sync_in_progress since {current}")
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

    @staticmethod
    def _poll_window(options):
        """How far back to ask OW for samples when a patient has nothing to resume from.

        A patient who links after already wearing a device has no prior Observation,
        so this window is the only thing deciding whether their history is reachable.
        Once they have one, the resume watermark is always the later bound, which is
        why widening this costs nothing on subsequent polls.
        """
        days = options.get("days")
        if days is None:
            try:
                days = int(get_setting("ow.poll_window_days", DEFAULT_POLL_WINDOW_DAYS))
            except (TypeError, ValueError):
                days = DEFAULT_POLL_WINDOW_DAYS
        return timedelta(days=max(days, 1))

    @staticmethod
    def _sleep_lookback():
        """How far back the ``LOOKBACK_TYPES`` are refetched, from ``ow.sleep_lookback_days``."""
        try:
            days = int(get_setting("ow.sleep_lookback_days", DEFAULT_SLEEP_LOOKBACK_DAYS))
        except (TypeError, ValueError):
            days = DEFAULT_SLEEP_LOOKBACK_DAYS
        return timedelta(days=max(days, 1))

    def _run_poll(self, options):
        mode = str(get_setting("ow.ingest_mode", "normalized") or "normalized").lower()
        if mode not in ("normalized", "raw"):
            self.stderr.write(f"ow_poll aborted: unknown ow.ingest_mode '{mode}'")
            return

        ow_api_url = (get_setting("ow.api_url", "") or "").rstrip("/")
        ow_api_key = get_setting("ow.api_key", "")
        if mode == "normalized" and (not ow_api_url or not ow_api_key):
            self.stderr.write("ow_poll aborted: ow.api_url / ow.api_key not configured")
            return

        codes = {}
        for ow_type, coding_code in OW_TYPE_TO_CODE.items():
            try:
                codes[ow_type] = CodeableConcept.objects.get(coding_code=coding_code)
            except CodeableConcept.DoesNotExist:
                self.stderr.write(f"CodeableConcept '{coding_code}' not found. Run seed first.")
        if not codes:
            return

        oura_ds, _ = DataSource.objects.get_or_create(name="Oura", defaults={"type": "personal_device"})
        poll_window = self._poll_window(options)

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
            polled = {t: c for t, c in codes.items() if c.coding_code in consented_codes}
            if not polled:
                continue

            for ow_type, code in polled.items():
                try:
                    ow_user_id = user.identifier.removeprefix("ow:")
                    if mode == "normalized":
                        fetcher = self._normalized_fetcher(user, ow_user_id, ow_api_url, ow_api_key, ow_type)
                    else:
                        fetcher = self._raw_fetcher(user, ow_user_id, ow_type)
                    if fetcher is None:
                        continue
                    total_created += self._poll_user(user, patient, ow_type, code, oura_ds, fetcher, poll_window)
                except Exception:
                    logger.exception("ow_poll failed for jhe_user_id=%s type=%s", user.id, ow_type)

        self.stdout.write(self.style.SUCCESS(f"OW poll complete (mode={mode}). Created {total_created} observations."))

    def _fetch_page(self, user, ow_api_url, ow_api_key, ow_user_id, path, params):
        """Yield every OW record across pages.

        Every OW list route returns the same envelope, so one loop serves them
        all. ``params`` carries whatever that route needs, because the timeseries
        route windows on ``start_time``/``end_time`` while the events and
        summaries routes window on ``start_date``/``end_date``.
        """
        cursor = None
        for _ in range(MAX_PAGES):
            page_params = dict(params, limit=PAGE_LIMIT)
            if cursor:
                page_params["cursor"] = cursor

            try:
                resp = requests.get(
                    f"{ow_api_url}/api/v1/users/{ow_user_id}/{path}",
                    params=page_params,
                    headers={"X-Open-Wearables-API-Key": ow_api_key},
                    timeout=30,
                )
            except requests.RequestException as e:
                logger.error("OW request failed for user=%s path=%s: %s", user.id, path, e)
                return

            if resp.status_code != 200:
                logger.error(
                    "OW error for user=%s path=%s: %s %s",
                    user.id,
                    path,
                    resp.status_code,
                    resp.text[:300],
                )
                return

            data = resp.json()
            records = data.get("data", data) if isinstance(data, dict) else data
            if not isinstance(records, list):
                logger.warning("OW returned non-list payload for user=%s path=%s", user.id, path)
                return
            yield from records

            pagination = data.get("pagination") or {} if isinstance(data, dict) else {}
            next_cursor = pagination.get("next_cursor")
            if not pagination.get("has_more") or not next_cursor or next_cursor == cursor:
                return
            cursor = next_cursor

        logger.warning("OW hit MAX_PAGES for user=%s path=%s", user.id, path)

    def _timeseries_dedupe_key(self, ow_user_id, ow_type, record):
        """Identify one timeseries sample.

        OW timeseries samples carry no id of their own, so the natural key is the
        patient, the series and the instant. The OW user id is part of the key
        because ObservationIdentifier is unique on (system, value) globally, not
        per patient, so two patients reporting the same value at the same instant
        would otherwise collide and the second row would be dropped.

        The series comes off the sample rather than from the data type, because
        several OW series can share one data type: ``resting_heart_rate`` and
        ``heart_rate`` both convert as ``heart_rate`` and both file under
        ``omh:heart-rate:2.0``, so keying on the data type would collide them.
        """
        timestamp = record.get("timestamp")
        if not timestamp:
            return None
        series = record.get("type") or OW_TYPE_TO_SERIES.get(ow_type, ow_type)
        return f"{ow_user_id}:{series}:{timestamp}"

    def _event_dedupe_key(self, ow_user_id, ow_type, record):
        """Identify one OW event record.

        Events carry their own id, so it is the natural key. The OW user id is
        prefixed for the same reason as the timeseries key: ObservationIdentifier
        is unique on (system, value) globally rather than per patient. The data
        type is part of the key because one OW record feeds several data types --
        a SleepSession is read as both a sleep episode and a sleep stage summary --
        so the id alone cannot tell those Observations apart.
        """
        record_id = record.get("id")
        if not record_id:
            return None
        return f"{ow_user_id}:{ow_type}:{record_id}"

    def _summary_dedupe_key(self, ow_user_id, ow_type, record):
        """Identify one OW daily summary.

        Summaries carry no id, only the day they summarise, and a patient has one
        summary of a given kind per day.
        """
        day = record.get("date")
        if not day:
            return None
        return f"{ow_user_id}:{ow_type}:{day}"

    def _normalized_fetcher(self, user, ow_user_id, ow_api_url, ow_api_key, ow_type):
        """Samples from whichever OW route serves this data type."""
        path = OW_TYPE_TO_ROUTE.get(ow_type, "timeseries")
        if path == "timeseries":
            series = OW_TYPE_TO_SERIES.get(ow_type, ow_type)
            params = lambda start_time, end_time: {
                "types": series,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
            }
            dedupe_key = lambda record: self._timeseries_dedupe_key(ow_user_id, ow_type, record)
        else:
            params = lambda start_time, end_time: {
                "start_date": start_time.isoformat(),
                "end_date": end_time.isoformat(),
            }
            if path.startswith("events/"):
                dedupe_key = lambda record: self._event_dedupe_key(ow_user_id, ow_type, record)
            else:
                dedupe_key = lambda record: self._summary_dedupe_key(ow_user_id, ow_type, record)

        return Fetcher(
            system=NORMALIZED_SYSTEM,
            shim_source="ow_normalized",
            records=lambda start_time, end_time: self._fetch_page(
                user, ow_api_url, ow_api_key, ow_user_id, path, params(start_time, end_time)
            ),
            dedupe_key=dedupe_key,
            lookback=self._sleep_lookback() if ow_type in LOOKBACK_TYPES else None,
        )

    def _raw_fetcher(self, user, ow_user_id, ow_type):
        """Samples from Oura payloads OW archived to its bucket.

        Returns None for a type raw mode cannot serve, so the caller skips it
        without listing the bucket.
        """
        if ow_type not in RAW_SUPPORTED_TYPES:
            return None
        return Fetcher(
            system=RAW_SYSTEM,
            shim_source="oura_raw",
            records=lambda start_time, end_time: self._fetch_raw_objects(user, ow_user_id, start_time),
            dedupe_key=lambda record: self._timeseries_dedupe_key(ow_user_id, ow_type, record),
        )

    def _fetch_raw_objects(self, user, ow_user_id, start_time):
        """Yield every record inside the heart-rate S3 objects newer than start_time."""
        try:
            objects = list_new_objects(ow_user_id, start_time)
        except Exception as e:
            logger.error("OW raw S3 list failed for user=%s: %s", user.id, e)
            return

        for obj in objects:
            if RAW_TRACE_ID_HEART_RATE not in obj.key:
                continue
            try:
                payload = read_object(obj.key)
            except Exception:
                logger.warning("Skipping unreadable raw object %s", obj.key, exc_info=True)
                continue
            yield from payload.get("data", [])

    def _poll_user(self, user, patient, ow_type, code, data_source, fetcher, poll_window):
        """Ingest one data type for one patient from one source."""
        end_time = timezone.now()
        if fetcher.lookback is None:
            start_time = self._resume_start_time(patient, code, fetcher.system, end_time - poll_window)
        else:
            start_time = end_time - max(fetcher.lookback, poll_window)

        created = 0
        for record in fetcher.records(start_time, end_time):
            try:
                shim_type = OW_TYPE_TO_SHIM_TYPE.get(ow_type, ow_type)
                omh_record = convert(source=fetcher.shim_source, data_type=shim_type, sample=record, tz=UTC)
            except Exception:
                logger.warning("Skipping unconvertible record for user=%s type=%s", user.id, ow_type, exc_info=True)
                continue

            identifier = fetcher.dedupe_key(record)
            if not identifier:
                continue

            if self._save_observation(patient, code, data_source, omh_record, fetcher.system, identifier):
                created += 1

        logger.info("Poll completed for jhe_user=%s patient=%s created=%d", user.id, patient.id, created)
        return created

    def _resume_start_time(self, patient, code, system, window_start):
        """Return the time this poll should ask OW from.

        Resuming from the most recent ingested row avoids refetching the whole
        window every tick. POLL_OVERLAP is subtracted so a sample written on the
        boundary of the previous run is not missed.

        Sample time, not write time: Oura sleep only syncs when the patient opens
        the app, so a night can arrive after rows measured later were already
        written. Resuming from the write time would skip it permanently.

        An interval-shaped body populates ``effective_period_start`` and leaves
        ``effective_date_time`` null, so the two are coalesced. Ordering on the
        raw column instead would sort nulls first under a descending Postgres
        sort, pinning the watermark to whichever interval row happened to exist.
        """
        sample_time = Coalesce("effective_date_time", "effective_period_start")
        last_obs = (
            Observation.objects.filter(
                subject_patient=patient,
                codeable_concept=code,
                identifiers__system=system,
            )
            .annotate(sample_time=sample_time)
            .exclude(sample_time=None)
            .order_by("-sample_time")
            .first()
        )
        if not last_obs:
            return window_start
        return max(window_start, last_obs.sample_time - POLL_OVERLAP)

    def _save_observation(self, patient, code, data_source, omh_record, system, value):
        """Create one Observation and its dedupe identifier, or overwrite the existing one.

        Last write wins: Oura can update a sleep record after it is first created,
        so a record whose key is already stored replaces the stored body rather
        than being skipped. An unchanged body is left alone: the header's uuid and
        creation time are regenerated on every convert, so rewriting it would bump
        ``last_updated`` on every tick of the lookback window.

        Returns True only when a row was created. A concurrent tick can win the
        race between the lookup and the insert, so IntegrityError is treated as
        already-ingested rather than an error. Any other failure, on create or
        update, is logged and the record skipped, so the rest of the poll still lands.

        The lookup is scoped to the patient because JheUser.identifier is not
        unique. When two patients share a dedupe key, the second one's insert hits
        the unique constraint and is skipped rather than overwriting the first's row.
        """
        try:
            with transaction.atomic():
                existing = (
                    Observation.objects.filter(
                        subject_patient=patient, identifiers__system=system, identifiers__value=value
                    )
                    .order_by("id")
                    .first()
                )
                if existing is not None:
                    if existing.omh_data.get("body") == omh_record.get("body"):
                        return False
                    existing.omh_data = omh_record
                    existing.save()
                    return False

                obs = Observation.objects.create(
                    subject_patient=patient,
                    codeable_concept=code,
                    data_source=data_source,
                    omh_data=omh_record,
                    status="final",
                )
                ObservationIdentifier.objects.create(
                    observation=obs,
                    system=system,
                    value=value,
                )
            return True
        except IntegrityError:
            return False
        except Exception:
            logger.warning(
                "Failed to persist observation for patient=%s identifier=%s",
                patient.id,
                value,
                exc_info=True,
            )
            return False
