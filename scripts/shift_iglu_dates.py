"""Shift the Iglu CGM Test Data study's blood-glucose observations forward by a
single global delta so the newest reading lands at ANCHOR_MAX (recent), pulling
the ~3.4-year cohort span into roughly 2023..2026. Preserves all relative timing
and inter-patient staggering. Also shifts blood-glucose consents and deletes the
previously-generated wearable observations so they can be regenerated aligned to
the new CGM window.

Only touches the 19 iglu study patients (filtered by subject_patient_id) and only
deletes wearables this project created (filtered by acquisition_provenance.source_name),
leaving any unrelated blood-glucose / Oura data intact.

Run: python manage.py shell -c "exec(open('/tmp/shift_iglu_dates.py').read())"
"""

from datetime import date, timedelta

from django.db import connection, transaction

from core.models import CodeableConcept, Study, StudyPatient, StudyPatientScopeConsent

STUDY_NAME = "Iglu CGM Test Data"
ANCHOR_MAX = date(2026, 5, 29)  # newest CGM reading lands on this day
WEARABLE_SOURCE = "demo-synthea-omh-ieee-generator"

_FMT = 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'
_SHIFT_SQL = f"""
    UPDATE core_observation
    SET omh_data = jsonb_set(
        jsonb_set(
            omh_data,
            '{{header,source_creation_date_time}}',
            to_jsonb(to_char(
                ((omh_data #>> '{{header,source_creation_date_time}}')::timestamptz
                 + make_interval(days => %s)) AT TIME ZONE 'UTC', '{_FMT}'))
        ),
        '{{body,effective_time_frame,date_time}}',
        to_jsonb(to_char(
            ((omh_data #>> '{{body,effective_time_frame,date_time}}')::timestamptz
             + make_interval(days => %s)) AT TIME ZONE 'UTC', '{_FMT}'))
    )
    WHERE codeable_concept_id = %s AND subject_patient_id = ANY(%s)
"""

_SPAN_SQL = """
    SELECT min((omh_data #>> '{header,source_creation_date_time}')::timestamptz)::date,
           max((omh_data #>> '{header,source_creation_date_time}')::timestamptz)::date
    FROM core_observation
    WHERE codeable_concept_id = %s AND subject_patient_id = ANY(%s)
"""


def main():
    study = Study.objects.filter(name=STUDY_NAME).first()
    if not study:
        print(f"ERROR: study '{STUDY_NAME}' not found.")
        return
    pids = list(StudyPatient.objects.filter(study=study).values_list("patient_id", flat=True))
    bg = CodeableConcept.objects.get(coding_code="omh:blood-glucose:4.0")

    with connection.cursor() as cur:
        cur.execute(_SPAN_SQL, [bg.id, pids])
        gmin, gmax = cur.fetchone()
    delta = (ANCHOR_MAX - gmax).days
    print(f"before:  {gmin} -> {gmax}   (delta {delta} days, ~{delta / 365.25:.1f} yr)")

    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(_SHIFT_SQL, [delta, delta, bg.id, pids])
            print(f"cgm observations shifted: {cur.rowcount}")
        shifted_consents = 0
        for spc in StudyPatientScopeConsent.objects.filter(study_patient__study=study, scope_code=bg):
            if spc.consented_time:
                spc.consented_time = spc.consented_time + timedelta(days=delta)
                spc.save(update_fields=["consented_time"])
                shifted_consents += 1
        print(f"blood-glucose consents shifted: {shifted_consents}")
        with connection.cursor() as cur:
            cur.execute(
                "DELETE FROM core_observation WHERE (omh_data #>> '{header,acquisition_provenance,source_name}') = %s",
                [WEARABLE_SOURCE],
            )
            print(f"old wearable observations deleted: {cur.rowcount}")

    with connection.cursor() as cur:
        cur.execute(_SPAN_SQL, [bg.id, pids])
        nmin, nmax = cur.fetchone()
    print(f"after:   {nmin} -> {nmax}")


main()
