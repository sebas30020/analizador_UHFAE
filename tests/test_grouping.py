import numpy as np
import pytest

from core.grouping import resolve_groups, resolve_groups_by_count, resolve_groups_by_time


def test_by_time_fixed_non_overlapping_windows():
    # 3 ventanas de 10s: [0,10) con t=0,5 ; [10,20) con t=12 ; [20,30) vacía -> se omite.
    ts = np.array([0.0, 5.0, 12.0])
    groups = resolve_groups_by_time(ts, delta_t=10.0)
    assert len(groups) == 2
    assert groups[0].start_idx == 0 and groups[0].end_idx == 2
    assert groups[1].start_idx == 2 and groups[1].end_idx == 3


def test_by_time_empty_windows_are_omitted_not_zero_valued():
    ts = np.array([0.0, 25.0])  # ventana [10,20) queda vacía entre medio
    groups = resolve_groups_by_time(ts, delta_t=10.0)
    # bins: 0 -> [0,10), 2 -> [20,30) ; bin 1 [10,20) no aparece
    bins_covered = {g.start_idx for g in groups}
    assert len(groups) == 2
    assert all(g.n_signals >= 1 for g in groups)


def test_by_time_last_group_marked_partial_if_window_exceeds_data():
    ts = np.array([0.0, 1.0, 2.0])  # todo cabe en los primeros 10s de una ventana de 100s
    groups = resolve_groups_by_time(ts, delta_t=100.0)
    assert len(groups) == 1
    assert groups[0].is_partial is True


def test_by_time_t_w_is_always_the_declared_duration_not_observed_span():
    # AUDITORIA_FORMULAS_PDF_vs_metricas.md §4: T_w nunca se infiere del span observado.
    ts = np.array([0.0, 0.1, 0.2])  # los 3 pulsos caen en los primeros 0.2s de una ventana de 60s
    groups = resolve_groups_by_time(ts, delta_t=60.0)
    assert len(groups) == 1
    assert groups[0].T_w == 60.0  # NO 0.2


def test_by_time_center_timestamp_is_window_midpoint():
    ts = np.array([1.0])
    groups = resolve_groups_by_time(ts, delta_t=10.0, t_start=0.0)
    assert groups[0].center_timestamp == 5.0  # (0 + 10)/2, no el timestamp del pulso


def test_by_time_rejects_non_positive_delta_t():
    with pytest.raises(ValueError):
        resolve_groups_by_time(np.array([0.0]), delta_t=0.0)


def test_by_count_fixed_blocks_of_k():
    ts = np.arange(10, dtype=np.float64)  # 0..9
    groups = resolve_groups_by_count(ts, k=3)
    assert len(groups) == 4  # 3+3+3+1
    assert [g.n_signals for g in groups] == [3, 3, 3, 1]
    assert groups[-1].is_partial is True
    assert all(not g.is_partial for g in groups[:-1])


def test_by_count_t_w_is_observed_span_of_the_group():
    ts = np.array([0.0, 1.0, 3.0, 10.0])
    groups = resolve_groups_by_count(ts, k=2)
    assert groups[0].T_w == 1.0   # 1.0 - 0.0
    assert groups[1].T_w == 7.0   # 10.0 - 3.0


def test_by_count_rejects_non_positive_k():
    with pytest.raises(ValueError):
        resolve_groups_by_count(np.array([0.0]), k=0)


def test_resolve_groups_dispatches_by_mode():
    ts = np.array([0.0, 1.0, 2.0])
    by_time = resolve_groups(ts, mode="by_time", value=10.0)
    by_count = resolve_groups(ts, mode="by_count", value=2)
    assert len(by_time) == 1
    assert len(by_count) == 2

    with pytest.raises(ValueError):
        resolve_groups(ts, mode="bogus", value=1.0)


def test_empty_timestamps_returns_no_groups():
    assert resolve_groups_by_time(np.array([]), delta_t=10.0) == []
    assert resolve_groups_by_count(np.array([]), k=5) == []
