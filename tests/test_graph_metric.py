"""Gráfica tipo #3 (evolución de métricas): unión de puntos, suavizado y visibilidad
de eventos (archivos_md/prompt-mejora-graficas.md).

No existía ninguna prueba de este módulo antes de esta tarea -- ``build_signal_figure``
y ``build_metric_figure`` solo se ejercitaban manualmente y desde ``benchmarks/``.
"""
import numpy as np

from core.models import EventSeries
from ui.components.graph_metric import (
    EMPTY_ACTIVE_SET_MESSAGE,
    METRIC_WEBGL_THRESHOLD,
    PARTIAL_COLOR,
    POINT_COLOR,
    RAW_POINT_OPACITY_DIMMED,
    RAW_POINT_SIZE_DEFAULT,
    RAW_POINT_SIZE_DIMMED,
    build_metric_figure,
)
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


def test_adaptive_rendering_uses_scatter_below_default_threshold():
    n = 1000
    t = np.linspace(0.0, 6000.0, n)
    v = np.sin(t / 100.0)
    fig = build_metric_figure(t, v, _empty_events(), 0.0, label="rms")
    assert len(fig.data) == 1
    trace = fig.data[0]
    assert trace.type == "scatter"
    assert trace.mode == "markers"
    assert len(trace.x) == n
    assert trace.name == "rms"
    assert trace.marker.size == RAW_POINT_SIZE_DEFAULT


def test_adaptive_rendering_uses_scattergl_at_and_above_default_threshold():
    # Exact threshold boundary (5000 points)
    n_exact = METRIC_WEBGL_THRESHOLD
    t_exact = np.linspace(0.0, 6000.0, n_exact)
    v_exact = np.sin(t_exact / 100.0)
    fig_exact = build_metric_figure(t_exact, v_exact, _empty_events(), 0.0, label="rms")
    assert len(fig_exact.data) == 1
    trace_exact = fig_exact.data[0]
    assert trace_exact.type == "scattergl"
    assert trace_exact.mode == "markers"
    assert len(trace_exact.x) == n_exact
    assert trace_exact.name == "rms"

    # Above threshold (5500 points)
    n_above = 5500
    t_above = np.linspace(0.0, 6000.0, n_above)
    v_above = np.cos(t_above / 100.0)
    fig_above = build_metric_figure(t_above, v_above, _empty_events(), 0.0, label="rms")
    assert len(fig_above.data) == 1
    trace_above = fig_above.data[0]
    assert trace_above.type == "scattergl"
    assert trace_above.mode == "markers"
    assert len(trace_above.x) == n_above


def test_adaptive_rendering_custom_threshold_switches_mode():
    n = 150
    t = np.linspace(0.0, 6000.0, n)
    v = np.sin(t / 100.0)

    # Below custom threshold 200 -> Scatter (SVG)
    fig_below = build_metric_figure(t, v, _empty_events(), 0.0, label="rms", webgl_threshold=200)
    assert fig_below.data[0].type == "scatter"

    # At or above custom threshold 100 -> Scattergl (WebGL)
    fig_above = build_metric_figure(t, v, _empty_events(), 0.0, label="rms", webgl_threshold=100)
    assert fig_above.data[0].type == "scattergl"

    # Exact custom threshold 150 -> Scattergl (WebGL)
    fig_exact = build_metric_figure(t, v, _empty_events(), 0.0, label="rms", webgl_threshold=150)
    assert fig_exact.data[0].type == "scattergl"


def test_is_partial_color_array_preserved_in_both_scatter_and_scattergl():
    n = 100
    t = np.linspace(0.0, 6000.0, n)
    v = np.sin(t / 100.0)
    is_partial = np.zeros(n, dtype=bool)
    is_partial[10] = True
    is_partial[50] = True

    # Scatter (SVG)
    fig_svg = build_metric_figure(
        t, v, _empty_events(), 0.0, label="rms", is_partial=is_partial, webgl_threshold=500
    )
    trace_svg = fig_svg.data[0]
    assert trace_svg.type == "scatter"
    colors_svg = np.asarray(trace_svg.marker.color)
    assert colors_svg[0] == POINT_COLOR
    assert colors_svg[10] == PARTIAL_COLOR
    assert colors_svg[50] == PARTIAL_COLOR

    # Scattergl (WebGL)
    fig_gl = build_metric_figure(
        t, v, _empty_events(), 0.0, label="rms", is_partial=is_partial, webgl_threshold=50
    )
    trace_gl = fig_gl.data[0]
    assert trace_gl.type == "scattergl"
    colors_gl = np.asarray(trace_gl.marker.color)
    assert colors_gl[0] == POINT_COLOR
    assert colors_gl[10] == PARTIAL_COLOR
    assert colors_gl[50] == PARTIAL_COLOR


def test_dimmed_mode_opacity_preserved_in_both_scatter_and_scattergl():
    n = 100
    t = np.linspace(0.0, 6000.0, n)
    v = np.sin(t / 100.0)
    spec = SmoothingSpec(method="media_movil_temporal", window=5.0)

    # Scatter (SVG) with smoothing
    fig_svg = build_metric_figure(
        t, v, _empty_events(), 0.0, label="rms", smoothing=spec, webgl_threshold=500
    )
    marker_svg = next(tr for tr in fig_svg.data if tr.mode == "markers")
    assert marker_svg.type == "scatter"
    assert marker_svg.opacity == RAW_POINT_OPACITY_DIMMED
    assert marker_svg.marker.size == RAW_POINT_SIZE_DIMMED

    # Scattergl (WebGL) with smoothing
    fig_gl = build_metric_figure(
        t, v, _empty_events(), 0.0, label="rms", smoothing=spec, webgl_threshold=50
    )
    marker_gl = next(tr for tr in fig_gl.data if tr.mode == "markers")
    assert marker_gl.type == "scattergl"
    assert marker_gl.opacity == RAW_POINT_OPACITY_DIMMED
    assert marker_gl.marker.size == RAW_POINT_SIZE_DIMMED


def test_scattergl_preserves_all_marker_trace_attributes():
    n = 120
    t = np.linspace(0.0, 6000.0, n)
    v = np.sin(t / 50.0)
    fig = build_metric_figure(t, v, _empty_events(), 0.0, label="kurtosis", unit="V", webgl_threshold=100)
    trace = fig.data[0]
    assert trace.type == "scattergl"
    assert trace.mode == "markers"
    assert trace.name == "kurtosis"
    np.testing.assert_allclose(np.asarray(trace.x), t / 60.0, rtol=1e-5, atol=1e-5)
    np.testing.assert_allclose(np.asarray(trace.y), v, rtol=1e-5, atol=1e-5)
    assert trace.marker.size == RAW_POINT_SIZE_DEFAULT
    assert trace.opacity is None or trace.opacity == 1.0
    assert fig.layout.yaxis.title.text == "kurtosis (V)"


def test_metric_figure_with_zero_points_shows_empty_active_set_message():
    t_empty = np.array([], dtype=np.float64)
    v_empty = np.array([], dtype=np.float64)
    fig = build_metric_figure(t_empty, v_empty, _empty_events(), 0.0, label="rms")
    assert len(fig.data) == 0
    assert any(EMPTY_ACTIVE_SET_MESSAGE in str(ann.text) for ann in fig.layout.annotations)


def test_metric_figure_encodes_coordinates_as_float32():
    n = 50
    t = np.linspace(0.0, 3000.0, n)
    v = np.sin(t / 100.0)
    spec = SmoothingSpec(method="media_movil_temporal", window=5.0)
    fig = build_metric_figure(t, v, _empty_events(), 0.0, label="rms", smoothing=spec, connect_points=True)

    for trace in fig.data:
        x_arr = np.asarray(trace.x)
        y_arr = np.asarray(trace.y)
        assert x_arr.dtype == np.float32
        assert y_arr.dtype == np.float32

