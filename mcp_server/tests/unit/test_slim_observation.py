from jhe_mcp.fhir.models import Observation, SlimObservation, extract_value_unit


def test_extract_value_unit_nested_measure():
    body = {
        "blood_glucose": {"unit": "mg/dL", "value": 92},
        "effective_time_frame": {"date_time": "2026-04-15T08:00:00Z"},
    }
    assert extract_value_unit(body) == (92, "mg/dL")


def test_extract_value_unit_top_level():
    assert extract_value_unit({"value": 70, "unit": "beats/min"}) == (70, "beats/min")


def test_extract_value_unit_none_for_no_scalar():
    body = {"sleep_stage_summary": {"stage": "rem"}, "effective_time_frame": {}}
    assert extract_value_unit(body) == (None, None)


def test_extract_value_unit_none_for_multi_component_blood_pressure():
    # Blood pressure has two scalars; surfacing only the first (systolic) would
    # mislead, so the slim view returns (None, None) and steers to verbosity=full.
    body = {
        "systolic_blood_pressure": {"value": 120, "unit": "mmHg"},
        "diastolic_blood_pressure": {"value": 80, "unit": "mmHg"},
        "effective_time_frame": {"date_time": "2026-04-15T08:00:00Z"},
    }
    assert extract_value_unit(body) == (None, None)


def test_extract_value_unit_empty():
    assert extract_value_unit(None) == (None, None)
    assert extract_value_unit({}) == (None, None)


def test_slim_observation_from_observation_drops_body():
    obs = Observation(
        observation_id="o1",
        patient_id="7",
        code="omh:blood-glucose:4.0",
        code_display="Blood glucose",
        effective_at="2026-04-15T08:00:00Z",
        omh_body={"blood_glucose": {"value": 92, "unit": "mg/dL"}},
    )
    slim = SlimObservation.from_observation(obs)
    assert slim.observation_id == "o1"
    assert slim.patient_id == "7"
    assert slim.type == "Blood glucose"
    assert slim.effective_at == "2026-04-15T08:00:00Z"
    assert slim.value == 92
    assert slim.unit == "mg/dL"
    assert not hasattr(slim, "omh_body")
