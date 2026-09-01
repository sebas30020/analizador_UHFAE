"""Toggle "Mostrar eventos": que el ``dash.Patch`` no arrase con nada más.

El control de eventos no reconstruye las figuras, las parchea
(``ui/callbacks/sensor_window_callbacks.py``). Un ``Patch`` opera por índice, así que
la regresión que vigilan estas pruebas es que el parche toque **solo** la traza de
eventos: si la figura no la trae, o llega vacía o ``None``, no debe emitir ninguna
operación; y en la gráfica #3 no puede llevarse por delante la línea de referencia ni
su anotación, que viven en el mismo ``layout.shapes``.

La última prueba corre contra el dataset real y se salta sola si no está en la máquina.
"""
from __future__ import annotations

import numpy as np
import pytest
import plotly.graph_objects as go
from dash import Patch

import ui.callbacks.sensor_window_callbacks as swc
from core.models import EnvironmentalSeries, EventSeries, SensorConfig, SignalBlock
from metrics.registry import discover_metrics
from ui.app import create_app
from ui.callbacks.helpers import encode_metric_option
from ui.components.event_lines import build_event_line_shapes, build_event_lines_trace
from ui.components.graph_metric import build_metric_figure
from ui.components.graph_timeseries import build_timeseries_figure
from ui.reference_registry import get_reference_registry
from ui.state import AppState


@pytest.fixture(autouse=True, scope="module")
def _ensure_registered():
    discover_metrics()


@pytest.fixture(autouse=True)
def _clear_reference_registry():
    get_reference_registry().clear()
    yield
    get_reference_registry().clear()


def _get_callback_fn(dash_app, key_substring: str):
    for k, v in dash_app.callback_map.items():
        if key_substring in k:
            return v["callback"].__wrapped__
    raise AssertionError(f"Callback with substring '{key_substring}' not found")


# --- Toggle de eventos y mecánica del Patch en la gráfica #1 ----------------


def test_decorations_patch_on_dict_timeseries_figure(tmp_path, synthetic_hdf5, monkeypatch):
    state = AppState(cache_dir=tmp_path / "cache", warmup_on_load=False)
    state.load_dataset(synthetic_hdf5)
    monkeypatch.setattr(swc, "get_state", lambda: state)
    dash_app = create_app()

    decorations_fn = _get_callback_fn(dash_app, "graph-timeseries")
    shapes_fn = _get_callback_fn(dash_app, "event-shapes.data")
    event_shapes = shapes_fn(state.dataset_version, "UHF")

    # Build real timeseries dict
    block = state.dataset.blocks["UHF"]
    cfg = state.dataset.sensor_configs["UHF"]
    fig = build_timeseries_figure(
        cfg, block, state.dataset.environmental, state.dataset.events, state.dataset.t0,
        state.get_active_mask("UHF"), show_events=True
    )
    fig_dict = fig.to_dict()

    # Find index of Eventos trace in fig_dict
    event_idx = next(i for i, tr in enumerate(fig_dict["data"]) if tr.get("name") == "Eventos")

    # 1. Toggle OFF
    ts_patch_off, metric_patches = decorations_fn([], [], 60.0, event_shapes, [], fig_dict)
    ops_off = ts_patch_off.to_plotly_json()["operations"]
    assert len(ops_off) == 1
    assert ops_off[0]["operation"] == "Assign"
    assert ops_off[0]["location"] == ["data", event_idx, "visible"]
    assert ops_off[0]["params"]["value"] is False

    # 2. Toggle ON
    ts_patch_on, _ = decorations_fn(["show"], [], 60.0, event_shapes, [], fig_dict)
    ops_on = ts_patch_on.to_plotly_json()["operations"]
    assert len(ops_on) == 1
    assert ops_on[0]["operation"] == "Assign"
    assert ops_on[0]["location"] == ["data", event_idx, "visible"]
    assert ops_on[0]["params"]["value"] is True


def test_decorations_patch_on_plotly_figure_object(tmp_path, synthetic_hdf5, monkeypatch):
    state = AppState(cache_dir=tmp_path / "cache", warmup_on_load=False)
    state.load_dataset(synthetic_hdf5)
    monkeypatch.setattr(swc, "get_state", lambda: state)
    dash_app = create_app()

    decorations_fn = _get_callback_fn(dash_app, "graph-timeseries")
    shapes_fn = _get_callback_fn(dash_app, "event-shapes.data")
    event_shapes = shapes_fn(state.dataset_version, "UHF")

    block = state.dataset.blocks["UHF"]
    cfg = state.dataset.sensor_configs["UHF"]
    fig_obj = build_timeseries_figure(
        cfg, block, state.dataset.environmental, state.dataset.events, state.dataset.t0,
        state.get_active_mask("UHF"), show_events=True
    )
    event_idx = next(i for i, tr in enumerate(fig_obj.data) if tr.name == "Eventos")

    ts_patch, _ = decorations_fn([], [], 60.0, event_shapes, [], fig_obj)
    ops = ts_patch.to_plotly_json()["operations"]
    assert len(ops) == 1
    assert ops[0]["location"] == ["data", event_idx, "visible"]
    assert ops[0]["params"]["value"] is False


def test_decorations_patch_when_no_events_trace_present(tmp_path, synthetic_hdf5, monkeypatch):
    state = AppState(cache_dir=tmp_path / "cache", warmup_on_load=False)
    state.load_dataset(synthetic_hdf5)
    monkeypatch.setattr(swc, "get_state", lambda: state)
    dash_app = create_app()

    decorations_fn = _get_callback_fn(dash_app, "graph-timeseries")

    # Case A: Figure without Eventos trace
    fig_no_events = {"data": [{"name": "Señal UHF (envolvente)"}, {"name": "Temperatura (°C)"}]}
    ts_patch, _ = decorations_fn([], [], 60.0, [], [], fig_no_events)
    assert len(ts_patch.to_plotly_json()["operations"]) == 0

    # Case B: Figure is empty dict or None
    ts_patch_empty, _ = decorations_fn([], [], 60.0, [], [], {})
    assert len(ts_patch_empty.to_plotly_json()["operations"]) == 0

    ts_patch_none, _ = decorations_fn([], [], 60.0, [], [], None)
    assert len(ts_patch_none.to_plotly_json()["operations"]) == 0


# --- Que el parche no borre shapes ni línea de referencia de la #3 ---------


def test_decorations_patch_preserves_graph3_reference_line_when_events_toggled(tmp_path, synthetic_hdf5, monkeypatch):
    state = AppState(cache_dir=tmp_path / "cache", warmup_on_load=False)
    state.load_dataset(synthetic_hdf5)
    monkeypatch.setattr(swc, "get_state", lambda: state)
    dash_app = create_app()

    # Step 1: Render metrics to populate reference registry
    opt_rms = encode_metric_option("puntual", "rms")
    opt_vmax = encode_metric_option("puntual", "vmax")
    refresh_metrics = _get_callback_fn(dash_app, "metrics-graphs-container")
    refresh_metrics(
        [opt_rms, opt_vmax],
        state.dataset_version, state.filter_version,
        "by_time", 60.0, "median", 75.0,
        [], [], "media_movil_temporal", 5.0, None,
        "UHF", ["show"], ["show"], 1000.0,
    )

    registry = get_reference_registry()
    mean_rms = registry.mean_until(opt_rms, 1000.0)
    mean_vmax = registry.mean_until(opt_vmax, 1000.0)
    assert mean_rms is not None
    assert mean_vmax is not None

    shapes_fn = _get_callback_fn(dash_app, "event-shapes.data")
    event_shapes = shapes_fn(state.dataset_version, "UHF")
    assert len(event_shapes) == 2  # synthetic_hdf5 has 2 events

    decorations_fn = _get_callback_fn(dash_app, "graph-timeseries")
    metric_ids = [{"type": "graph-metric", "index": opt_rms}, {"type": "graph-metric", "index": opt_vmax}]

    # Scenario 1: Events ON, Reference ON
    _, patches_on = decorations_fn(["show"], ["show"], 1000.0, event_shapes, metric_ids, None)
    assert len(patches_on) == 2
    for i, p in enumerate(patches_on):
        ops = p.to_plotly_json()["operations"]
        shapes_op = next(op for op in ops if op["location"] == ["layout", "shapes"])
        shapes = shapes_op["params"]["value"]
        # Must contain 2 event shapes (xref="x") + 1 reference shape (xref="paper")
        assert len(shapes) == 3
        event_s = [s for s in shapes if s.get("xref") == "x"]
        ref_s = [s for s in shapes if s.get("xref") == "paper"]
        assert len(event_s) == 2
        assert len(ref_s) == 1
        expected_mean = mean_rms if i == 0 else mean_vmax
        assert ref_s[0]["y0"] == pytest.approx(expected_mean)

    # Scenario 2: Events OFF, Reference ON
    # CRITICAL: Toggling events OFF must NOT erase the reference line!
    _, patches_off = decorations_fn([], ["show"], 1000.0, event_shapes, metric_ids, None)
    assert len(patches_off) == 2
    for i, p in enumerate(patches_off):
        ops = p.to_plotly_json()["operations"]
        shapes_op = next(op for op in ops if op["location"] == ["layout", "shapes"])
        shapes = shapes_op["params"]["value"]
        # Must contain 0 event shapes + 1 reference shape (xref="paper")
        assert len(shapes) == 1
        assert shapes[0]["xref"] == "paper"
        expected_mean = mean_rms if i == 0 else mean_vmax
        assert shapes[0]["y0"] == pytest.approx(expected_mean)

    # Scenario 3: Events OFF, Reference OFF
    _, patches_none = decorations_fn([], [], 1000.0, event_shapes, metric_ids, None)
    assert len(patches_none) == 2
    for p in patches_none:
        ops = p.to_plotly_json()["operations"]
        shapes_op = next(op for op in ops if op["location"] == ["layout", "shapes"])
        shapes = shapes_op["params"]["value"]
        assert shapes == []
        annotations_op = next(op for op in ops if op["location"] == ["layout", "annotations"])
        assert annotations_op["params"]["value"] == []


def test_decorations_patch_preserves_reference_message_annotation_when_events_toggled(tmp_path, synthetic_hdf5, monkeypatch):
    state = AppState(cache_dir=tmp_path / "cache", warmup_on_load=False)
    state.load_dataset(synthetic_hdf5)
    monkeypatch.setattr(swc, "get_state", lambda: state)
    dash_app = create_app()

    opt_rms = encode_metric_option("puntual", "rms")
    refresh_metrics = _get_callback_fn(dash_app, "metrics-graphs-container")
    refresh_metrics(
        [opt_rms],
        state.dataset_version, state.filter_version,
        "by_time", 60.0, "median", 75.0,
        [], [], "media_movil_temporal", 5.0, None,
        "UHF", ["show"], ["show"], 0.0,  # t=0 -> triggers warning message
    )

    shapes_fn = _get_callback_fn(dash_app, "event-shapes.data")
    event_shapes = shapes_fn(state.dataset_version, "UHF")
    decorations_fn = _get_callback_fn(dash_app, "graph-timeseries")
    metric_ids = [{"type": "graph-metric", "index": opt_rms}]

    # Events OFF, Reference ON at t=0
    _, patches = decorations_fn([], ["show"], 0.0, event_shapes, metric_ids, None)
    ops = patches[0].to_plotly_json()["operations"]
    shapes_op = next(op for op in ops if op["location"] == ["layout", "shapes"])
    assert shapes_op["params"]["value"] == []  # No event lines, no reference line
    annotations_op = next(op for op in ops if op["location"] == ["layout", "annotations"])
    annotations = annotations_op["params"]["value"]
    assert len(annotations) == 1
    assert "mayor que 0" in annotations[0]["text"]


# --- Dataset real med_5_ago_3.hdf5 (se salta si no está) -------------------


def test_real_dataset_med_5_ago_3_event_collapse_and_decorations(tmp_path, monkeypatch):
    from pathlib import Path
    real_path = Path(r"D:\data\data\main\med_5_ago_3.hdf5")
    if not real_path.exists():
        pytest.skip("med_5_ago_3.hdf5 not found on machine")

    state = AppState(cache_dir=tmp_path / "cache", warmup_on_load=False)
    dataset = state.load_dataset(str(real_path))
    monkeypatch.setattr(swc, "get_state", lambda: state)
    dash_app = create_app()

    n_events = dataset.events.timestamps.shape[0]
    assert n_events == 98  # Exact 98 shots from med_5_ago_3

    # 1. Verify Graph #1 has 0 shapes and 1 collapsed trace
    block = dataset.blocks["AE"]
    cfg = dataset.sensor_configs["AE"]
    fig_ts = build_timeseries_figure(
        cfg, block, dataset.environmental, dataset.events, dataset.t0,
        state.get_active_mask("AE"), show_events=True
    )
    assert len(fig_ts.layout.shapes or ()) == 0
    ev_trace = next(tr for tr in fig_ts.data if tr.name == "Eventos")
    assert len(ev_trace.x) == 3 * 98
    assert ev_trace.hoverinfo == "skip"

    # 2. Verify Graph #3 uses exactly 98 shapes
    fig_m = build_metric_figure(
        block.timestamps[:100], np.ones(100), dataset.events, dataset.t0,
        label="Test Metric", show_events=True
    )
    assert len(fig_m.layout.shapes) == 98
    assert all(s["type"] == "line" and s["xref"] == "x" for s in fig_m.layout.shapes)

    # 3. Test Patch toggle on med_5_ago_3
    shapes_fn = _get_callback_fn(dash_app, "event-shapes.data")
    event_shapes = shapes_fn(state.dataset_version, "AE")
    assert len(event_shapes) == 98

    decorations_fn = _get_callback_fn(dash_app, "graph-timeseries")
    ts_patch_off, metric_patches = decorations_fn(
        [], [], 60.0, event_shapes,
        [{"type": "graph-metric", "index": "puntual:vmax"}],
        fig_ts
    )
    # Timeseries patch hides trace
    ts_ops = ts_patch_off.to_plotly_json()["operations"]
    assert ts_ops[0]["params"]["value"] is False
    # Metric patch empties shapes
    m_ops = metric_patches[0].to_plotly_json()["operations"]
    assert m_ops[0]["params"]["value"] == []
