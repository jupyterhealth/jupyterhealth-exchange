"""How a boolean is read from the environment: `DEBUG=false` must mean false."""

import pytest

from jhe.settings import env_flag


@pytest.mark.parametrize("value", ["1", "true", "True", "YES", "on"])
def test_true_spellings(monkeypatch, value):
    monkeypatch.setenv("JHE_TEST_FLAG", value)
    assert env_flag("JHE_TEST_FLAG") is True


@pytest.mark.parametrize("value", ["0", "false", "False", "no", "off", "", "  "])
def test_false_spellings(monkeypatch, value):
    """`bool()` of any non-empty string is true; that is what this replaces."""
    monkeypatch.setenv("JHE_TEST_FLAG", value)
    assert env_flag("JHE_TEST_FLAG") is False


def test_unset(monkeypatch):
    monkeypatch.delenv("JHE_TEST_FLAG", raising=False)
    assert env_flag("JHE_TEST_FLAG") is False
