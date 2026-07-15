"""Seed Oura-style wearable observations (sleep / activity / vitals) onto the
existing "Iglu CGM Test Data" study patients, aligned to each patient's own CGM
date window so CGM + wearable data share a coherent timeline.

Simulation logic is ported from JP's student's generator
(github.com/dicristea/oura-clinical-workbench, demo_data/omh_ieee_generator):
risk-score-correlated sleep, activity, and nightly vitals.

Run on fly:  python manage.py shell -c "exec(open('/tmp/load_wearables.py').read())"
Run locally: python manage.py shell < scripts/load_wearables.py
"""

import math
import random
import uuid
from datetime import UTC, datetime, time, timedelta

from django.db import connection, transaction

from core.models import (
    CodeableConcept,
    DataSource,
    Observation,
    Study,
    StudyDataSource,
    StudyPatient,
    StudyPatientScopeConsent,
    StudyScopeRequest,
)

STUDY_NAME = "Iglu CGM Test Data"
SOURCE_NAME = "demo-synthea-omh-ieee-generator"  # credit: dicristea/oura-clinical-workbench
DATA_SOURCE_NAME = "Oura"
SEED = 4242
MAX_DAYS = 21  # cap per-patient window so volume stays reasonable

OMH = "https://w3id.org/openmhealth"
IEEE = "https://w3id.org/ieee1752"

# scope coding_code -> (coding_system, label). Order is the per-day record order.
SCOPES = [
    ("ieee:physical-activity:1.0", IEEE, "Physical activity"),
    ("ieee:sleep-episode:1.0", IEEE, "Sleep episode (IEEE)"),
    ("ieee:time-in-bed:1.0", IEEE, "Time in bed"),
    ("omh:heart-rate:2.0", OMH, "Heart Rate"),
    ("omh:respiratory-rate:2.0", OMH, "Respiratory rate"),
    ("omh:oxygen-saturation:2.0", OMH, "Oxygen saturation"),
    ("omh:total-sleep-time:1.0", OMH, "Total sleep time"),
    ("omh:sleep-episode:1.1", OMH, "Sleep episode"),
]


def _iso(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat().replace("+00:00", "Z")


def _header(coding_code):
    namespace, name, version = coding_code.split(":", 2)
    return {
        "header": {
            "uuid": str(uuid.uuid4()),
            "source_creation_date_time": _iso(datetime.now(UTC)),
            "schema_id": {"namespace": namespace, "name": name, "version": version},
            "modality": "sensed",
            "acquisition_provenance": {"source_name": SOURCE_NAME},
        }
    }


def _uv(value, unit, digits=1):
    return {"value": round(float(value), digits), "unit": unit}


def _dur(seconds):
    return {"value": int(round(float(seconds))), "unit": "sec"}


def _interval(start, end):
    return {
        "time_interval": {
            "start_date_time": _iso(start),
            "end_date_time": _iso(end),
            "duration": _dur((end - start).total_seconds()),
        }
    }


def _risk_score(age):
    # No conditions on iglu patients -> age-driven baseline (ported from simulate._risk_score).
    return max(0.0, min(0.85, 0.12 + max(age - 40, 0) / 200))


def _generate_day(day, day_index, age, risk, rng):
    """Port of simulate._generate_day (scalar signals; HR time-series omitted)."""
    weekday_factor = 0.35 if day.weekday() >= 5 else 0.0

    bedtime_hour = 22 + rng.uniform(0.0, 1.5) + 0.25 * risk
    bedtime_minute = int((bedtime_hour % 1) * 60)
    sleep_start = datetime.combine(
        day - timedelta(days=1),
        time(hour=int(bedtime_hour), minute=bedtime_minute, tzinfo=UTC),
    )

    latency_sec = int((10 + 25 * risk + weekday_factor * 5 + rng.uniform(-3, 8)) * 60)
    wake_after_sec = int((20 + 40 * risk + rng.uniform(-5, 15)) * 60)
    time_in_bed_sec = int((7.6 - 0.01 * max(age - 45, 0) - 0.4 * risk + rng.uniform(-0.5, 0.5)) * 3600)
    time_in_bed_sec = max(6 * 3600, min(9 * 3600, time_in_bed_sec))

    total_sleep_sec = time_in_bed_sec - latency_sec - wake_after_sec
    total_sleep_sec = max(4 * 3600, min(time_in_bed_sec - 300, total_sleep_sec))

    deep_pct = max(0.12, min(0.24, 0.20 - 0.06 * risk + rng.uniform(-0.02, 0.02)))
    rem_pct = max(0.14, min(0.28, 0.22 - 0.05 * risk + rng.uniform(-0.02, 0.02)))
    deep_sec = int(total_sleep_sec * deep_pct)
    rem_sec = int(total_sleep_sec * rem_pct)
    light_sec = max(0, total_sleep_sec - deep_sec - rem_sec)
    efficiency = round(total_sleep_sec / time_in_bed_sec * 100, 1)
    sleep_end = sleep_start + timedelta(seconds=time_in_bed_sec)

    activity_drag = 1200 * risk + max(age - 55, 0) * 55
    steps = int(max(1500, 9000 - activity_drag + 700 * math.sin(day_index / 4) + rng.uniform(-1800, 1800)))
    distance_m = round(steps * rng.uniform(0.68, 0.80), 1)
    active_kcal = round(max(120.0, 260 + steps * 0.035 + rng.uniform(-60, 80)), 1)
    light_act = int(max(20, 95 - 25 * risk + rng.uniform(-20, 20)) * 60)
    mod_act = int(max(5, 35 - 15 * risk + rng.uniform(-10, 15)) * 60)
    vig_act = int(max(0, 12 - 10 * risk + rng.uniform(-6, 8)) * 60)

    avg_hr = round(55 + 10 * risk + max(age - 50, 0) * 0.12 + rng.uniform(-4, 4), 1)
    resp_rate = round(13 + 2.5 * risk + rng.uniform(-1.0, 1.5), 1)
    spo2 = round(max(92.5, 98.2 - 1.8 * risk + rng.uniform(-0.7, 0.4)), 1)

    day_start = sleep_end.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start.replace(hour=23, minute=59, second=59)
    sleep_iv = {"effective_time_frame": _interval(sleep_start, sleep_end)}

    # Build the 8 single-object-body records, keyed by scope coding_code.
    records = {}

    pa = _header("ieee:physical-activity:1.0")
    pa["body"] = {
        "activity_name": "Total Daily Physical Activity",
        "effective_time_frame": _interval(day_start, day_end),
        "base_movement_quantity": _uv(steps, "steps", 0),
        "distance": _uv(distance_m, "m"),
        "kcal_burned": _uv(active_kcal, "kcal"),
        "duration": _dur(light_act + mod_act + vig_act),
        "duration_light_activity": _dur(light_act),
        "duration_moderate_activity": _dur(mod_act),
        "duration_vigorous_activity": _dur(vig_act),
        "descriptive_statistic": "sum",
        "descriptive_statistic_denominator": "d",
    }
    records["ieee:physical-activity:1.0"] = pa

    ieee_sleep = _header("ieee:sleep-episode:1.0")
    ieee_sleep["body"] = {
        "latency_to_sleep_onset": _dur(latency_sec),
        "total_sleep_time": _dur(total_sleep_sec),
        "light_sleep_duration": _dur(light_sec),
        "deep_sleep_duration": _dur(deep_sec),
        "rem_sleep_duration": _dur(rem_sec),
        "wake_after_sleep_onset": _dur(wake_after_sec),
        "is_main_sleep": True,
        "sleep_efficiency_percentage": _uv(efficiency, "%"),
        **sleep_iv,
    }
    records["ieee:sleep-episode:1.0"] = ieee_sleep

    tib = _header("ieee:time-in-bed:1.0")
    tib["body"] = {"time_in_bed": _dur(time_in_bed_sec), "is_main_sleep": True, **sleep_iv}
    records["ieee:time-in-bed:1.0"] = tib

    hr = _header("omh:heart-rate:2.0")
    hr["body"] = {
        "heart_rate": _uv(avg_hr, "beats/min"),
        "descriptive_statistic": "average",
        "temporal_relationship_to_sleep": "during",
        **sleep_iv,
    }
    records["omh:heart-rate:2.0"] = hr

    rr = _header("omh:respiratory-rate:2.0")
    rr["body"] = {"respiratory_rate": _uv(resp_rate, "breaths/min"), "descriptive_statistic": "average", **sleep_iv}
    records["omh:respiratory-rate:2.0"] = rr

    o2 = _header("omh:oxygen-saturation:2.0")
    o2["body"] = {
        "oxygen_saturation": _uv(spo2, "%"),
        "descriptive_statistic": "average",
        "measurement_method": "pulse oximetry",
        "system": "peripheral capillary",
        **sleep_iv,
    }
    records["omh:oxygen-saturation:2.0"] = o2

    tst = _header("omh:total-sleep-time:1.0")
    tst["body"] = {"total_sleep_time": _dur(total_sleep_sec), **sleep_iv}
    records["omh:total-sleep-time:1.0"] = tst

    omh_sleep = _header("omh:sleep-episode:1.1")
    omh_sleep["body"] = {
        "latency_to_sleep_onset": _dur(latency_sec),
        "total_sleep_time": _dur(total_sleep_sec),
        "wake_after_sleep_onset": _dur(wake_after_sec),
        "is_main_sleep": True,
        "sleep_maintenance_efficiency_percentage": _uv(efficiency, "%"),
        **sleep_iv,
    }
    records["omh:sleep-episode:1.1"] = omh_sleep

    return records


def main():
    study = Study.objects.filter(name=STUDY_NAME).first()
    if not study:
        print(f"ERROR: study '{STUDY_NAME}' not found — run `manage.py iglu` first.")
        return

    study_patients = list(
        StudyPatient.objects.filter(study=study)
        .select_related("patient")
        .prefetch_related("patient__identifiers")
        .order_by("id")
    )
    if not study_patients:
        print("ERROR: study has no patients yet.")
        return

    bg = CodeableConcept.objects.get(coding_code="omh:blood-glucose:4.0")

    # Per-patient CGM date window (min/max of glucose source_creation_date_time).
    patient_ids = [sp.patient_id for sp in study_patients]
    windows = {}
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT subject_patient_id,
                   min((omh_data #>> '{header,source_creation_date_time}')::timestamptz),
                   max((omh_data #>> '{header,source_creation_date_time}')::timestamptz)
            FROM core_observation
            WHERE codeable_concept_id = %s AND subject_patient_id = ANY(%s)
            GROUP BY subject_patient_id
            """,
            [bg.id, patient_ids],
        )
        for pid, lo, hi in cur.fetchall():
            windows[pid] = (lo, hi)

    data_source, _ = DataSource.objects.get_or_create(name=DATA_SOURCE_NAME, defaults={"type": "personal_device"})

    # CodeableConcepts (create the 5 new ones; reuse the 3 existing omh vitals).
    cc_by_code = {}
    for code, system, label in SCOPES:
        cc, _ = CodeableConcept.objects.get_or_create(
            coding_code=code, defaults={"coding_system": system, "text": label}
        )
        cc_by_code[code] = cc

    with transaction.atomic():
        StudyDataSource.objects.get_or_create(study=study, data_source=data_source)
        for code, _system, _label in SCOPES:
            StudyScopeRequest.objects.get_or_create(
                study=study, scope_code=cc_by_code[code], defaults={"scope_actions": "rs"}
            )

        to_create = []
        per_patient = []
        for sp in study_patients:
            win = windows.get(sp.patient_id)
            if not win or not win[0]:
                continue
            lo, hi = win
            start_day, end_day = lo.date(), hi.date()
            span = (end_day - start_day).days + 1
            if span > MAX_DAYS:
                start_day = end_day - timedelta(days=MAX_DAYS - 1)
                span = MAX_DAYS

            birth = sp.patient.birth_date
            age = end_day.year - birth.year - ((end_day.month, end_day.day) < (birth.month, birth.day)) if birth else 50
            risk = _risk_score(age)
            # Stable subject identifier (e.g. "1636-69-001"), now stored on the related
            # PatientIdentifier model (system="iglu"); fall back to PK if absent.
            ident = next(
                (i.value for i in sp.patient.identifiers.all() if i.system == "iglu"),
                None,
            ) or str(sp.patient_id)
            # Seed on the stable identifier, not the DB PK, so the same patient gets
            # identical values across re-seeds.
            rng = random.Random(f"{SEED}:{ident}")

            # Consent once per patient per scope.
            for code, _system, _label in SCOPES:
                StudyPatientScopeConsent.objects.update_or_create(
                    study_patient=sp,
                    scope_code=cc_by_code[code],
                    defaults={
                        "consented": True,
                        "consented_time": lo - timedelta(days=3),
                        "scope_actions": "rs",
                    },
                )

            day_count = 0
            for offset in range(span):
                day = start_day + timedelta(days=offset)
                records = _generate_day(day, offset, age, risk, rng)
                for code, _system, _label in SCOPES:
                    to_create.append(
                        Observation(
                            subject_patient=sp.patient,
                            codeable_concept=cc_by_code[code],
                            data_source=data_source,
                            status="final",
                            omh_data=records[code],
                        )
                    )
                day_count += 1
            per_patient.append((ident, age, risk, start_day, end_day, day_count))

        Observation.objects.bulk_create(to_create, batch_size=2000)

    print(f"Created {len(to_create)} wearable observations across {len(per_patient)} patients.")
    print(f"{'subject':<14}{'age':>4}{'risk':>6}  window                     days")
    for ident, age, risk, s, e, d in per_patient:
        print(f"{ident:<14}{age:>4}{risk:>6.2f}  {s} -> {e}  {d:>3}")


main()
