from __future__ import annotations

import numpy as np
import pytest

from core.metric_filters import (
    MetricCondition,
    combine_conditions,
    combine_masks,
    condition_key,
    detect_contradictions,
    evaluate_condition,
    normalize_threshold,
    parse_condition_key,
    scatter_to_full,
)


def test_metric_condition_properties_and_hashability():
    c1 = MetricCondition(metric_name="vpp", operator=">=", threshold=0.5)
    assert c1.metric_name == "vpp"
    assert c1.metric_id == "vpp"
    assert c1.operator == ">="
    assert c1.threshold == 0.5

    c2 = MetricCondition(metric_id="vpp", operator=">=", threshold=0.5)
    assert c2.metric_name == "vpp"
    assert c2.metric_id == "vpp"
    assert c1 == c2
    assert hash(c1) == hash(c2)

    s = {c1, c2}
    assert len(s) == 1


def test_condition_key_and_parsing():
    c = MetricCondition("feq", "<=", 3.5e8)
    key = condition_key(c)
    assert key == "feq|<=|350000000.0"

    parsed = parse_condition_key(key)
    assert parsed is not None
    assert parsed.metric_name == "feq"
    assert parsed.operator == "<="
    assert parsed.threshold == 3.5e8

    c_params = MetricCondition("vpp", ">=", 0.2, params=(("filter", "lowpass"),))
    key_params = condition_key(c_params)
    assert "filter=lowpass" in key_params
    parsed_params = parse_condition_key(key_params)
    assert parsed_params is not None
    assert parsed_params.metric_name == "vpp"
    assert parsed_params.operator == ">="
    assert parsed_params.threshold == 0.2
    assert parsed_params.params == (("filter", "lowpass"),)


def test_evaluate_condition_basic():
    values = np.array([0.1, 0.5, 0.8, 1.2])
    c_ge = MetricCondition("vpp", ">=", 0.5)
    mask_ge = evaluate_condition(c_ge, values)
    assert np.array_equal(mask_ge, np.array([False, True, True, True]))

    # Inverse argument order
    mask_ge_inv = evaluate_condition(values, c_ge)
    assert np.array_equal(mask_ge, mask_ge_inv)

    c_le = MetricCondition("vpp", "<=", 0.5)
    mask_le = evaluate_condition(c_le, values)
    assert np.array_equal(mask_le, np.array([True, True, False, False]))


def test_evaluate_condition_non_finite_values_are_false():
    values = np.array([np.nan, 0.5, np.inf, -np.inf, 1.0])
    c_ge = MetricCondition("vpp", ">=", 0.5)
    mask_ge = evaluate_condition(c_ge, values)
    # NaN, Inf, -Inf must be False
    assert np.array_equal(mask_ge, np.array([False, True, False, False, True]))

    c_le = MetricCondition("vpp", "<=", 0.5)
    mask_le = evaluate_condition(c_le, values)
    assert np.array_equal(mask_le, np.array([False, True, False, False, False]))


def test_scatter_to_full():
    partial = np.array([True, False, True])
    valid_idx = np.array([1, 3, 4])
    n_total = 6
    full = scatter_to_full(partial, valid_idx, n_total)
    expected = np.array([False, True, False, False, True, False])
    assert np.array_equal(full, expected)

    # Empty valid_idx
    empty_full = scatter_to_full(np.array([], dtype=bool), np.array([], dtype=int), 4)
    assert np.array_equal(empty_full, np.zeros(4, dtype=bool))


def test_combine_conditions_and_masks():
    m1 = np.array([True, True, False, True])
    m2 = np.array([True, False, True, True])
    combined = combine_conditions([m1, m2], 4)
    assert np.array_equal(combined, np.array([True, False, False, True]))

    # Empty list returns all True
    empty_combined = combine_conditions([], 4)
    assert np.array_equal(empty_combined, np.ones(4, dtype=bool))
    assert np.array_equal(combine_masks([m1, m2], 4), combined)


def test_normalize_threshold():
    assert normalize_threshold(10) == 10.0
    assert normalize_threshold(3.14) == 3.14
    assert normalize_threshold("42.5") == 42.5
    assert normalize_threshold(" 42,5 ") == 42.5
    assert normalize_threshold(None) is None
    assert normalize_threshold("") is None
    assert normalize_threshold("invalid") is None
    assert normalize_threshold(float("nan")) is None
    assert normalize_threshold(float("inf")) is None


def test_detect_contradictions():
    c1 = MetricCondition("vpp", ">=", 10.0)
    c2 = MetricCondition("vpp", "<=", 5.0)
    warnings = detect_contradictions([c1, c2])
    assert len(warnings) == 1
    assert "Rango vacío" in warnings[0]
    assert "vpp" in warnings[0]

    # Non-contradicting range
    c3 = MetricCondition("vpp", ">=", 5.0)
    c4 = MetricCondition("vpp", "<=", 10.0)
    assert len(detect_contradictions([c3, c4])) == 0

    # Equal bounds (e.g. >= 5 and <= 5) is a valid single point, not contradiction
    c5 = MetricCondition("vpp", ">=", 5.0)
    c6 = MetricCondition("vpp", "<=", 5.0)
    assert len(detect_contradictions([c5, c6])) == 0

    # Contradiction with multiple conditions picking most restrictive
    c7 = MetricCondition("feq", ">=", 100.0)
    c8 = MetricCondition("feq", ">=", 200.0)  # more restrictive min = 200
    c9 = MetricCondition("feq", "<=", 150.0)  # max = 150 -> 200 > 150
    w_feq = detect_contradictions([c7, c8, c9])
    assert len(w_feq) == 1
    assert "feq" in w_feq[0]


def test_detect_contradictions_tuple_interface():
    c1 = MetricCondition("vpp", ">=", 10.0)
    c2 = MetricCondition("vpp", "<=", 5.0)
    warnings = detect_contradictions([c1, c2])
    assert len(warnings) == 1
    assert isinstance(warnings[0], tuple)
    cond_ge, cond_le = warnings[0]
    assert cond_ge == c1
    assert cond_le == c2
    assert "vpp" in str(warnings[0])
    assert "Rango vacío" in warnings[0]


def test_normalize_threshold_two_args():
    assert normalize_threshold("vpp", 10) == 10.0
    assert normalize_threshold("vpp", "42.5") == 42.5
    assert normalize_threshold("vpp", None) is None
    assert normalize_threshold("vpp", "invalid") is None


def test_combine_conditions_without_n_total():
    m1 = np.array([True, True, False, True])
    m2 = np.array([True, False, True, True])
    res = combine_conditions([m1, m2])
    assert np.array_equal(res, np.array([True, False, False, True]))
    assert len(combine_conditions([])) == 0


def test_build_metric_filter_badges():
    from ui.components.control_panel import build_metric_filter_badges
    c = MetricCondition("vpp", ">=", 0.5)
    badges = build_metric_filter_badges([c])
    assert len(badges) == 1
    badge = badges[0]
    button = badge.children[1]
    assert button.id == {"type": "btn-remove-metric-filter", "index": condition_key(c)}


def test_app_state_metric_filter_lifecycle(tmp_path):
    from core.models import EnvironmentalSeries, EventSeries, SensorConfig, SignalBlock
    from ui.state import AppState, LoadedDataset

    cfg = SensorConfig(
        name="UHF", hdf5_group="signals", fs_hz=1e9, n_samples=8, freq_limit_hz=5e8,
        axis_unit="us", axis_scale=1e6, target_block_bytes=1 << 20,
    )
    n = 6
    data = np.zeros((n, 8), dtype=np.float32)
    data[:3, 0] = -0.05
    data[:3, 1] = 0.05
    data[3:, 0] = -1.0
    data[3:, 1] = 1.0
    minmax = np.zeros((n, 2), dtype=np.float64)
    minmax[:3, 0] = -0.05
    minmax[:3, 1] = 0.05
    minmax[3:, 0] = -1.0
    minmax[3:, 1] = 1.0

    block = SignalBlock(
        data=data,
        timestamps=np.linspace(100.0, 160.0, n),
        trigger=np.full(n, 0.02),
        vrange=np.full(n, 0.5),
        minmax=minmax,
        valid_mask=np.ones(n, dtype=bool),
    )
    env = EnvironmentalSeries(timestamps=np.array([]), temperature=np.array([]), humidity=np.array([]))
    events = EventSeries(timestamps=np.array([]), event_type=np.array([], dtype=object))

    dataset = LoadedDataset(
        dataset_id="test_ds_filter",
        source_path=tmp_path / "test.h5",
        experiment="exp1",
        sensor_configs={"UHF": cfg},
        blocks={"UHF": block},
        environmental=env,
        events=events,
        t0=100.0,
    )

    state = AppState(cache_dir=tmp_path / "cache", warmup_on_load=False)
    state.publish_dataset(dataset)

    assert state.get_filter_version() == 0
    assert np.all(state.get_active_mask("UHF"))

    # Add metric filter vpp >= 0.5 -> rows 3, 4, 5 should remain active
    c_vpp = MetricCondition("vpp", ">=", 0.5)
    res = state.add_metric_filter("UHF", c_vpp)
    assert res.error is None
    assert res.active_count == 3
    assert res.total_count == 6
    assert res.filter_version == 1
    assert state.get_filter_version() == 1
    mask1 = state.get_active_mask("UHF")
    assert np.array_equal(mask1, np.array([False, False, False, True, True, True]))
    assert len(state.get_metric_filters("UHF")) == 1

    # Adding duplicate is no-op
    res_dup = state.add_metric_filter("UHF", c_vpp)
    assert res_dup.filter_version == 1
    assert state.get_filter_version() == 1

    # Apply manual filter on index 3 -> active should become False at index 3
    v_manual = state.apply_filter("UHF", np.array([3]))
    assert v_manual == 2
    mask2 = state.get_active_mask("UHF")
    assert np.array_equal(mask2, np.array([False, False, False, False, True, True]))

    # Undo manual filter -> restores index 3
    v_undo = state.undo_filter("UHF")
    assert v_undo == 3
    mask_undo = state.get_active_mask("UHF")
    assert np.array_equal(mask_undo, mask1)

    # Redo manual filter -> re-excludes index 3
    v_redo = state.redo_filter("UHF")
    assert v_redo == 4
    mask_redo = state.get_active_mask("UHF")
    assert np.array_equal(mask_redo, mask2)

    # Remove metric filter -> manual filter remains (index 3 excluded, others active)
    k_vpp = condition_key(c_vpp)
    res_rem = state.remove_metric_filter("UHF", k_vpp)
    assert res_rem.filter_version == 5
    mask_rem = state.get_active_mask("UHF")
    assert np.array_equal(mask_rem, np.array([True, True, True, False, True, True]))
    assert len(state.get_metric_filters("UHF")) == 0

    # Undo removal -> restores metric filter
    v_undo2 = state.undo_filter("UHF")
    assert v_undo2 == 6
    assert np.array_equal(state.get_active_mask("UHF"), mask2)
    assert len(state.get_metric_filters("UHF")) == 1

    # Clear metric filters
    res_clear = state.clear_metric_filters("UHF")
    assert res_clear.filter_version == 7
    assert len(state.get_metric_filters("UHF")) == 0
    assert np.array_equal(state.get_active_mask("UHF"), np.array([True, True, True, False, True, True]))

    # Reset all filters
    v_reset = state.reset_filters("UHF")
    assert v_reset == 8
    assert np.all(state.get_active_mask("UHF"))
    assert len(state.get_metric_filters("UHF")) == 0
    assert not state.can_undo("UHF")
    assert not state.can_redo("UHF")
