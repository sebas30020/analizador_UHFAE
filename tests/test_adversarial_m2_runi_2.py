"""Empirical adversarial test suite for Milestone 2 (Invariante B3 & Empty Set UI).
Authored by challenger_m2_runi_2.

Adversarial Objectives:
1. Concurrency: Multi-threaded concurrent queries to _GROUP_MEMO (readers/writers, race conditions, clear).
2. Capacity: Push > 150 unique keys and verify size is strictly capped at 128 entries.
3. Invalidation: Verify filter_version increments isolate results and follow true LRU eviction.
4. Mutation / Immutability: Verify whether returned arrays can corrupt cached memo entries.
5. Empty Set UI stability:
   - graph_timeseries.py with 0 active signals: selection anchor trace persistence, modebar selectability contract, empty set annotation.
   - Fallback ladder when dataset has 0 valid signals or 0 total timestamps.
   - graph_metric.py with 0 points: graceful annotation and empty trace handling under various configurations.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time

import numpy as np
import pytest

from cache.backend import SqliteHdf5CacheBackend
from cache.service import (
    _GROUP_MEMO,
    _GROUP_MEMO_LOCK,
    _GROUP_MEMO_MAX_ENTRIES,
    clear_group_memo,
    get_or_compute_group_intrinsic,
    get_or_compute_group_reduction,
    get_or_compute_puntual,
)
from core.models import EnvironmentalSeries, EventSeries, SensorConfig, SignalBlock
from metrics.registry import discover_metrics
from ui.components.graph_metric import (
    EMPTY_ACTIVE_SET_MESSAGE as METRIC_EMPTY_MESSAGE,
    build_metric_figure,
)
from ui.components.graph_timeseries import (
    EMPTY_ACTIVE_SET_MESSAGE as TIMESERIES_EMPTY_MESSAGE,
    SELECTION_ANCHOR_NAME,
    build_timeseries_figure,
)
from viz.smoothing import SmoothingSpec


@pytest.fixture(autouse=True, scope="module")
def _ensure_registered():
    discover_metrics()


@pytest.fixture
def cache(tmp_path):
    backend = SqliteHdf5CacheBackend(tmp_path / "adversarial_cache")
    yield backend
    backend.close()


@pytest.fixture
def sensor_config() -> SensorConfig:
    return SensorConfig(
        name="UHF",
        hdf5_group="signals",
        fs_hz=1e9,
        n_samples=4,
        freq_limit_hz=1e8,
        axis_unit="us",
        axis_scale=1e6,
        target_block_bytes=1024,
    )


def _make_block(n: int = 10) -> SignalBlock:
    data = np.arange(n * 4, dtype=np.float32).reshape(n, 4)
    timestamps = np.linspace(1000.0, 2000.0, n, dtype=np.float64)
    trigger = np.full(n, 0.01)
    vrange = np.full(n, 1.0)
    valid_mask = np.full(n, True)
    minmax = np.stack([data.min(axis=1), data.max(axis=1)], axis=1).astype(np.float32)
    return SignalBlock(
        data=data,
        timestamps=timestamps,
        trigger=trigger,
        vrange=vrange,
        valid_mask=valid_mask,
        minmax=minmax,
    )


# =============================================================================
# 1. CONCURRENCY TESTS (cache/service.py LRU memo)
# =============================================================================

class TestGroupMemoConcurrency:
    """Stress tests verifying thread-safety and race immunity of _GROUP_MEMO."""

    def test_concurrent_readers_and_writers_high_contention(self, cache, sensor_config):
        clear_group_memo()
        block = _make_block(20)
        mask = np.ones(20, dtype=bool)
        mask[0] = False  # active filter -> bypass disk, use memo

        n_threads = 16
        n_queries_per_thread = 50
        errors: list[Exception] = []

        def worker(thread_id: int):
            try:
                for i in range(n_queries_per_thread):
                    # Half of queries use shared version, half use thread-private version
                    f_ver = 1 if (i % 2 == 0) else (100 + thread_id)
                    t, v, p = get_or_compute_group_reduction(
                        cache,
                        block,
                        sensor_config,
                        "ds_concurrent",
                        "vmax",
                        "by_count",
                        2,
                        reducer="median",
                        active_mask=mask,
                        filter_version=f_ver,
                    )
                    assert t.shape[0] > 0
                    assert v.shape[0] > 0
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=n_threads) as executor:
            futures = [executor.submit(worker, tid) for tid in range(n_threads)]
            for f in as_completed(futures):
                f.result()

        assert not errors, f"Encountered concurrency errors: {errors}"
        with _GROUP_MEMO_LOCK:
            assert len(_GROUP_MEMO) <= _GROUP_MEMO_MAX_ENTRIES

    def test_concurrent_writers_never_exceed_capacity_cap(self, cache, sensor_config):
        clear_group_memo()
        block = _make_block(10)
        mask = np.array([True, False] * 5)

        n_threads = 20
        keys_per_thread = 15  # total 300 unique keys, far exceeding 128 max capacity
        max_seen_capacity = 0
        lock = threading.Lock()
        errors: list[Exception] = []

        def worker(thread_id: int):
            nonlocal max_seen_capacity
            try:
                for k in range(keys_per_thread):
                    f_ver = thread_id * 1000 + k
                    get_or_compute_group_reduction(
                        cache,
                        block,
                        sensor_config,
                        "ds_capacity_stress",
                        "vmax",
                        "by_count",
                        2,
                        reducer="median",
                        active_mask=mask,
                        filter_version=f_ver,
                    )
                    with _GROUP_MEMO_LOCK:
                        cur_len = len(_GROUP_MEMO)
                    with lock:
                        if cur_len > max_seen_capacity:
                            max_seen_capacity = cur_len
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=n_threads) as executor:
            futures = [executor.submit(worker, tid) for tid in range(n_threads)]
            for f in as_completed(futures):
                f.result()

        assert not errors, f"Errors during concurrent write stress: {errors}"
        assert max_seen_capacity <= _GROUP_MEMO_MAX_ENTRIES, (
            f"Observed _GROUP_MEMO size {max_seen_capacity} exceeding limit {_GROUP_MEMO_MAX_ENTRIES}"
        )
        with _GROUP_MEMO_LOCK:
            assert len(_GROUP_MEMO) == _GROUP_MEMO_MAX_ENTRIES

    def test_concurrent_clear_during_active_reads_and_writes(self, cache, sensor_config):
        clear_group_memo()
        block = _make_block(10)
        mask = np.array([True, False] * 5)

        stop_event = threading.Event()
        errors: list[Exception] = []

        def reader_writer():
            try:
                ver = 1
                while not stop_event.is_set():
                    get_or_compute_group_reduction(
                        cache,
                        block,
                        sensor_config,
                        "ds_clear_race",
                        "vmax",
                        "by_count",
                        2,
                        active_mask=mask,
                        filter_version=ver,
                    )
                    ver = (ver % 50) + 1
            except Exception as e:
                errors.append(e)

        def clearer():
            try:
                while not stop_event.is_set():
                    clear_group_memo()
                    time.sleep(0.005)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader_writer) for _ in range(8)]
        threads.append(threading.Thread(target=clearer))

        for t in threads:
            t.start()
        time.sleep(0.3)
        stop_event.set()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent clear/read/write: {errors}"


# =============================================================================
# 2. CAPACITY & LRU EVICTION TESTS (cache/service.py)
# =============================================================================

class TestGroupMemoCapacityAndLRU:
    """Stress tests verifying size capping and strict LRU eviction mechanics."""

    def test_push_over_150_keys_strictly_capped_at_128(self, cache, sensor_config):
        clear_group_memo()
        block = _make_block(10)
        mask = np.array([True, False] * 5)

        # Push 200 unique filter versions
        for f_ver in range(1, 201):
            get_or_compute_group_reduction(
                cache,
                block,
                sensor_config,
                "ds_cap_150",
                "vmax",
                "by_count",
                2,
                active_mask=mask,
                filter_version=f_ver,
            )
            with _GROUP_MEMO_LOCK:
                assert len(_GROUP_MEMO) <= _GROUP_MEMO_MAX_ENTRIES

        with _GROUP_MEMO_LOCK:
            assert len(_GROUP_MEMO) == 128
            # The first 72 items (200 - 128 = 72) must have been evicted
            versions_in_memo = [key[1] for key in _GROUP_MEMO.keys()]
            assert versions_in_memo == list(range(73, 201))

    def test_lru_eviction_promotes_hit_keys(self, cache, sensor_config):
        clear_group_memo()
        block = _make_block(10)
        mask = np.array([True, False] * 5)

        # Fill memo exactly to capacity 128 with versions 1..128
        for f_ver in range(1, 129):
            get_or_compute_group_reduction(
                cache,
                block,
                sensor_config,
                "ds_lru",
                "vmax",
                "by_count",
                2,
                active_mask=mask,
                filter_version=f_ver,
            )

        with _GROUP_MEMO_LOCK:
            assert len(_GROUP_MEMO) == 128

        # Now touch key version 1 (it is currently the oldest / LRU candidate).
        # Touching it via cache hit must move it to the MRU position (end).
        get_or_compute_group_reduction(
            cache,
            block,
            sensor_config,
            "ds_lru",
            "vmax",
            "by_count",
            2,
            active_mask=mask,
            filter_version=1,
        )

        # Push a brand new key (version 129).
        # If LRU works, version 2 should be evicted (not version 1)!
        get_or_compute_group_reduction(
            cache,
            block,
            sensor_config,
            "ds_lru",
            "vmax",
            "by_count",
            2,
            active_mask=mask,
            filter_version=129,
        )

        with _GROUP_MEMO_LOCK:
            assert len(_GROUP_MEMO) == 128
            versions = [key[1] for key in _GROUP_MEMO.keys()]
            assert 1 in versions, "Key 1 was recently accessed and should NOT have been evicted!"
            assert 2 not in versions, "Key 2 was least recently used and SHOULD have been evicted!"
            assert 129 in versions, "Key 129 is newly inserted and should be present!"
            assert versions[-1] == 129, "Key 129 should be the newest (MRU)!"
            assert versions[-2] == 1, "Key 1 should be second to newest!"


# =============================================================================
# 3. INVALIDATION & ISOLATION TESTS (cache/service.py)
# =============================================================================

class TestGroupMemoInvalidationAndIsolation:
    """Stress tests verifying filter_version isolation, undo restoration, and mask handling."""

    def test_filter_version_invalidation_and_undo_reusability(self, cache, sensor_config):
        clear_group_memo()
        block = _make_block(10)
        mask_v1 = np.array([True, True, True, True, True, False, False, False, False, False])
        mask_v2 = np.array([True, False, True, False, True, False, True, False, True, False])

        # Step 1: Version 1 query
        t1, v1, _ = get_or_compute_group_reduction(
            cache, block, sensor_config, "ds_iso", "vmax", "by_count", 2,
            active_mask=mask_v1, filter_version=1,
        )

        # Step 2: Version 2 query with different mask (filter changed)
        t2, v2, _ = get_or_compute_group_reduction(
            cache, block, sensor_config, "ds_iso", "vmax", "by_count", 2,
            active_mask=mask_v2, filter_version=2,
        )
        assert not np.array_equal(v1, v2), "Different masks must produce different values!"

        with _GROUP_MEMO_LOCK:
            assert len(_GROUP_MEMO) == 2

        # Step 3: Mutate block in place to detect if subsequent call recalcs or uses memo
        block.data[:] = 99999.0

        # Step 4: Undo action brings back filter_version=1
        t1_undo, v1_undo, _ = get_or_compute_group_reduction(
            cache, block, sensor_config, "ds_iso", "vmax", "by_count", 2,
            active_mask=mask_v1, filter_version=1,
        )
        np.testing.assert_array_equal(v1, v1_undo)  # Served from memo, not recalculated

    def test_unfiltered_active_mask_does_not_pollute_group_memo(self, cache, sensor_config):
        clear_group_memo()
        block = _make_block(10)
        all_true = np.ones(10, dtype=bool)

        # All-true mask must use disk cache and NOT populate _GROUP_MEMO
        get_or_compute_group_reduction(
            cache, block, sensor_config, "ds_unfiltered", "vmax", "by_count", 2,
            active_mask=all_true, filter_version=1,
        )
        assert len(_GROUP_MEMO) == 0
        assert cache.stats()["total_entries"] == 1

        get_or_compute_group_intrinsic(
            cache, block, sensor_config, "ds_unfiltered", "tasa_pulsos", "by_time", 60.0,
            active_mask=all_true, filter_version=1,
        )
        assert len(_GROUP_MEMO) == 0
        assert cache.stats()["total_entries"] == 2

    def test_group_memo_hit_array_mutations_do_not_corrupt_memo(self, cache, sensor_config):
        clear_group_memo()
        block = _make_block(10)
        mask = np.array([True, False] * 5)

        # Initial call (miss)
        t1, v1, p1 = get_or_compute_group_reduction(
            cache, block, sensor_config, "ds_mut", "vmax", "by_count", 2,
            active_mask=mask, filter_version=42,
        )
        orig_val = float(v1[0])

        # Second call (hit)
        t2, v2, p2 = get_or_compute_group_reduction(
            cache, block, sensor_config, "ds_mut", "vmax", "by_count", 2,
            active_mask=mask, filter_version=42,
        )

        # Mutate array returned on hit
        v2[0] = -99999.0

        # Third call (hit) - verify memo is not corrupted
        t3, v3, p3 = get_or_compute_group_reduction(
            cache, block, sensor_config, "ds_mut", "vmax", "by_count", 2,
            active_mask=mask, filter_version=42,
        )
        assert float(v3[0]) == orig_val


# =============================================================================
# 4. EMPTY SET UI STABILITY TESTS (graph_timeseries.py)
# =============================================================================

class TestEmptyActiveSetTimeseriesUI:
    """Stress tests verifying graph_timeseries.py with 0 active signals."""

    def test_zero_active_signals_selection_anchor_trace_exists_and_preserves_modebar(self):
        n = 50
        block = _make_block(n)
        env = EnvironmentalSeries(
            timestamps=np.array([1000.0, 1060.0]),
            temperature=np.array([22.0, 23.0]),
            humidity=np.array([50.0, 52.0]),
        )
        events = EventSeries(
            timestamps=np.array([1050.0]),
            event_type=np.array(["DISCHARGE"], dtype=object),
        )
        t0 = float(block.timestamps[0])
        zero_active_mask = np.zeros(n, dtype=bool)

        fig = build_timeseries_figure(
            SensorConfig(name="UHF", hdf5_group="signals", fs_hz=1e9, n_samples=4, freq_limit_hz=1e8,
                         axis_unit="us", axis_scale=1e6, target_block_bytes=1024),
            block,
            env,
            events,
            t0,
            zero_active_mask,
        )

        # 1. No signal envelope trace should exist
        assert not any("envolvente" in (tr.name or "") for tr in fig.data)

        # 2. Selection anchor trace MUST exist and be the LAST trace
        assert len(fig.data) >= 1
        anchor = fig.data[-1]
        assert anchor.name == SELECTION_ANCHOR_NAME
        assert anchor.mode == "markers", "Anchor MUST have markers mode for Plotly isSelectable check"
        assert anchor.marker.opacity == 0, "Anchor must be invisible"
        assert anchor.marker.size == 1
        assert anchor.hoverinfo == "skip"
        assert anchor.showlegend is False

        # 3. Modebar selectability contract:
        # In Plotly.js (components/modebar/manage.js), isSelectable(trace) checks:
        # trace.mode && (trace.mode.indexOf('markers') !== -1 || trace.mode.indexOf('text') !== -1)
        # Verify our anchor trace satisfies this contract:
        assert "markers" in anchor.mode or "text" in anchor.mode

        # 4. User-friendly empty set message annotation is present
        assert any(TIMESERIES_EMPTY_MESSAGE in str(ann.text) for ann in fig.layout.annotations)

    def test_zero_active_signals_dataset_with_zero_valid_signals_fallback(self):
        n = 20
        block = _make_block(n)
        block.valid_mask[:] = False  # entire dataset is invalid
        zero_active = np.zeros(n, dtype=bool)
        t0 = float(block.timestamps[0])

        env = EnvironmentalSeries(np.array([]), np.array([]), np.array([]))
        events = EventSeries(np.array([]), np.array([], dtype=object))

        # Must not crash with IndexError or empty slice
        fig = build_timeseries_figure(
            SensorConfig(name="UHF", hdf5_group="signals", fs_hz=1e9, n_samples=4, freq_limit_hz=1e8,
                         axis_unit="us", axis_scale=1e6, target_block_bytes=1024),
            block, env, events, t0, zero_active,
        )

        assert len(fig.data) == 1
        anchor = fig.data[0]
        assert anchor.name == SELECTION_ANCHOR_NAME
        assert anchor.mode == "markers"
        assert len(anchor.x) == 1
        assert len(anchor.y) == 1

    def test_zero_active_signals_empty_block_fallback(self):
        # Pathological edge case: SignalBlock with 0 signals
        empty_data = np.zeros((0, 4), dtype=np.float32)
        empty_ts = np.zeros(0, dtype=np.float64)
        empty_trig = np.zeros(0, dtype=np.float64)
        empty_vr = np.zeros(0, dtype=np.float64)
        empty_valid = np.zeros(0, dtype=bool)
        empty_minmax = np.zeros((0, 2), dtype=np.float32)
        block = SignalBlock(empty_data, empty_ts, empty_trig, empty_vr, empty_valid, empty_minmax)

        env = EnvironmentalSeries(np.array([]), np.array([]), np.array([]))
        events = EventSeries(np.array([]), np.array([], dtype=object))

        fig = build_timeseries_figure(
            SensorConfig(name="UHF", hdf5_group="signals", fs_hz=1e9, n_samples=4, freq_limit_hz=1e8,
                         axis_unit="us", axis_scale=1e6, target_block_bytes=1024),
            block, env, events, 0.0, np.zeros(0, dtype=bool),
        )

        assert len(fig.data) == 1
        anchor = fig.data[0]
        assert anchor.name == SELECTION_ANCHOR_NAME
        assert anchor.mode == "markers"
        assert anchor.x[0] == 0.0
        assert anchor.y[0] == 0.0


# =============================================================================
# 5. EMPTY SET UI STABILITY TESTS (graph_metric.py)
# =============================================================================

class TestEmptyActiveSetMetricUI:
    """Stress tests verifying graph_metric.py with 0 points."""

    def test_zero_points_displays_empty_message_without_errors(self):
        t_empty = np.array([], dtype=np.float64)
        v_empty = np.array([], dtype=np.float64)
        events = EventSeries(np.array([]), np.array([], dtype=object))

        fig = build_metric_figure(t_empty, v_empty, events, 0.0, label="rms", unit="V")

        assert len(fig.data) == 0
        assert any(METRIC_EMPTY_MESSAGE in str(ann.text) for ann in fig.layout.annotations)

    def test_zero_points_with_all_decorations_enabled_does_not_crash(self):
        t_empty = np.array([], dtype=np.float64)
        v_empty = np.array([], dtype=np.float64)
        events = EventSeries(
            timestamps=np.array([100.0, 200.0]),
            event_type=np.array(["EVENT_A", "EVENT_B"], dtype=object),
        )

        # Pass smoothing, reference line, connect_points, events
        fig = build_metric_figure(
            t_empty,
            v_empty,
            events,
            0.0,
            label="kurtosis",
            unit="",
            show_events=True,
            connect_points=True,
            smoothing=SmoothingSpec(method="media_movil_temporal", window=5.0),
            reference_value=4.5,
            reference_message="Referencia fija",
        )

        # Data traces should still be 0 (no points)
        assert len(fig.data) == 0
        # Empty set annotation must be present
        assert any(METRIC_EMPTY_MESSAGE in str(ann.text) for ann in fig.layout.annotations)
        # Event shapes should coexist without error
        assert len(fig.layout.shapes) >= 2
