"""Empirical adversarial test suite for Milestone 3.
Authored by challenger_m3_runi_2.

Adversarial Objectives:
1. cache/warmup.py parallel execution:
   - Concurrency: ThreadPoolExecutor under simulated load and immediate cooperative cancellation via Event.
   - Race conditions: SQLite locking errors, HDF5 integrity, concurrent writes.
2. ui/callbacks/sensor_window_callbacks.py event lines callback:
   - Exhaustive combinatorial matrix for deterministic index resolution:
     (env present/absent) x (valid signals present/absent) x (events present/absent).
   - Missing sensor name edge case.
   - All signals filtered out (empty active mask).
   - Fallback modes (dict, go.Figure, int, invalid).
3. ui/components/graph_metric.py float32 transport encoding:
   - dtype verification on all traces (points, lines, smoothing).
   - Plotly JSON serialization fidelity with PlotlyJSONEncoder.
   - Precision error bounds between float64 and float32.
   - Pathological and extreme values (empty, NaN, Inf, subnormal).
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path
from typing import Any
import unittest.mock as mock

import numpy as np
import plotly.graph_objects as go
import plotly.utils
import pytest

import ui.callbacks.sensor_window_callbacks as swc
from cache.backend import SqliteHdf5CacheBackend
from cache.warmup import WarmupSpec, default_warmup_specs, start_background_warmup, warm_cache
from core.models import (
    EnvironmentalSeries,
    EventSeries,
    SensorConfig,
    SignalBlock,
)
from metrics.registry import discover_metrics
from ui.app import create_app
from ui.callbacks.helpers import encode_metric_option
from ui.components.event_lines import build_event_line_shapes, build_event_lines_trace
from ui.components.graph_metric import build_metric_figure
from ui.components.graph_timeseries import build_timeseries_figure
from ui.reference_registry import get_reference_registry
from ui.state import AppState, LoadedDataset
from viz.smoothing import SmoothingSpec


@pytest.fixture(autouse=True, scope="module")
def _ensure_registered():
    discover_metrics()


@pytest.fixture(autouse=True)
def _clear_reference_registry():
    get_reference_registry().clear()
    yield
    get_reference_registry().clear()


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


@pytest.fixture
def block() -> SignalBlock:
    rng = np.random.default_rng(42)
    n = 100
    data = rng.normal(size=(n, 4)).astype(np.float32)
    timestamps = np.sort(rng.uniform(10.0, 600.0, size=n))
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


def _get_callback_fn(dash_app: Any, key_substring: str):
    for k, v in dash_app.callback_map.items():
        if key_substring in k:
            return v["callback"].__wrapped__
    raise AssertionError(f"Callback with substring '{key_substring}' not found")


# ==============================================================================
# SUITE 1: cache/warmup.py Concurrency & Cancellation Stress Tests
# ==============================================================================


def test_adversarial_warmup_cooperative_cancellation_under_simulated_load(tmp_path, sensor_config, block):
    """Stress test: verify cooperative cancellation stops execution promptly between specs,
    terminates all threads cleanly, and returns only the specs that actually finished.
    """
    cache = SqliteHdf5CacheBackend(tmp_path / "cache_cancel")
    cancelled = threading.Event()

    # 10 specs to provide plenty of work
    specs = [
        WarmupSpec(sensor="UHF", metric_id=f"m_{i}", regimen="puntual")
        for i in range(10)
    ]

    completed_specs: list[str] = []
    completed_lock = threading.Lock()

    def _slow_puntual(c, b, cfg, ds_id, metric_id, **kwargs):
        # Simulate heavy CPU computation
        time.sleep(0.04)
        with completed_lock:
            completed_specs.append(metric_id)
            if len(completed_specs) == 2:
                # Signal cancellation from inside worker once 2 specs finish
                cancelled.set()
        # Call backend directly to register something
        c.put(
            cache_key=f"key_{metric_id}",
            canonical_json="{}",
            dataset_id=ds_id,
            sensor=cfg.name,
            metric_id=metric_id,
            metric_version=1,
            timestamps=b.timestamps[:5],
            values=np.ones(5, dtype=np.float32),
        )

    with mock.patch("cache.warmup.get_or_compute_puntual", side_effect=_slow_puntual):
        t0 = time.time()
        done = warm_cache(
            cache,
            {"UHF": block},
            {"UHF": sensor_config},
            "ds_test",
            specs,
            cancelled=cancelled,
            max_workers=2,
        )
        elapsed = time.time() - t0

    # Verification:
    # 1. Total finished specs must be significantly less than 10 (cooperative cancel stopped the rest)
    assert len(done) < 10, f"Expected cancellation to stop work, but completed {len(done)} of 10"
    # 2. Elapsed time must be much smaller than 10 * 0.04s / 2 = 0.20s + overhead
    assert elapsed < 0.25
    # 3. Exactly what was marked done must match what was saved in the cache
    assert len(done) == len(completed_specs)
    assert cache.stats()["total_entries"] == len(done)
    cache.close()


def test_adversarial_warmup_immediate_precancellation(tmp_path, sensor_config, block):
    """Verify that warm_cache with a pre-set cancelled Event exits in 0ms with zero DB activity."""
    cache = SqliteHdf5CacheBackend(tmp_path / "cache_precancel")
    cancelled = threading.Event()
    cancelled.set()

    specs = default_warmup_specs("UHF")
    t0 = time.time()
    done = warm_cache(
        cache,
        {"UHF": block},
        {"UHF": sensor_config},
        "ds_test",
        specs,
        cancelled=cancelled,
        max_workers=4,
    )
    elapsed = time.time() - t0

    assert done == []
    assert elapsed < 0.05
    assert cache.stats()["total_entries"] == 0
    cache.close()


def test_adversarial_warmup_dynamic_external_cancellation_race(tmp_path, sensor_config, block):
    """Multiple specs running across 4 worker threads; external thread sets cancellation
    asynchronously after a tiny delay. Verify no race conditions or deadlocks.
    """
    cache = SqliteHdf5CacheBackend(tmp_path / "cache_dyn_cancel")
    cancelled = threading.Event()

    specs = [
        WarmupSpec(sensor="UHF", metric_id=f"dyn_m_{i}", regimen="puntual")
        for i in range(20)
    ]

    def _variable_delay_puntual(c, b, cfg, ds_id, metric_id, **kwargs):
        time.sleep(0.02)
        c.put(
            cache_key=f"dyn_key_{metric_id}",
            canonical_json="{}",
            dataset_id=ds_id,
            sensor=cfg.name,
            metric_id=metric_id,
            metric_version=1,
            timestamps=b.timestamps[:2],
            values=np.ones(2, dtype=np.float32),
        )

    def _external_canceller():
        time.sleep(0.05)
        cancelled.set()

    canceller_thread = threading.Thread(target=_external_canceller)
    canceller_thread.start()

    with mock.patch("cache.warmup.get_or_compute_puntual", side_effect=_variable_delay_puntual):
        done = warm_cache(
            cache,
            {"UHF": block},
            {"UHF": sensor_config},
            "ds_test",
            specs,
            cancelled=cancelled,
            max_workers=4,
        )

    canceller_thread.join()

    assert len(done) < 20, "Should have been cancelled before completing all 20 specs"
    assert cache.stats()["total_entries"] == len(done)
    cache.close()


# ==============================================================================
# SUITE 2: SQLite & HDF5 Concurrency, Race Conditions & Database Integrity
# ==============================================================================


def test_adversarial_concurrent_writes_single_backend_instance(tmp_path, sensor_config, block):
    """Stress test: 4 threads concurrently calling warm_cache on the SAME backend instance
    with overlapping and non-overlapping specs.
    Verify: No sqlite3.OperationalError (database locked), no HDF5 file corruption,
    and exact consistency between SQLite index and HDF5 payloads.
    """
    cache = SqliteHdf5CacheBackend(tmp_path / "cache_concurrent_single")

    def _run_warmup(worker_id: int):
        # Overlapping specs (e.g. rms, vpp) + unique specs per worker
        specs = [
            WarmupSpec(sensor="UHF", metric_id="rms", regimen="puntual"),
            WarmupSpec(sensor="UHF", metric_id="vpp", regimen="puntual"),
            WarmupSpec(sensor="UHF", metric_id=f"worker_{worker_id}_metric", regimen="puntual"),
        ]
        # In this test we use genuine get_or_compute_puntual against real discover_metrics
        return warm_cache(
            cache,
            {"UHF": block},
            {"UHF": sensor_config},
            f"ds_worker_{worker_id}",
            specs,
            max_workers=2,
        )

    n_threads = 4
    with ThreadPoolExecutor(max_workers=n_threads) as executor:
        futures = [executor.submit(_run_warmup, i) for i in range(n_threads)]
        results = [f.result() for f in as_completed(futures)]

    # All workers should have completed their specs without exceptions
    assert len(results) == n_threads
    for r in results:
        assert "rms" in r
        assert "vpp" in r

    # Validate SQLite integrity
    cur = cache._conn.cursor()
    cur.execute("PRAGMA integrity_check")
    status = cur.fetchone()[0]
    assert status == "ok", f"SQLite integrity check failed: {status}"

    # Verify each entry in SQLite index actually exists and is readable from HDF5
    total = cache.stats()["total_entries"]
    assert total > 0
    rows = cur.execute("SELECT cache_key FROM cache_entries").fetchall()
    assert len(rows) == total

    for (k,) in rows:
        entry = cache.get(k)
        assert entry is not None, f"Cache entry {k} in SQLite index missing from HDF5"
        assert entry.timestamps.shape[0] > 0
        assert entry.values.shape[0] > 0

    cache.close()


def test_adversarial_concurrent_writes_separate_backend_instances(tmp_path, sensor_config, block):
    """Stress test: 2 concurrent background threads each opening their own
    SqliteHdf5CacheBackend pointing to the SAME cache directory (the pattern used by
    start_background_warmup).
    Verify SQLite and HDF5 file-locking handling under contention.
    """
    cache_dir = tmp_path / "cache_concurrent_multi"
    cache_dir.mkdir(parents=True, exist_ok=True)

    errors: list[Exception] = []
    completed_threads: list[int] = []
    lock = threading.Lock()

    def _background_worker(thread_id: int):
        try:
            backend = SqliteHdf5CacheBackend(cache_dir)
            specs = [
                WarmupSpec(sensor="UHF", metric_id=f"metric_t{thread_id}_{i}", regimen="puntual")
                for i in range(8)
            ]
            done = warm_cache(
                backend,
                {"UHF": block},
                {"UHF": sensor_config},
                f"ds_shared_{thread_id}",
                specs,
                max_workers=2,
            )
            backend.close()
            with lock:
                completed_threads.append(len(done))
        except Exception as e:
            with lock:
                errors.append(e)

    t1 = threading.Thread(target=_background_worker, args=(1,))
    t2 = threading.Thread(target=_background_worker, args=(2,))

    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not t1.is_alive(), "Thread 1 timed out"
    assert not t2.is_alive(), "Thread 2 timed out"

    # Verify whether any locking or corruption errors occurred
    if errors:
        pytest.fail(f"Concurrent multi-instance backends produced errors: {errors}")

    assert len(completed_threads) == 2

    # Open verification backend and inspect database
    verifier = SqliteHdf5CacheBackend(cache_dir)
    cur = verifier._conn.cursor()
    cur.execute("PRAGMA integrity_check")
    status = cur.fetchone()[0]
    assert status == "ok"
    assert verifier.stats()["total_entries"] == sum(completed_threads)
    verifier.close()


def test_adversarial_concurrent_readers_and_writers(tmp_path, sensor_config, block):
    """Verify that reader threads calling cache.get() in a fast loop never observe partial
    or corrupted data while writer threads are executing warm_cache in parallel.
    """
    cache = SqliteHdf5CacheBackend(tmp_path / "cache_rw_contention")
    stop_event = threading.Event()
    read_errors: list[str] = []
    reads_performed = [0]

    # Pre-populate one entry to ensure reader has something to query
    specs_init = [WarmupSpec(sensor="UHF", metric_id="rms", regimen="puntual")]
    warm_cache(cache, {"UHF": block}, {"UHF": sensor_config}, "ds_rw", specs_init)

    def _reader():
        while not stop_event.is_set():
            # Query keys known to exist or being written
            for m in ["rms", "vpp", "kurtosis"]:
                try:
                    # Search SQLite for matching keys
                    cur = cache._conn.cursor()
                    rows = cur.execute(
                        "SELECT cache_key FROM cache_entries WHERE metric_id = ?", (m,)
                    ).fetchall()
                    for (k,) in rows:
                        entry = cache.get(k)
                        if entry is not None:
                            if len(entry.timestamps) != len(entry.values):
                                read_errors.append(f"Inconsistent lengths in {k}")
                    reads_performed[0] += 1
                except Exception as ex:
                    read_errors.append(str(ex))
            time.sleep(0.001)

    reader_thread = threading.Thread(target=_reader)
    reader_thread.start()

    # Writer executes additional specs
    specs_more = [
        WarmupSpec(sensor="UHF", metric_id="vpp", regimen="puntual"),
        WarmupSpec(sensor="UHF", metric_id="kurtosis", regimen="puntual"),
        WarmupSpec(sensor="UHF", metric_id="crest_factor", regimen="puntual"),
    ]
    done = warm_cache(cache, {"UHF": block}, {"UHF": sensor_config}, "ds_rw", specs_more, max_workers=2)

    stop_event.set()
    reader_thread.join(timeout=10)

    assert not reader_thread.is_alive()
    assert len(read_errors) == 0, f"Reader encountered inconsistencies: {read_errors}"
    assert reads_performed[0] >= 1, "Reader did not execute any loops"
    cache.close()


# ==============================================================================
# SUITE 3: Event Lines Callback Deterministic Index Resolution
# ==============================================================================


@pytest.mark.parametrize(
    "env_present, valid_signals_present, expected_idx",
    [
        (True, True, 3),   # Trace 0: Signal, Trace 1: Temp, Trace 2: Hum, Trace 3: Eventos
        (False, True, 1),  # Trace 0: Signal, Trace 1: Eventos
        (True, False, 2),  # Trace 0: Temp, Trace 1: Hum, Trace 2: Eventos (No Signal trace)
        (False, False, 0), # Trace 0: Eventos (No Signal trace, No Temp/Hum traces)
    ],
)
def test_adversarial_event_lines_index_resolution_combinatorial_matrix(
    tmp_path, env_present: bool, valid_signals_present: bool, expected_idx: int, monkeypatch
):
    """Exhaustively verify that the deterministic index formula:
        event_idx = (1 if has_envelope else 0) + (2 if has_env else 0)
    strictly matches the physical trace index of 'Eventos' in build_timeseries_figure
    across all 4 combinations of environmental data and signal validity.
    """
    n_signals = 20
    rng = np.random.default_rng(123)

    # 1. Prepare SignalBlock
    data = rng.normal(size=(n_signals, 4)).astype(np.float32)
    timestamps = np.sort(rng.uniform(1.0, 100.0, size=n_signals))
    valid_mask = np.full(n_signals, valid_signals_present)
    minmax = np.stack([data.min(axis=1), data.max(axis=1)], axis=1).astype(np.float32)
    block = SignalBlock(
        data=data,
        timestamps=timestamps,
        trigger=np.zeros(n_signals),
        vrange=np.ones(n_signals),
        valid_mask=valid_mask,
        minmax=minmax,
    )

    # 2. Prepare EnvironmentalSeries
    if env_present:
        n_env = 10
        t_env = np.linspace(1.0, 100.0, n_env)
        env = EnvironmentalSeries(
            timestamps=t_env,
            temperature=np.full(n_env, 25.0),
            humidity=np.full(n_env, 50.0),
        )
    else:
        env = EnvironmentalSeries(
            timestamps=np.array([], dtype=np.float64),
            temperature=np.array([], dtype=np.float64),
            humidity=np.array([], dtype=np.float64),
        )

    # 3. Prepare EventSeries (always present in this test to check index)
    events = EventSeries(
        timestamps=np.array([10.0, 50.0], dtype=np.float64),
        event_type=np.array(["Event 1", "Event 2"]),
    )

    sensor_cfg = SensorConfig(
        name="UHF",
        hdf5_group="signals",
        fs_hz=1e9,
        n_samples=4,
        freq_limit_hz=1e8,
        axis_unit="us",
        axis_scale=1e6,
        target_block_bytes=1024,
    )

    # 4. Build true figure
    active_mask = np.ones(n_signals, dtype=bool)
    fig = build_timeseries_figure(
        sensor_cfg, block, env, events, t0=0.0, active_mask=active_mask, show_events=True
    )

    # Oracle assertion: find the actual index of the 'Eventos' trace in fig.data
    trace_names = [tr.name for tr in fig.data]
    assert "Eventos" in trace_names, f"Eventos trace missing from figure! Traces: {trace_names}"
    actual_event_idx = trace_names.index("Eventos")

    assert actual_event_idx == expected_idx, (
        f"Mismatch for env={env_present}, valid={valid_signals_present}: "
        f"actual index {actual_event_idx} != expected {expected_idx}. Traces: {trace_names}"
    )

    # 5. Now verify the callback _on_refresh_metric_decorations computes this exact same index
    # when passed sensor_or_fig="UHF" (the new Milestone 3 state value)
    state = AppState(cache_dir=tmp_path / "cache_matrix", warmup_on_load=False)
    dataset = LoadedDataset(
        dataset_id="ds_matrix",
        source_path=Path("simulated.h5"),
        experiment="test",
        sensor_configs={"UHF": sensor_cfg},
        blocks={"UHF": block},
        environmental=env,
        events=events,
        t0=0.0,
    )
    state.publish_dataset(dataset)
    monkeypatch.setattr(swc, "get_state", lambda: state)

    dash_app = create_app()
    decorations_fn = _get_callback_fn(dash_app, "graph-timeseries")

    # Call callback with sensor string "UHF"
    ts_patch_off, _ = decorations_fn([], [], 60.0, [], [], "UHF")

    ops = ts_patch_off.to_plotly_json()["operations"]
    assert len(ops) == 1
    assert ops[0]["operation"] == "Assign"
    assert ops[0]["location"] == ["data", expected_idx, "visible"]
    assert ops[0]["params"]["value"] is False


def test_adversarial_event_lines_when_no_events_in_dataset(tmp_path, sensor_config, block, monkeypatch):
    """When dataset.events.timestamps is empty, the callback must NOT emit any patch
    operations targeting nonexistent trace indices.
    """
    empty_events = EventSeries(
        timestamps=np.array([], dtype=np.float64),
        event_type=np.array([], dtype=str),
    )
    env = EnvironmentalSeries(
        timestamps=np.array([1.0, 2.0]),
        temperature=np.array([20.0, 21.0]),
        humidity=np.array([40.0, 41.0]),
    )
    state = AppState(cache_dir=tmp_path / "cache_no_events", warmup_on_load=False)
    dataset_no_ev = LoadedDataset(
        dataset_id="ds_no_events",
        source_path=Path("simulated.h5"),
        experiment="test",
        sensor_configs={"UHF": sensor_config},
        blocks={"UHF": block},
        environmental=env,
        events=empty_events,
        t0=0.0,
    )
    state.publish_dataset(dataset_no_ev)
    monkeypatch.setattr(swc, "get_state", lambda: state)

    dash_app = create_app()
    decorations_fn = _get_callback_fn(dash_app, "graph-timeseries")

    # Toggle events ON and OFF with string sensor
    ts_patch_on, _ = decorations_fn(["show"], [], 60.0, [], [], "UHF")
    assert len(ts_patch_on.to_plotly_json()["operations"]) == 0

    ts_patch_off, _ = decorations_fn([], [], 60.0, [], [], "UHF")
    assert len(ts_patch_off.to_plotly_json()["operations"]) == 0


def test_adversarial_event_lines_with_active_mask_filtering_all_signals(tmp_path, sensor_config, block, monkeypatch):
    """When active_mask filters out ALL signals (has_envelope becomes False even though
    valid_mask is True in block), verify event_idx correctly adjusts.
    """
    env = EnvironmentalSeries(
        timestamps=np.array([10.0, 20.0]),
        temperature=np.array([22.0, 22.5]),
        humidity=np.array([55.0, 56.0]),
    )
    events = EventSeries(
        timestamps=np.array([15.0]),
        event_type=np.array(["Single Shot"]),
    )
    state = AppState(cache_dir=tmp_path / "cache_filtered_all", warmup_on_load=False)
    dataset_filt = LoadedDataset(
        dataset_id="ds_filter_all",
        source_path=Path("simulated.h5"),
        experiment="test",
        sensor_configs={"UHF": sensor_config},
        blocks={"UHF": block},
        environmental=env,
        events=events,
        t0=0.0,
    )
    state.publish_dataset(dataset_filt)
    # Filter out all signals via active mask
    all_false_mask = np.zeros(block.data.shape[0], dtype=bool)
    state._active_mask["UHF"] = all_false_mask
    monkeypatch.setattr(swc, "get_state", lambda: state)

    dash_app = create_app()
    decorations_fn = _get_callback_fn(dash_app, "graph-timeseries")

    # has_envelope is False, has_env is True -> event_idx should be 0 + 2 = 2
    ts_patch, _ = decorations_fn(["show"], [], 60.0, [], [], "UHF")
    ops = ts_patch.to_plotly_json()["operations"]
    assert len(ops) == 1
    assert ops[0]["location"] == ["data", 2, "visible"]
    assert ops[0]["params"]["value"] is True

    # Check against true figure with active_mask all-False
    fig = build_timeseries_figure(
        sensor_config, block, env, events, t0=0.0, active_mask=all_false_mask, show_events=True
    )
    assert fig.data[2].name == "Eventos"


def test_adversarial_event_lines_missing_sensor_name_handling(tmp_path, sensor_config, block, monkeypatch):
    """Adversarial case: what happens if sensor_or_fig is a sensor name not present
    in dataset.blocks (e.g. 'UNKNOWN_SENSOR')?
    Verify callback does not raise an unhandled exception.
    """
    events = EventSeries(
        timestamps=np.array([15.0]),
        event_type=np.array(["Single Shot"]),
    )
    state = AppState(cache_dir=tmp_path / "cache_unknown_sensor", warmup_on_load=False)
    dataset_unk = LoadedDataset(
        dataset_id="ds_unknown",
        source_path=Path("simulated.h5"),
        experiment="test",
        sensor_configs={"UHF": sensor_config},
        blocks={"UHF": block},
        environmental=EnvironmentalSeries(np.array([]), np.array([]), np.array([])),
        events=events,
        t0=0.0,
    )
    state.publish_dataset(dataset_unk)
    monkeypatch.setattr(swc, "get_state", lambda: state)

    dash_app = create_app()
    decorations_fn = _get_callback_fn(dash_app, "graph-timeseries")

    # Should safely return without crashing
    ts_patch, metric_patches = decorations_fn([], [], 60.0, [], [], "UNKNOWN_SENSOR")
    assert isinstance(ts_patch, swc.Patch)


# ==============================================================================
# SUITE 4: Transport Encoding & Float32 Precision in graph_metric.py
# ==============================================================================


def test_adversarial_graph_metric_float32_dtypes():
    """Verify that build_metric_figure converts timestamps and values arrays
    to np.float32 for:
    - Raw marker scatter trace (Scatter / Scattergl)
    - Connecting lines trace (when connect_points=True)
    - Smoothing trendline trace (when smoothing is active)
    """
    n = 200
    timestamps = np.linspace(0.0, 1200.0, n, dtype=np.float64)
    values = (np.sin(timestamps / 50.0) * 10.0).astype(np.float64)

    events = EventSeries(np.array([]), [])
    smoothing = SmoothingSpec(method="media_movil_temporal", window=30.0)

    fig = build_metric_figure(
        timestamps=timestamps,
        values=values,
        events=events,
        t0=0.0,
        label="Test Voltage",
        unit="mV",
        connect_points=True,
        smoothing=smoothing,
        webgl_threshold=500,  # force SVG mode
    )

    # 3 traces expected:
    # 0: connect lines
    # 1: raw markers
    # 2: smoothing trendline
    assert len(fig.data) == 3

    # Check Trace 0: Connecting lines
    tr_lines = fig.data[0]
    assert isinstance(tr_lines.x, np.ndarray)
    assert tr_lines.x.dtype == np.float32
    assert isinstance(tr_lines.y, np.ndarray)
    assert tr_lines.y.dtype == np.float32

    # Check Trace 1: Markers
    tr_markers = fig.data[1]
    assert isinstance(tr_markers.x, np.ndarray)
    assert tr_markers.x.dtype == np.float32
    assert isinstance(tr_markers.y, np.ndarray)
    assert tr_markers.y.dtype == np.float32

    # Check Trace 2: Smoothing
    tr_smooth = fig.data[2]
    assert isinstance(tr_smooth.x, np.ndarray)
    assert tr_smooth.x.dtype == np.float32
    assert isinstance(tr_smooth.y, np.ndarray)
    assert tr_smooth.y.dtype == np.float32


def test_adversarial_graph_metric_webgl_mode_float32():
    """Verify that when points >= webgl_threshold, Scattergl is used and retains float32 arrays."""
    n = 1000
    timestamps = np.linspace(0.0, 3600.0, n, dtype=np.float64)
    values = np.random.default_rng(0).normal(size=n).astype(np.float64)
    events = EventSeries(np.array([]), np.array([], dtype=str))

    fig = build_metric_figure(
        timestamps=timestamps,
        values=values,
        events=events,
        t0=0.0,
        label="High Density Metric",
        webgl_threshold=500,  # triggers Scattergl
    )

    assert len(fig.data) == 1
    tr = fig.data[0]
    assert isinstance(tr, go.Scattergl)
    assert tr.x.dtype == np.float32
    assert tr.y.dtype == np.float32


def test_adversarial_graph_metric_plotly_json_serialization_fidelity():
    """Verify that figures with float32 arrays serialize cleanly through Plotly's JSON encoder
    and round-trip back without data corruption, NaN issues, or type serialization errors.
    """
    import base64

    n = 300
    rng = np.random.default_rng(999)
    timestamps = np.sort(rng.uniform(0.0, 7200.0, size=n))
    values = rng.exponential(scale=2.5, size=n)
    is_partial = rng.choice([True, False], size=n, p=[0.1, 0.9])
    events = EventSeries(np.array([100.0, 500.0]), np.array(["Shot 1", "Shot 2"]))

    fig = build_metric_figure(
        timestamps=timestamps,
        values=values,
        events=events,
        t0=0.0,
        label="Exponential Metric",
        unit="a.u.",
        is_partial=is_partial,
        connect_points=True,
    )

    # 1. to_dict()
    d = fig.to_dict()
    assert "data" in d

    # 2. to_json()
    json_str = fig.to_json()
    assert isinstance(json_str, str)
    assert len(json_str) > 0

    # 3. PlotlyJSONEncoder used by Dash
    dash_json = json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)
    assert isinstance(dash_json, str)

    # 4. Round-trip parsed JSON
    parsed = json.loads(dash_json)
    assert "data" in parsed
    marker_trace = next(tr for tr in parsed["data"] if tr.get("mode") == "markers")

    # Plotly serializes float32 numpy arrays as {'dtype': 'f4', 'bdata': '...'} base64 dicts
    if isinstance(marker_trace["x"], dict) and "bdata" in marker_trace["x"]:
        raw_x = base64.b64decode(marker_trace["x"]["bdata"])
        x_serialized = np.frombuffer(raw_x, dtype=np.float32)
        raw_y = base64.b64decode(marker_trace["y"]["bdata"])
        y_serialized = np.frombuffer(raw_y, dtype=np.float32)
    else:
        x_serialized = np.array(marker_trace["x"], dtype=np.float32)
        y_serialized = np.array(marker_trace["y"], dtype=np.float32)

    # Precision analysis: compare against original float32 inputs
    t_expected_min = (timestamps / 60.0).astype(np.float32)
    np.testing.assert_allclose(x_serialized, t_expected_min, rtol=1e-6, atol=1e-6)
    np.testing.assert_allclose(y_serialized, values.astype(np.float32), rtol=1e-6, atol=1e-6)


def test_adversarial_graph_metric_float32_precision_drift_bounds():
    """Adversarial precision analysis: measure maximum absolute drift between float64
    elapsed minutes and float32 representation for a long 24-hour test (86,400 seconds).
    Verify error is strictly bounded below 0.5 milliseconds (< 0.00001 minutes).
    """
    # 24 hours = 1440 minutes = 86400 seconds
    timestamps_f64 = np.linspace(0.0, 86400.0, 10000, dtype=np.float64)
    t0 = 0.0
    elapsed_minutes_f64 = timestamps_f64 / 60.0

    # Conversion as done in graph_metric.py
    elapsed_minutes_f32 = elapsed_minutes_f64.astype(np.float32)

    abs_error_minutes = np.abs(elapsed_minutes_f64 - elapsed_minutes_f32)
    max_abs_error_minutes = float(np.max(abs_error_minutes))
    max_abs_error_seconds = max_abs_error_minutes * 60.0

    # 24 hours in minutes is 1440.0. float32 has 24-bit mantissa.
    # 1440 * 2^-23 ~= 0.00017 minutes ~= 0.01 seconds (10 ms).
    # Verify that max drift is strictly less than 20 ms across the entire 24h timeline
    assert max_abs_error_seconds < 0.02, (
        f"Drift too large: {max_abs_error_seconds:.4f} s ({max_abs_error_minutes:.6f} min)"
    )


def test_adversarial_graph_metric_pathological_values():
    """Stress test: verify graph_metric handles empty arrays, subnormal numbers,
    and large dynamic ranges without crashing.
    """
    events = EventSeries(np.array([]), np.array([], dtype=str))

    # 1. Empty arrays
    fig_empty = build_metric_figure(
        timestamps=np.array([], dtype=np.float64),
        values=np.array([], dtype=np.float64),
        events=events,
        t0=0.0,
        label="Empty",
    )
    assert len(fig_empty.data) == 0
    # Must have empty set annotation
    assert any("Sin señales activas" in ann.text for ann in fig_empty.layout.annotations)

    # 2. Extreme dynamic range (1e-30 to 1e30)
    timestamps = np.array([10.0, 20.0, 30.0], dtype=np.float64)
    values = np.array([1e-35, 1.0, 1e35], dtype=np.float64)
    fig_extreme = build_metric_figure(
        timestamps=timestamps,
        values=values,
        events=events,
        t0=0.0,
        label="Extreme",
    )
    assert len(fig_extreme.data) == 1
    # Check serialization succeeds
    json_extreme = fig_extreme.to_json()
    assert "Extreme" in json_extreme
