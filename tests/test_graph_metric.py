"""Gráfica tipo #3 (evolución de métricas): unión de puntos, suavizado y visibilidad
de eventos (archivos_md/prompt-mejora-graficas.md).

No existía ninguna prueba de este módulo antes de esta tarea -- ``build_signal_figure``
y ``build_metric_figure`` solo se ejercitaban manualmente y desde ``benchmarks/``.
"""
import numpy as np

from core.models import EventSeries
from ui.components.graph_metric import build_metric_figure
from viz.smoothing import SmoothingSpec


def _events(n: int, t0: float) -> EventSeries:
    timestamps = t0 + np.linspace(0, 60.0 * n, min(n, 5) or 1)
    return EventSeries(timestamps=timestamps, event_type=np.array(["SHOT"] * timestamps.shape[0], dtype=object))


def _empty_events() -> EventSeries:
    return EventSeries(timestamps=np.array([]), event_type=np.array([], dtype=object))


def test_default_behaviour_matches_legacy_scatter_puro():
    # Sin ningún parámetro nuevo: una sola traza de marcadores a opacidad plena --
    # criterio de no-regresión del §5 (config por defecto == comportamiento actual).
    n = 50
    t = np.linspace(0.0, 3000.0, n)
    v = np.sin(t / 100.0)
    fig = build_metric_figure(t, v, _empty_events(), 0.0, label="rms")
    assert len(fig.data) == 1
    assert fig.data[0].mode == "markers"
    assert fig.data[0].opacity is None or fig.data[0].opacity == 1.0


def test_puntual_with_smoothing_dims_raw_markers_and_adds_trend_line():
    n = 500
    t = np.linspace(0.0, 30_000.0, n)
    v = np.sin(t / 500.0) + np.random.default_rng(0).normal(0, 0.1, n)
    fig = build_metric_figure(
        t, v, _empty_events(), 0.0, label="rms",
        smoothing=SmoothingSpec(method="media_movil_temporal", window=5.0),
    )
    modes = [tr.mode for tr in fig.data]
    assert "markers" in modes
    marker_trace = next(tr for tr in fig.data if tr.mode == "markers")
    assert marker_trace.opacity == 0.30
    line_trace = next(tr for tr in fig.data if tr.mode == "lines")
    assert len(line_trace.x) > 0


def test_group_regime_connects_points_with_direct_line():
    n = 20
    t = np.linspace(0.0, 6000.0, n)
    v = np.arange(n, dtype=np.float64)
    fig = build_metric_figure(t, v, _empty_events(), 0.0, label="tasa_pulsos", connect_points=True)
    modes = {tr.mode for tr in fig.data}
    assert "lines" in modes
    assert "markers" in modes
    marker_trace = next(tr for tr in fig.data if tr.mode == "markers")
    # en régimen de grupo los marcadores no se atenúan (solo se atenúan en puntual)
    assert marker_trace.opacity is None or marker_trace.opacity == 1.0


def test_group_regime_smoothing_reuses_same_smooth_function():
    n = 30
    t = np.linspace(0.0, 6000.0, n)
    v = np.arange(n, dtype=np.float64)
    fig = build_metric_figure(
        t, v, _empty_events(), 0.0, label="tasa_pulsos",
        connect_points=True, smoothing=SmoothingSpec(window=5.0),
    )
    # línea directa + marcadores + línea de tendencia = 3 trazas
    assert len(fig.data) == 3


def test_show_events_false_yields_no_shapes():
    n = 10
    t = np.linspace(0.0, 600.0, n)
    v = np.arange(n, dtype=np.float64)
    events = _events(n, 0.0)
    fig_on = build_metric_figure(t, v, events, 0.0, label="rms", show_events=True)
    fig_off = build_metric_figure(t, v, events, 0.0, label="rms", show_events=False)
    assert len(fig_on.layout.shapes) > 0
    assert len(fig_off.layout.shapes) == 0


def test_uirevision_is_stable_across_rebuilds_with_same_value():
    n = 10
    t = np.linspace(0.0, 600.0, n)
    v = np.arange(n, dtype=np.float64)
    fig1 = build_metric_figure(t, v, _empty_events(), 0.0, label="rms", uirevision="UHF|dataset-1")
    fig2 = build_metric_figure(t, v, _empty_events(), 0.0, label="rms", uirevision="UHF|dataset-1")
    assert fig1.layout.uirevision == fig2.layout.uirevision == "UHF|dataset-1"


def test_is_partial_coloring_preserved_in_group_regime():
    n = 6
    t = np.linspace(0.0, 300.0, n)
    v = np.arange(n, dtype=np.float64)
    is_partial = np.array([False, False, False, False, False, True])
    fig = build_metric_figure(
        t, v, _empty_events(), 0.0, label="tasa_pulsos", connect_points=True, is_partial=is_partial,
    )
    marker_trace = next(tr for tr in fig.data if tr.mode == "markers")
    colors = list(marker_trace.marker.color)
    assert colors[-1] != colors[0]  # el último punto (parcial) usa un color distinto


def test_empty_input_does_not_raise():
    fig = build_metric_figure(
        np.array([]), np.array([]), _empty_events(), 0.0, label="rms",
        smoothing=SmoothingSpec(), connect_points=True,
    )
    assert len(fig.data) == 0


def test_reference_value_none_draws_no_shape_or_annotation():
    n = 10
    t = np.linspace(0.0, 600.0, n)
    v = np.arange(n, dtype=np.float64)
    fig = build_metric_figure(t, v, _empty_events(), 0.0, label="rms", reference_value=None)
    assert len(fig.layout.shapes) == 0
    assert len(fig.layout.annotations) == 0


def test_reference_value_draws_shape_below_layer_across_full_canvas():
    n = 10
    t = np.linspace(0.0, 600.0, n)
    v = np.arange(n, dtype=np.float64)
    fig = build_metric_figure(t, v, _empty_events(), 0.0, label="rms", reference_value=4.5)
    shapes = fig.layout.shapes
    assert len(shapes) == 1
    ref_shape = shapes[0]
    assert ref_shape.xref == "paper"
    assert ref_shape.x0 == 0 and ref_shape.x1 == 1
    assert ref_shape.y0 == ref_shape.y1 == 4.5
    assert ref_shape.layer == "below"
    assert ref_shape.line.dash == "dot"
    assert len(fig.layout.annotations) == 1
    assert "4.5" in fig.layout.annotations[0].text


def test_reference_value_coexists_with_event_shapes():
    n = 10
    t = np.linspace(0.0, 600.0, n)
    v = np.arange(n, dtype=np.float64)
    events = _events(n, 0.0)
    fig = build_metric_figure(t, v, events, 0.0, label="rms", show_events=True, reference_value=1.0)
    # Shapes de evento (líneas verticales) + la shape de referencia (horizontal) --
    # ambas conviven, ninguna reemplaza a la otra.
    assert len(fig.layout.shapes) == len(events.timestamps) + 1


def test_reference_value_unchanged_by_smoothing_or_connect_points():
    # El valor lo calcula el llamador sobre la serie cruda -- pasar smoothing o
    # connect_points no debe alterar la shape de referencia (criterio de aceptación 8).
    n = 30
    t = np.linspace(0.0, 6000.0, n)
    v = np.arange(n, dtype=np.float64)
    fig_plain = build_metric_figure(t, v, _empty_events(), 0.0, label="rms", reference_value=7.0)
    fig_smoothed = build_metric_figure(
        t, v, _empty_events(), 0.0, label="rms", reference_value=7.0,
        smoothing=SmoothingSpec(window=5.0), connect_points=True,
    )
    assert fig_plain.layout.shapes[0].y0 == fig_smoothed.layout.shapes[0].y0 == 7.0
