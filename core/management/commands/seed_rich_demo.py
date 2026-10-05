"""Generate a synthetic demo cohort (CGM + Oura wearables) ending today.

Instead of loading recorded data, this synthesizes plausible CGM glucose and
Oura-style wearable observations for a fixed roster of demo patients. All data is
clearly synthetic (distinct acquisition_provenance.source_name) and dated up to
the run date.

Wearable simulation math references JP's student's generator
(github.com/dicristea/oura-clinical-workbench, demo_data/omh_ieee_generator).
"""

import math
import random
import uuid
from datetime import UTC, datetime, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone as dj_timezone
from django.utils.crypto import get_random_string

from core.models import (
    CodeableConcept,
    DataSource,
    JheUser,
    Observation,
    Organization,
    PatientIdentifier,
    Study,
    StudyDataSource,
    StudyPatient,
    StudyPatientScopeConsent,
    StudyScopeRequest,
)

STUDY_NAME = "CGM & Wearables Demo"
ORG_NAME_FRAGMENT = "Planetary Research Institute"
CGM_DATA_SOURCE = "Dexcom Stelo"
WEARABLE_DATA_SOURCE = "Oura"
CGM_SOURCE_NAME = "demo-cgm-generator"
WEARABLE_SOURCE_NAME = "demo-synthea-omh-ieee-generator"  # credit: dicristea/oura-clinical-workbench
SEED = 4242

CGM_CODE = "omh:blood-glucose:4.0"
CGM_INTERVAL_MINUTES = 15
CGM_WINDOW_DAYS = 14  # recent dense CGM window (a realistic wear period)
WEARABLE_MIN_DAYS = 60  # per-patient wearable history is varied in this range...
WEARABLE_MAX_DAYS = 180  # ...so the cohort has a realistic spread, all ending today

OMH = "https://w3id.org/openmhealth"
IEEE = "https://w3id.org/ieee1752"

# (coding_code, coding_system, label) — order is the per-day record order.
WEARABLE_SCOPES = [
    ("omh:physical-activity:1.2", OMH, "Physical activity"),
    ("omh:step-count:3.0", OMH, "Step count"),
    ("ieee:sleep-stage-summary:1.0", IEEE, "Sleep stage summary"),
    ("omh:sleep-episode:1.1", OMH, "Sleep episode"),
    ("omh:sleep-duration:2.0", OMH, "Sleep duration"),
    ("omh:heart-rate:2.0", OMH, "Heart Rate"),
    ("omh:respiratory-rate:2.0", OMH, "Respiratory rate"),
    ("omh:oxygen-saturation:2.0", OMH, "Oxygen saturation"),
]

MOCK_PATIENTS = [
    {
        "name_family": "Nguyen",
        "name_given": "May",
        "birth_date": "1984-07-11",
        "telecom_phone": "265-642-0143",
        "email": "may.nguyen@example.com",
    },
    {
        "name_family": "Smith",
        "name_given": "Olivia",
        "birth_date": "1976-03-23",
        "telecom_phone": "187-554-0198",
        "email": "olivia.smith@example.com",
    },
    {
        "name_family": "Chen",
        "name_given": "Liang",
        "birth_date": "1948-11-30",
        "telecom_phone": "997-576-0102",
        "email": "liang.chen@example.com",
    },
    {
        "name_family": "Patel",
        "name_given": "Anika",
        "birth_date": "1989-01-17",
        "telecom_phone": "345-233-0170",
        "email": "anika.patel@example.com",
    },
    {
        "name_family": "Garcia",
        "name_given": "Carlos",
        "birth_date": "1955-05-04",
        "telecom_phone": "609-442-0186",
        "email": "carlos.garcia@example.com",
    },
    {
        "name_family": "Okafor",
        "name_given": "Chinelo",
        "birth_date": "1962-08-19",
        "telecom_phone": "435-287-0116",
        "email": "chinelo.okafor@example.com",
    },
    {
        "name_family": "Kowalski",
        "name_given": "Zofia",
        "birth_date": "1945-02-14",
        "telecom_phone": "399-765-0124",
        "email": "zofia.kowalski@example.com",
    },
    {
        "name_family": "Tanaka",
        "name_given": "Hiroshi",
        "birth_date": "1958-10-01",
        "telecom_phone": "298-443-0131",
        "email": "hiroshi.tanaka@example.com",
    },
    {
        "name_family": "Abdullah",
        "name_given": "Layla",
        "birth_date": "1973-12-25",
        "telecom_phone": "198-619-0149",
        "email": "layla.abdullah@example.com",
    },
    {
        "name_family": "Dubois",
        "name_given": "Emile",
        "birth_date": "1981-06-03",
        "telecom_phone": "400-870-0162",
        "email": "emile.dubois@example.com",
    },
    {
        "name_family": "Singh",
        "name_given": "Raj",
        "birth_date": "1992-09-12",
        "telecom_phone": "398-112-0181",
        "email": "raj.singh@example.com",
    },
    {
        "name_family": "Martinez",
        "name_given": "Sofia",
        "birth_date": "1967-07-27",
        "telecom_phone": "229-998-0108",
        "email": "sofia.martinez@example.com",
    },
    {
        "name_family": "Kim",
        "name_given": "Jisoo",
        "birth_date": "1950-04-20",
        "telecom_phone": "988-889-0157",
        "email": "jisoo.kim@example.com",
    },
    {
        "name_family": "Ivanov",
        "name_given": "Dmitri",
        "birth_date": "1983-02-05",
        "telecom_phone": "799-443-0129",
        "email": "dmitri.ivanov@example.com",
    },
    {
        "name_family": "Mbatha",
        "name_given": "Sipho",
        "birth_date": "1979-11-08",
        "telecom_phone": "762-112-0140",
        "email": "sipho.mbatha@example.com",
    },
    {
        "name_family": "Rossi",
        "name_given": "Giulia",
        "birth_date": "1960-05-30",
        "telecom_phone": "772-981-0169",
        "email": "giulia.rossi@example.com",
    },
    {
        "name_family": "Hernandez",
        "name_given": "Luis",
        "birth_date": "1952-03-14",
        "telecom_phone": "118-112-0194",
        "email": "luis.hernandez@example.com",
    },
    {
        "name_family": "Yilmaz",
        "name_given": "Aylin",
        "birth_date": "1972-01-01",
        "telecom_phone": "388-887-0175",
        "email": "aylin.yilmaz@example.com",
    },
    {
        "name_family": "Andersson",
        "name_given": "Lars",
        "birth_date": "1988-10-10",
        "telecom_phone": "334-874-0111",
        "email": "lars.andersson@example.com",
    },
    {
        "name_family": "Ali",
        "name_given": "Zara",
        "birth_date": "1947-06-06",
        "telecom_phone": "202-555-0188",
        "email": "zara.ali@example.com",
    },
]


def _iso(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat().replace("+00:00", "Z")


def _uv(value, unit, digits=1):
    return {"value": round(float(value), digits), "unit": unit}


def _dur(seconds):
    return {"value": int(round(float(seconds))), "unit": "sec"}


def _interval(start, end):
    # Use start+end only (no duration) so the oneOf in time-interval-1.x.json is satisfied
    # unambiguously — the schema's oneOf would fail if all three keys are present.
    return {
        "time_interval": {
            "start_date_time": _iso(start),
            "end_date_time": _iso(end),
        }
    }


def _wearable_header(coding_code, created):
    namespace, name, version = coding_code.split(":", 2)
    return {
        "header": {
            "uuid": str(uuid.uuid4()),
            "source_creation_date_time": _iso(created),
            "schema_id": {"namespace": namespace, "name": name, "version": version},
            "modality": "sensed",
            "acquisition_provenance": {"source_name": WEARABLE_SOURCE_NAME},
        }
    }


# Minute-of-day for breakfast / lunch / dinner; CGM rises for ~3h after each.
_MEAL_MINUTES = (7 * 60 + 30, 12 * 60 + 30, 18 * 60 + 30)


def cgm_value(dt, risk, rng):
    """Plausible (not clinical-grade) glucose: baseline + dawn rhythm + meal
    excursions + noise, clamped to a physiologic range. Higher risk -> higher
    baseline and bigger excursions.

    Meal timing is read off ``dt``'s own clock (callers pass UTC datetimes), so
    the breakfast/lunch/dinner peaks track whatever timezone ``dt`` carries."""
    minutes = dt.hour * 60 + dt.minute
    baseline = 100 + 40 * risk
    diurnal = 8 * math.sin((minutes - 300) / 1440 * 2 * math.pi)
    spike = 0.0
    for meal in _MEAL_MINUTES:
        dm = minutes - meal
        if 0 <= dm <= 180:
            spike += (45 + 60 * risk) * math.exp(-((dm - 45) ** 2) / (2 * 35**2))
    value = baseline + diurnal + spike + rng.gauss(0, 6)
    return max(40, min(300, int(round(value))))


def risk_score(age):
    # Age-driven baseline risk (ported from JP's student's simulate._risk_score).
    return max(0.0, min(0.85, 0.12 + max(age - 40, 0) / 200))


def generate_wearable_day(day, day_index, age, risk, rng):
    """Port of simulate._generate_day: returns the 8 single-object-body records
    keyed by scope coding_code (scalar signals; HR time-series omitted)."""
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

    avg_hr = round(55 + 10 * risk + max(age - 50, 0) * 0.12 + rng.uniform(-4, 4), 1)
    resp_rate = round(13 + 2.5 * risk + rng.uniform(-1.0, 1.5), 1)
    spo2 = round(max(92.5, 98.2 - 1.8 * risk + rng.uniform(-0.7, 0.4)), 1)

    day_start = sleep_end.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start.replace(hour=23, minute=59, second=59)

    # Creation timestamp for this day's observations: morning of the observation day.
    created = datetime.combine(day, time(8, 0, tzinfo=UTC))

    records = {}

    # omh:physical-activity:1.2 — required: activity_name
    # Schema allows: activity_name, effective_time_frame, distance, kcal_burned,
    #   reported_activity_intensity, met_value. No additionalProperties: false.
    pa = _wearable_header("omh:physical-activity:1.2", created)
    pa["body"] = {
        "activity_name": "Total Daily Physical Activity",
        "effective_time_frame": _interval(day_start, day_end),
        "distance": _uv(distance_m, "m"),
        "kcal_burned": _uv(active_kcal, "kcal"),
        "reported_activity_intensity": "moderate",
    }
    records["omh:physical-activity:1.2"] = pa

    # omh:step-count:3.0 — required: step_count, effective_time_frame
    sc = _wearable_header("omh:step-count:3.0", created)
    sc["body"] = {
        "step_count": _uv(steps, "steps", 0),
        "effective_time_frame": _interval(day_start, day_end),
        "descriptive_statistic": "sum",
        "descriptive_statistic_denominator": "d",
    }
    records["omh:step-count:3.0"] = sc

    # ieee:sleep-stage-summary:1.0 — required: sleep_stage_summary (with total_sleep_time), effective_time_frame
    sss = _wearable_header("ieee:sleep-stage-summary:1.0", created)
    sss["body"] = {
        "sleep_stage_summary": {
            "total_sleep_time": _dur(total_sleep_sec),
            "light_sleep_duration": _dur(light_sec),
            "deep_sleep_duration": _dur(deep_sec),
            "rem_sleep_duration": _dur(rem_sec),
            "sleep_efficiency_percentage": _uv(efficiency, "%"),
        },
        "effective_time_frame": _interval(sleep_start, sleep_end),
        "is_main_sleep": True,
    }
    records["ieee:sleep-stage-summary:1.0"] = sss

    # omh:sleep-episode:1.1 — required: effective_time_frame (time_interval)
    omh_sleep = _wearable_header("omh:sleep-episode:1.1", created)
    omh_sleep["body"] = {
        "effective_time_frame": _interval(sleep_start, sleep_end),
        "latency_to_sleep_onset": _dur(latency_sec),
        "total_sleep_time": _dur(total_sleep_sec),
        "wake_after_sleep_onset": _dur(wake_after_sec),
        "is_main_sleep": True,
        "sleep_maintenance_efficiency_percentage": _uv(efficiency, "%"),
    }
    records["omh:sleep-episode:1.1"] = omh_sleep

    # omh:sleep-duration:2.0 — required: sleep_duration, effective_time_frame (time_interval)
    sd = _wearable_header("omh:sleep-duration:2.0", created)
    sd["body"] = {
        "sleep_duration": _dur(total_sleep_sec),
        "effective_time_frame": _interval(sleep_start, sleep_end),
    }
    records["omh:sleep-duration:2.0"] = sd

    # omh:heart-rate:2.0 — required: heart_rate, effective_time_frame
    hr = _wearable_header("omh:heart-rate:2.0", created)
    hr["body"] = {
        "heart_rate": _uv(avg_hr, "beats/min"),
        "effective_time_frame": _interval(sleep_start, sleep_end),
        "descriptive_statistic": "average",
        "temporal_relationship_to_sleep": "during sleep",
    }
    records["omh:heart-rate:2.0"] = hr

    # omh:respiratory-rate:2.0 — required: respiratory_rate, effective_time_frame
    rr = _wearable_header("omh:respiratory-rate:2.0", created)
    rr["body"] = {
        "respiratory_rate": _uv(resp_rate, "breaths/min"),
        "effective_time_frame": _interval(sleep_start, sleep_end),
        "descriptive_statistic": "average",
    }
    records["omh:respiratory-rate:2.0"] = rr

    # omh:oxygen-saturation:2.0 — required: oxygen_saturation, effective_time_frame
    o2 = _wearable_header("omh:oxygen-saturation:2.0", created)
    o2["body"] = {
        "oxygen_saturation": _uv(spo2, "%"),
        "effective_time_frame": _interval(sleep_start, sleep_end),
        "descriptive_statistic": "average",
        "measurement_method": "pulse oximetry",
        "system": "peripheral capillary",
    }
    records["omh:oxygen-saturation:2.0"] = o2

    return records


def cgm_body(dt, value):
    """Build the OMH blood-glucose body for a single reading at ``dt``.

    For synthetic historical data the reading time and the device acquisition
    time are the same, so ``source_creation_date_time`` is ``dt`` (unlike the
    wearable header, which stamps the observation's day)."""
    return {
        "header": {
            "uuid": str(uuid.uuid4()),
            "source_creation_date_time": _iso(dt),
            "schema_id": {"namespace": "omh", "name": "blood-glucose", "version": "4.0"},
            "modality": "sensed",
            "acquisition_provenance": {"source_name": CGM_SOURCE_NAME},
        },
        "body": {
            "blood_glucose": {"unit": "mg/dL", "value": value},
            "effective_time_frame": {"date_time": _iso(dt)},
        },
    }


class Command(BaseCommand):
    help = "Generate a synthetic demo cohort (CGM + Oura wearables) dated up to today."

    def handle(self, *args, **options):
        organization = Organization.objects.filter(name__icontains=ORG_NAME_FRAGMENT).first()
        if not organization:
            raise CommandError(f"Missing Organization containing '{ORG_NAME_FRAGMENT}' — run `seed` first.")

        demo_emails = [mp["email"] for mp in MOCK_PATIENTS]
        if JheUser.objects.filter(email__in=demo_emails).exists():
            raise CommandError(
                "Rich demo data already present. Re-run a clean rebuild with "
                "`python manage.py seed --flush-db --with-rich-demo`."
            )

        cgm_source, _ = DataSource.objects.get_or_create(name=CGM_DATA_SOURCE, defaults={"type": "personal_device"})
        wearable_source, _ = DataSource.objects.get_or_create(
            name=WEARABLE_DATA_SOURCE, defaults={"type": "personal_device"}
        )

        # Concepts are seeded centrally by `seed_codeable_concepts`; this command
        # always runs after the base seed (the Organization check above), so look
        # them up rather than re-creating with a divergent pattern.
        cgm_cc = CodeableConcept.objects.get(coding_code=CGM_CODE)
        wearable_cc = {code: CodeableConcept.objects.get(coding_code=code) for code, _system, _label in WEARABLE_SCOPES}

        study, _ = Study.objects.get_or_create(
            organization=organization,
            name=STUDY_NAME,
            defaults={"description": "Synthetic CGM + Oura wearable demo data.", "icon_url": None},
        )

        StudyDataSource.objects.get_or_create(study=study, data_source=cgm_source)
        StudyDataSource.objects.get_or_create(study=study, data_source=wearable_source)
        StudyScopeRequest.objects.get_or_create(study=study, scope_code=cgm_cc, defaults={"scope_actions": "rs"})
        for code in wearable_cc:
            StudyScopeRequest.objects.get_or_create(
                study=study, scope_code=wearable_cc[code], defaults={"scope_actions": "rs"}
            )

        # Anchor CGM and wearables to the same (local) clock so the latest CGM
        # reading and the wearable "today" land on the same calendar day.
        now = dj_timezone.localtime()
        today = now.date()
        total_cgm = 0
        total_wearable = 0

        for mp in MOCK_PATIENTS:
            with transaction.atomic():
                rng = random.Random(f"{SEED}:{mp['email']}")
                birth = datetime.strptime(mp["birth_date"], "%Y-%m-%d").date()
                age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
                risk = risk_score(age)

                user = JheUser.objects.create_user(
                    email=mp["email"],
                    password=get_random_string(16),
                    first_name=mp["name_given"],
                    last_name=mp["name_family"],
                    user_type="patient",
                )
                patient = user.patient_profile
                patient.birth_date = mp["birth_date"]
                patient.telecom_phone = mp["telecom_phone"]
                patient.save()
                patient.organizations.add(organization)
                PatientIdentifier.objects.get_or_create(patient=patient, system="demo", value=mp["email"])
                study_patient, _ = StudyPatient.objects.get_or_create(study=study, patient=patient)

                consent_time = now - timedelta(days=WEARABLE_MAX_DAYS + 3)
                for cc in [cgm_cc, *wearable_cc.values()]:
                    StudyPatientScopeConsent.objects.update_or_create(
                        study_patient=study_patient,
                        scope_code=cc,
                        defaults={"consented": True, "consented_time": consent_time, "scope_actions": "rs"},
                    )

                to_create = []

                cgm_start = now - timedelta(days=CGM_WINDOW_DAYS)
                steps = (CGM_WINDOW_DAYS * 24 * 60) // CGM_INTERVAL_MINUTES
                for i in range(steps + 1):
                    dt = cgm_start + timedelta(minutes=i * CGM_INTERVAL_MINUTES)
                    to_create.append(
                        Observation(
                            subject_patient=patient,
                            codeable_concept=cgm_cc,
                            data_source=cgm_source,
                            status="final",
                            omh_data=cgm_body(dt, cgm_value(dt, risk, rng)),
                        )
                    )
                total_cgm += steps + 1

                wearable_days = rng.randint(WEARABLE_MIN_DAYS, WEARABLE_MAX_DAYS)
                start_day = today - timedelta(days=wearable_days - 1)
                for offset in range(wearable_days):
                    day = start_day + timedelta(days=offset)
                    records = generate_wearable_day(day, offset, age, risk, rng)
                    for code, _system, _label in WEARABLE_SCOPES:
                        to_create.append(
                            Observation(
                                subject_patient=patient,
                                codeable_concept=wearable_cc[code],
                                data_source=wearable_source,
                                status="final",
                                omh_data=records[code],
                            )
                        )
                total_wearable += wearable_days * len(WEARABLE_SCOPES)

                # bulk_create skips Observation.clean(); schema conformance is
                # covered by the clean()-based tests in test_seed_rich_demo.py.
                Observation.objects.bulk_create(to_create, batch_size=2000)

        self.stdout.write(
            self.style.SUCCESS(
                f"Rich demo seeded: {len(MOCK_PATIENTS)} patients, "
                f"{total_cgm} CGM + {total_wearable} wearable observations."
            )
        )
