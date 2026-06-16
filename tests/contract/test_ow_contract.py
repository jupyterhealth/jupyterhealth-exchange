"""OW -> JHE consumer contract test.

Runs against a booted Open Wearables instance (URL + seeded user/key supplied
via env by the ow-contract workflow). Mirrors what
core/management/commands/ow_poll.py does: fetch heart-rate samples from OW and
convert each with omh-shim. A failure here means OW broke the contract JHE's
ingest relies on - do not adopt the upstream change.

Pure stdlib + requests + omh-shim; no Django. The workflow runs it with
--override-ini to disable JHE's Django pytest config.
"""

import os
from datetime import UTC, datetime

import requests
from omh_shim import convert

OW_URL = os.environ["OW_URL"].rstrip("/")
OW_USER_ID = os.environ["OW_USER_ID"]
OW_API_KEY = os.environ["OW_API_KEY"]

# Matches the value seeded by seed_ow.py.
EXPECTED_VALUE = 72.5


def _fetch_heart_rate():
    resp = requests.get(
        f"{OW_URL}/api/v1/users/{OW_USER_ID}/timeseries",
        params={
            "types": "heart_rate",
            "start_time": "2000-01-01T00:00:00Z",
            "end_time": "2100-01-01T00:00:00Z",
        },
        headers={"X-Open-Wearables-API-Key": OW_API_KEY},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    # ow_poll tolerates both {"data": [...]} and a bare list.
    return payload["data"] if isinstance(payload, dict) else payload


def test_timeseries_response_shape():
    samples = _fetch_heart_rate()
    assert samples, "OW returned no heart-rate samples for the seeded user"
    sample = samples[0]
    # Fields omh-shim's ow_normalized heart_rate converter depends on.
    assert sample["type"] == "heart_rate"
    assert "timestamp" in sample
    assert "value" in sample


def test_omh_shim_converts_ow_heart_rate():
    samples = _fetch_heart_rate()
    for sample in samples:
        omh = convert(source="ow_normalized", data_type="heart_rate", sample=sample)
        # Header schema_id is an object {namespace, name, version}.
        assert omh["header"]["schema_id"] == {
            "namespace": "omh",
            "name": "heart-rate",
            "version": "2.0",
        }
        heart_rate = omh["body"]["heart_rate"]
        assert heart_rate["unit"] == "beats/min"
        assert float(heart_rate["value"]) == EXPECTED_VALUE
        assert "effective_time_frame" in omh["body"]
        # convert() validated the body against the OMH schema (validate=True).
        # Timestamp must round-trip to a parseable datetime.
        datetime.fromisoformat(omh["body"]["effective_time_frame"]["date_time"].replace("Z", "+00:00")).astimezone(UTC)
