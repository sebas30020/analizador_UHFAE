import numpy as np

from ui.components.time_axis import elapsed_minutes_to_unix_seconds, to_elapsed_minutes


def test_to_elapsed_minutes_basic():
    t0 = 100.0
    timestamps = np.array([100.0, 160.0, 220.0])
    assert np.allclose(to_elapsed_minutes(timestamps, t0), [0.0, 1.0, 2.0])


def test_to_elapsed_minutes_empty_array():
    result = to_elapsed_minutes(np.array([]), t0=1_700_000_000.0)
    assert result.shape[0] == 0


def test_elapsed_minutes_to_unix_seconds_basic():
    assert elapsed_minutes_to_unix_seconds(2.0, t0=100.0) == 220.0


def test_elapsed_minutes_to_unix_seconds_zero_is_t0():
    assert elapsed_minutes_to_unix_seconds(0.0, t0=42.0) == 42.0


def test_roundtrip_minutes_to_seconds_and_back():
    t0 = 1_700_000_000.0
    unix = elapsed_minutes_to_unix_seconds(5.5, t0)
    assert np.isclose(unix, t0 + 330.0)
    assert np.isclose(to_elapsed_minutes(np.array([unix]), t0)[0], 5.5)
