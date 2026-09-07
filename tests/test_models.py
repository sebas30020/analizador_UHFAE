import numpy as np

from core.models import load_normalization_info, load_sensor_configs, resolve_worker_count

CONFIG_PATH = "config/sensors.yaml"


def test_load_sensor_configs_matches_verified_real_values():
    configs = load_sensor_configs(CONFIG_PATH)
    assert set(configs.keys()) == {"UHF", "AE", "UHF_KS"}

    uhf = configs["UHF"]
    assert uhf.fs_hz == 3.0e9
    assert uhf.n_samples == 3000
    assert uhf.freq_limit_hz == 100.0e6
    assert uhf.decimate_full_view is False
    assert uhf.has_trigger_metadata is True

    ae = configs["AE"]
    assert ae.fs_hz == 1.0e5
    assert ae.n_samples == 10000
    assert ae.freq_limit_hz == 50.0e3
    assert ae.decimate_full_view is True
    assert ae.has_trigger_metadata is True

    # UHF_KS (rama lectura_keysight): valores nominales -- el reader los sobreescribe
    # por archivo vía sensor_config_overrides() (ver tests/test_keysight_reader.py).
    uhf_ks = configs["UHF_KS"]
    assert uhf_ks.fs_hz == 2.0e10
    assert uhf_ks.n_samples == 20000
    assert uhf_ks.freq_limit_hz == 1.0e9
    assert uhf_ks.decimate_full_view is True
    assert uhf_ks.has_trigger_metadata is False


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


def test_signal_block_in_memory_backward_compatibility():
    from core.models import SignalBlock
    data = np.zeros((4, 10), dtype=np.float32)
    ts = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float64)
    trig = np.array([0.1, 0.1, 0.1, 0.1], dtype=np.float64)
    vr = np.array([1.0, 1.0, 1.0, 1.0], dtype=np.float64)
    vm = np.array([True, True, True, True], dtype=bool)
    mm = np.zeros((4, 2), dtype=np.float32)

    # Positional instantiation backward compatibility
    block_pos = SignalBlock(data, ts, trig, vr, vm, mm)
    assert block_pos.n_signals == 4
    assert block_pos.n_samples == 10
    assert len(block_pos) == 4
    assert block_pos.data.shape == (4, 10)
    assert np.allclose(block_pos.rows(1, 3), data[1:3])
    assert np.allclose(block_pos.row(2), data[2])
    assert np.allclose(block_pos.rows_by_indices(np.array([0, 3])), data[[0, 3]])


def test_signal_block_lazy_handle_and_proxy():
    from core.models import LazyDataProxy, SignalBlock
    raw = np.arange(50, dtype=np.float32).reshape(5, 10)
    ts = np.arange(5, dtype=np.float64)
    trig = np.zeros(5, dtype=np.float64)
    vr = np.ones(5, dtype=np.float64)
    vm = np.ones(5, dtype=bool)
    mm = np.zeros((5, 2), dtype=np.float32)

    lazy_block = SignalBlock(
        data=None,
        timestamps=ts,
        trigger=trig,
        vrange=vr,
        valid_mask=vm,
        minmax=mm,
        row_source=lambda start, stop: raw[start:stop],
        single_row_source=lambda idx: raw[idx],
        indices_source=lambda idxs: raw[idxs],
        n_samples=10,
    )

    assert lazy_block.n_signals == 5
    assert lazy_block.n_samples == 10
    assert len(lazy_block) == 5

    # Proxy inspection properties
    proxy = lazy_block.data
    assert isinstance(proxy, LazyDataProxy)
    assert proxy.shape == (5, 10)
    assert proxy.ndim == 2
    assert proxy.dtype == np.float32
    assert len(proxy) == 5

    # Proxy indexing and slicing
    assert np.allclose(proxy[0], raw[0])
    assert np.allclose(proxy[1:3], raw[1:3])
    assert np.allclose(np.asarray(proxy), raw)

    # Rows API
    assert np.allclose(lazy_block.rows(2, 4), raw[2:4])
    assert np.allclose(lazy_block.row(4), raw[4])
    assert np.allclose(lazy_block.rows_by_indices(np.array([1, 4])), raw[[1, 4]])


def test_signal_block_zero_copy_order_permutation():
    from core.models import SignalBlock
    # Data stored physically out of chronological order
    raw_physical = np.array([[10, 10], [0, 0], [20, 20]], dtype=np.float32)
    # Chronological sort order would be [1, 0, 2]
    order = np.array([1, 0, 2], dtype=np.int64)
    ts = np.array([1.0, 2.0, 3.0], dtype=np.float64)  # Already sorted
    trig = np.zeros(3, dtype=np.float64)
    vr = np.ones(3, dtype=np.float64)
    vm = np.ones(3, dtype=bool)
    mm = np.zeros((3, 2), dtype=np.float32)

    block = SignalBlock(
        data=raw_physical,
        timestamps=ts,
        trigger=trig,
        vrange=vr,
        valid_mask=vm,
        minmax=mm,
        order=order,
    )

    # Calling row(0) must return the first chronological signal (physical row 1: [0, 0])
    assert np.allclose(block.row(0), [0, 0])
    assert np.allclose(block.row(1), [10, 10])
    assert np.allclose(block.row(2), [20, 20])
    assert np.allclose(block.rows(0, 2), [[0, 0], [10, 10]])
    assert np.allclose(block.rows_by_indices(np.array([0, 2])), [[0, 0], [20, 20]])


def test_signal_block_validation_errors():
    import pytest
    from core.models import SignalBlock

    ts = np.array([1.0, 2.0])
    trig = np.array([0.0])  # length mismatch
    vr = np.array([1.0, 1.0])
    vm = np.array([True, True])
    mm = np.zeros((2, 2))

    with pytest.raises(ValueError, match="SignalBlock.trigger"):
        SignalBlock(timestamps=ts, trigger=trig, vrange=vr, valid_mask=vm, minmax=mm)

    with pytest.raises(ValueError, match="SignalBlock.minmax"):
        SignalBlock(timestamps=ts, trigger=np.zeros(2), vrange=vr, valid_mask=vm, minmax=np.zeros((2, 3)))

