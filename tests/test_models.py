import numpy as np

from core.models import load_normalization_info, load_sensor_configs, resolve_worker_count

CONFIG_PATH = "config/sensors.yaml"


def test_load_sensor_configs_matches_verified_real_values():
    configs = load_sensor_configs(CONFIG_PATH)
    assert set(configs.keys()) == {"UHF", "AE"}

    uhf = configs["UHF"]
    assert uhf.fs_hz == 3.0e9
    assert uhf.n_samples == 3000
    assert uhf.freq_limit_hz == 100.0e6

    ae = configs["AE"]
    assert ae.fs_hz == 1.0e5
    assert ae.n_samples == 10000
    assert ae.freq_limit_hz == 50.0e3


def test_sensor_config_derived_properties():
    configs = load_sensor_configs(CONFIG_PATH)
    uhf, ae = configs["UHF"], configs["AE"]

    assert np.isclose(uhf.duration_s, 1e-6)      # ~1 us
    assert np.isclose(ae.duration_s, 0.1)        # ~100 ms
    assert np.isclose(uhf.freq_resolution_hz, 1e6)
    assert np.isclose(ae.freq_resolution_hz, 10.0)
    assert uhf.bytes_per_signal == 3000 * 4
    assert ae.bytes_per_signal == 10000 * 4


def test_sensor_config_time_axis_reconstructed_not_stored():
    configs = load_sensor_configs(CONFIG_PATH)
    uhf = configs["UHF"]
    axis = uhf.time_axis()
    assert axis.shape == (uhf.n_samples,)
    assert axis[0] == 0.0
    assert np.isclose(axis[1] - axis[0], uhf.dt_s)


def test_load_normalization_info():
    info = load_normalization_info(CONFIG_PATH)
    assert info.version == "v1_divide_by_vrange"


def test_resolve_worker_count_is_at_least_one():
    assert resolve_worker_count(CONFIG_PATH) >= 1
