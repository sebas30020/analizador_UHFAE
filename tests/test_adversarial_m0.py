"""Adversarial challenge test suite for Milestone 0 (M0 / Etapa 0).
Written by challenger_m0_2 to independently and empirically stress-test:
1. Baseline benchmark JSON schema and metrics (docs/benchmarks_baseline_2026.json).
2. Wire transport byte serialization (_serializar_bytes).
3. CanonicalStore roundtrip and slicing for UHF and AE datasets.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
import plotly.io as pio
import pytest

from typing import cast

from benchmarks.generate_synthetic import generate_synthetic_dataset
from benchmarks.run_benchmarks import _serializar_bytes
from core.models import SensorName
from data.storage import CanonicalStore


# ==============================================================================
# 1. Baseline JSON Verification
# ==============================================================================

def test_baseline_json_schema_and_metrics():
    baseline_path = Path("docs/benchmarks_baseline_2026.json")
    assert baseline_path.exists(), f"Baseline file not found at {baseline_path}"
    
    content = baseline_path.read_text(encoding="utf-8")
    data = json.loads(content)
    
    # 1. Metadata check
    assert "metadata" in data, "Missing 'metadata' root key"
    meta = data["metadata"]
    
    required_meta_keys = [
        "fecha_utc", "dataset", "dataset_id", "n_senales_total",
        "ingesta_total_ms", "throughput_total_senales_por_s",
        "rss_pico_mb", "rss_final_mb", "repeticiones",
        "python", "plataforma", "procesador"
    ]
    for key in required_meta_keys:
        assert key in meta, f"Missing metadata key: {key}"
    
    assert meta["n_senales_total"] > 0, "n_senales_total must be > 0"
    assert meta["ingesta_total_ms"] > 0, "ingesta_total_ms must be > 0"
    assert meta["throughput_total_senales_por_s"] > 0, "throughput must be > 0"
    assert meta["rss_pico_mb"] > 0, "rss_pico_mb must be > 0"
    assert meta["rss_final_mb"] > 0, "rss_final_mb must be > 0"
    assert meta["repeticiones"] >= 1, "repeticiones must be >= 1"
    
    # 2. Resultados check
    assert "resultados" in data, "Missing 'resultados' root key"
    resultados = data["resultados"]
    assert isinstance(resultados, list) and len(resultados) > 0, "resultados must be a non-empty list"
    
    rss_pico_found = False
    transport_bytes_found = False
    transport_checks = 0
    
    for idx, item in enumerate(resultados):
        assert "operacion" in item, f"Missing 'operacion' in item {idx}"
        assert "sensor" in item, f"Missing 'sensor' in item {idx}"
        assert "notas" in item, f"Missing 'notas' in item {idx}"
        notas = item["notas"]
        
        # Check max RSS presence
        if "rss_pico_mb" in notas:
            rss_pico_found = True
            assert notas["rss_pico_mb"] > 0, f"Invalid rss_pico_mb in item {idx}"
        
        # Check transport bytes presence and validity
        if "bytes_json" in notas and "bytes_gzip" in notas:
            transport_bytes_found = True
            transport_checks += 1
            bj = notas["bytes_json"]
            bg = notas["bytes_gzip"]
            assert isinstance(bj, int) and bj > 0, f"Invalid bytes_json ({bj}) in item {idx}"
            assert isinstance(bg, int) and bg > 0, f"Invalid bytes_gzip ({bg}) in item {idx}"
            assert bg < bj, f"bytes_gzip ({bg}) not strictly less than bytes_json ({bj}) in item {idx}"
    
    assert rss_pico_found, "No 'rss_pico_mb' recorded in any resultados notes"
    assert transport_bytes_found, "No transport bytes (bytes_json / bytes_gzip) recorded in resultados notes"
    assert transport_checks >= 6, f"Expected at least 6 render operations with transport bytes, found {transport_checks}"


# ==============================================================================
# 2. Wire Transport Byte Serialization Stress-Test
# ==============================================================================

@pytest.mark.parametrize("fig_type", ["scatter", "scattergl", "bar", "heatmap", "empty"])
def test_serializar_bytes_compression_and_fidelity(fig_type: str):
    rng = np.random.default_rng(12345)
    
    if fig_type == "empty":
        fig = go.Figure()
    elif fig_type == "scatter":
        x = np.linspace(0, 100, 1000)
        y = np.sin(x) + rng.normal(0, 0.1, 1000)
        fig = go.Figure(data=go.Scatter(x=x, y=y, mode="lines+markers", name="trace_scatter"))
    elif fig_type == "scattergl":
        x = np.linspace(0, 500, 15000)
        y = np.sin(x) * np.exp(-x / 200.0) + rng.normal(0, 0.05, 15000)
        fig = go.Figure(data=go.Scattergl(x=x, y=y, mode="markers", marker=dict(size=3, color=y)))
    elif fig_type == "bar":
        categories = [f"Cat_{i}" for i in range(200)]
        values = rng.uniform(10, 100, 200)
        fig = go.Figure(data=go.Bar(x=categories, y=values))
    elif fig_type == "heatmap":
        z = rng.uniform(0, 1, (50, 50))
        fig = go.Figure(data=go.Heatmap(z=z))
    else:
        raise ValueError(f"Unknown fig_type: {fig_type}")
    
    bytes_json, bytes_gzip = _serializar_bytes(fig)
    
    assert isinstance(bytes_json, int)
    assert isinstance(bytes_gzip, int)
    assert bytes_json > 0
    assert bytes_gzip > 0
    
    # For all realistic figures with data, gzip must be strictly smaller than json
    if fig_type != "empty":
        assert bytes_gzip < bytes_json, f"Expected bytes_gzip < bytes_json for {fig_type}: {bytes_gzip} vs {bytes_json}"
        ratio = bytes_gzip / bytes_json
        assert ratio < 0.75, f"Compression ratio {ratio:.2f} unexpectedly high for {fig_type}"
    
    # Exact lossless roundtrip fidelity
    raw_json_str = pio.to_json(fig)
    raw_encoded = raw_json_str.encode("utf-8")
    assert len(raw_encoded) == bytes_json
    
    # Compress manually with gzip level 6 (as _serializar_bytes does)
    decompressed = gzip.decompress(gzip.compress(raw_encoded, compresslevel=6))
    assert decompressed == raw_encoded
    
    # Verify JSON structure decodes without error
    parsed = json.loads(decompressed.decode("utf-8"))
    assert "data" in parsed or "layout" in parsed


def test_serializar_bytes_timeseries_and_metric_figures():
    """Stress-test _serializar_bytes using actual UI figure generators."""
    from core.models import EnvironmentalSeries, EventSeries, SignalBlock, load_sensor_configs
    from ui.components.graph_metric import build_metric_figure
    from ui.components.graph_timeseries import build_timeseries_figure
    
    cfg = load_sensor_configs(Path("config/sensors.yaml"))["UHF"]
    n_sig = 500
    m_samples = cfg.n_samples
    
    rng = np.random.default_rng(42)
    data = rng.normal(0, 0.1, (n_sig, m_samples)).astype(np.float32)
    ts = np.arange(n_sig, dtype=np.float64) * 0.05
    minmax = np.column_stack([data.min(axis=1), data.max(axis=1)]).astype(np.float32)
    valid_mask = np.ones(n_sig, dtype=bool)
    
    block = SignalBlock(
        data=data,
        timestamps=ts,
        trigger=np.full(n_sig, 0.02, dtype=np.float64),
        vrange=np.full(n_sig, 0.5, dtype=np.float64),
        valid_mask=valid_mask,
        minmax=minmax,
    )
    env = EnvironmentalSeries(timestamps=np.array([0.0, 10.0]), temperature=np.array([22.0, 22.5]), humidity=np.array([50.0, 51.0]))
    ev = EventSeries(timestamps=np.array([5.0]), event_type=np.array(["TEST"]))
    
    fig_g1 = build_timeseries_figure(cfg, block, env, ev, 0.0, valid_mask)
    bj_1, bg_1 = _serializar_bytes(fig_g1)
    assert bg_1 < bj_1
    assert bg_1 / bj_1 < 0.5
    
    metric_values = rng.uniform(0.01, 0.05, n_sig).astype(np.float64)
    fig_g3 = build_metric_figure(ts, metric_values, ev, 0.0, label="rms")
    bj_3, bg_3 = _serializar_bytes(fig_g3)
    assert bg_3 < bj_3
    assert bg_3 / bj_3 < 0.6


# ==============================================================================
# 3. CanonicalStore Roundtrip & Slicing for UHF and AE
# ==============================================================================

@pytest.mark.parametrize("sensor", ["UHF", "AE"])
def test_canonical_store_synthetic_roundtrip_and_slicing(tmp_path: Path, sensor: str):
    """Generates synthetic dataset and adversarially tests CanonicalStore slicing and invariants."""
    s_name = cast(SensorName, sensor)
    n_signals = 137  # Non-power-of-two, non-multiple-of-chunk-size
    chunk_size = 25  # Forces 6 chunks: 25, 25, 25, 25, 25, 12
    out_file = tmp_path / f"test_store_{s_name}.hdf5"
    
    generate_synthetic_dataset(
        output_path=out_file,
        n_signals=n_signals,
        sensors=s_name,
        chunk_size=chunk_size,
        seed=2026,
        experiment=f"AdvChallenge_{s_name}",
    )
    assert out_file.exists()
    
    # 1. Verification of Context Manager Enforcement
    store_no_ctx = CanonicalStore(out_file)
    with pytest.raises(RuntimeError, match="CanonicalStore debe usarse como context manager"):
        _ = store_no_ctx.dataset_id
    
    with CanonicalStore(out_file) as store:
        # 2. Metadata & Schema Invariants
        assert store.dataset_id.startswith(f"synthetic:AdvChallenge_{s_name}:{n_signals}:")
        assert store.experiment == f"AdvChallenge_{s_name}"
        assert store.normalization_version == "v1_divide_by_vrange"
        assert s_name in store.available_sensors()
        assert store.n_signals(s_name) == n_signals
        
        # 3. Full Vector Extraction & Invariants
        all_ts = store.get_all_timestamps(s_name)
        assert all_ts.shape == (n_signals,)
        assert all_ts.dtype == np.float64
        # Monotonicity check
        assert np.all(np.diff(all_ts) >= 0), "Timestamps are not monotonically non-decreasing"
        
        all_mm = store.get_all_minmax(s_name)
        assert all_mm.shape == (n_signals, 2)
        assert all_mm.dtype == np.float32
        assert np.all(all_mm[:, 0] <= all_mm[:, 1]), "Min exceeds Max in minmax array"
        
        val_mask = store.get_valid_mask(s_name)
        assert val_mask.shape == (n_signals,)
        assert val_mask.dtype == bool
        
        # 4. Pointwise Row Indexing & Consistency with MinMax
        for idx_to_test in [0, 24, 25, 50, n_signals - 1]:
            row_data, ts_val, trig_val, vr_val = store.get_signal_row(s_name, idx_to_test)
            expected_samples = 3000 if s_name == "UHF" else 10000
            assert row_data.shape == (expected_samples,)
            assert row_data.dtype == np.float32
            assert ts_val == pytest.approx(all_ts[idx_to_test])
            assert vr_val == (0.5 if s_name == "UHF" else 1.0)
            assert trig_val == (0.02 if s_name == "UHF" else 0.05)
            assert row_data.min() == pytest.approx(all_mm[idx_to_test, 0], rel=1e-5)
            assert row_data.max() == pytest.approx(all_mm[idx_to_test, 1], rel=1e-5)
        
        # 5. Adversarial Slicing across Chunk Boundaries
        slice_start = 18
        slice_stop = 55
        block_span = store.get_block(s_name, slice_start, slice_stop)
        span_len = slice_stop - slice_start
        assert block_span.data.shape == (span_len, expected_samples)
        assert block_span.timestamps.shape == (span_len,)
        assert np.array_equal(block_span.timestamps, all_ts[slice_start:slice_stop])
        assert np.array_equal(block_span.minmax, all_mm[slice_start:slice_stop, :])
        assert np.array_equal(block_span.valid_mask, val_mask[slice_start:slice_stop])
        
        # Slice at tail boundary [125, 137)
        block_tail = store.get_block(s_name, 125, n_signals)
        assert block_tail.data.shape == (12, expected_samples)
        
        # Single element slice [42, 43)
        block_one = store.get_block(s_name, 42, 43)
        assert block_one.data.shape == (1, expected_samples)
        assert block_one.timestamps[0] == all_ts[42]
        
        # 6. Iterative Block Generator Roundtrip Concatenation
        accum_data = []
        accum_ts = []
        accum_mm = []
        accum_mask = []
        for blk in store.iter_blocks(s_name, block_n_signals=33):
            accum_data.append(blk.data)
            accum_ts.append(blk.timestamps)
            accum_mm.append(blk.minmax)
            accum_mask.append(blk.valid_mask)
        
        full_recon_data = np.concatenate(accum_data, axis=0)
        full_recon_ts = np.concatenate(accum_ts, axis=0)
        full_recon_mm = np.concatenate(accum_mm, axis=0)
        full_recon_mask = np.concatenate(accum_mask, axis=0)
        
        assert full_recon_data.shape == (n_signals, expected_samples)
        assert np.array_equal(full_recon_ts, all_ts)
        assert np.array_equal(full_recon_mm, all_mm)
        assert np.array_equal(full_recon_mask, val_mask)
        
        # 7. Environmental and Events Integrity
        env = store.get_environmental()
        assert env.timestamps.shape[0] > 0
        assert env.temperature.shape == env.timestamps.shape
        assert env.humidity.shape == env.timestamps.shape
        assert np.all(env.temperature > 0.0)
        assert np.all((env.humidity >= 0.0) & (env.humidity <= 100.0))
        
        ev = store.get_events()
        assert ev.timestamps.shape[0] > 0
        assert ev.event_type.shape == ev.timestamps.shape
        for et in ev.event_type:
            assert isinstance(et, str)
            assert len(et) > 0


# ==============================================================================
# 4. Stress and Edge Cases (Extreme Chunking, Boundary Conditions, Error Paths)
# ==============================================================================

def test_canonical_store_extreme_chunk_sizes(tmp_path: Path):
    """Stress-test synthetic generation and CanonicalStore with extreme chunk sizes (1 and larger than n)."""
    # Case A: chunk_size = 1 (every signal is its own chunk write)
    file_chunk1 = tmp_path / "chunk1.hdf5"
    generate_synthetic_dataset(file_chunk1, n_signals=7, sensors="UHF", chunk_size=1, seed=101)
    with CanonicalStore(file_chunk1) as s:
        assert s.n_signals("UHF") == 7
        ts = s.get_all_timestamps("UHF")
        assert len(ts) == 7
        row, _, _, _ = s.get_signal_row("UHF", 6)
        assert row.shape == (3000,)
    
    # Case B: chunk_size >> n_signals (single chunk covers entire dataset)
    file_chunk_huge = tmp_path / "chunk_huge.hdf5"
    generate_synthetic_dataset(file_chunk_huge, n_signals=15, sensors="AE", chunk_size=1000, seed=202)
    with CanonicalStore(file_chunk_huge) as s:
        assert s.n_signals("AE") == 15
        blk = s.get_block("AE", 0, 15)
        assert blk.data.shape == (15, 10000)


def test_canonical_store_out_of_bounds_and_missing_sensors(tmp_path: Path):
    """Verify CanonicalStore raises expected exceptions on invalid accesses."""
    file_uhf_only = tmp_path / "uhf_only.hdf5"
    generate_synthetic_dataset(file_uhf_only, n_signals=10, sensors="UHF", chunk_size=5, seed=303)
    
    with CanonicalStore(file_uhf_only) as store:
        # Accessing non-existent sensor
        assert "AE" not in store.available_sensors()
        with pytest.raises(KeyError):
            _ = store.n_signals("AE")
        with pytest.raises(KeyError):
            _ = store.get_all_timestamps("AE")
        with pytest.raises(KeyError):
            _ = store.get_block("AE", 0, 5)
        
        # Out of bounds row index
        with pytest.raises(IndexError):
            _ = store.get_signal_row("UHF", 999)
        with pytest.raises(IndexError):
            _ = store.get_signal_row("UHF", 10)  # n_signals is 10, valid indices are 0..9


def test_wire_transport_compression_ratios_quantitative():
    """Quantitatively verify that Plotly figures achieve expected wire reduction."""
    rng = np.random.default_rng(999)
    # Simulate a realistic timeseries plot with 2000 points
    x = np.linspace(0, 120, 2000)
    y = rng.normal(0, 1, 2000)
    fig = go.Figure(data=go.Scattergl(x=x, y=y, mode="lines"))
    
    raw_bytes, gz_bytes = _serializar_bytes(fig)
    reduction = (raw_bytes - gz_bytes) / raw_bytes
    print(f"\n[Adversarial Check] Scattergl 2000 pts: raw={raw_bytes:,} B, gzip={gz_bytes:,} B, reduction={reduction:.1%}")
    
    # Wire compression must reduce bytes by at least 40%
    assert gz_bytes < raw_bytes
    assert reduction >= 0.40, f"Expected at least 40% reduction, got {reduction:.1%}"


