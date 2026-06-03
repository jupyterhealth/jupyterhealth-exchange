import pytest
from jhe_mcp.auth import broker_state

KEY = "test-broker-key"


def test_round_trip():
    token = broker_state.encode(KEY, {"a": 1, "b": "x"})
    assert broker_state.decode(KEY, token, max_age=60) == {"a": 1, "b": "x"}


def test_wrong_key_rejected():
    token = broker_state.encode(KEY, {"a": 1})
    with pytest.raises(broker_state.StateError):
        broker_state.decode("other-key", token, max_age=60)


def test_tamper_rejected():
    token = broker_state.encode(KEY, {"a": 1})
    tampered = token[:-4] + ("AAAA" if token[-4:] != "AAAA" else "BBBB")
    with pytest.raises(broker_state.StateError):
        broker_state.decode(KEY, tampered, max_age=60)
