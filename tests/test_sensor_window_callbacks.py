"""Verificación de los callbacks nuevos/modificados de
``ui/callbacks/sensor_window_callbacks.py`` para el control de eventos y el suavizado
(archivos_md/prompt-mejora-graficas.md), contra ``tests/conftest.py::synthetic_hdf5``
(rápido, determinista) en vez del dataset real de 33 058 señales.

Cada ``@app.callback`` registra la función envuelta por Dash (``add_context``, que
exige kwargs internos inyectados por el dispatcher HTTP real) -- ``__wrapped__`` es la
función pura original que Dash guarda con ``functools.wraps``, invocable directamente
con los mismos argumentos posicionales que Dash le pasaría, sin levantar un servidor
(mismo patrón usado para validar esto manualmente antes de escribir estas pruebas).
"""
from __future__ import annotations

import numpy as np
import pytest
from dash.exceptions import PreventUpdate

import ui.callbacks.sensor_window_callbacks as swc
from cache.service import get_or_compute_puntual
from core.models import EventSeries
from metrics.registry import discover_metrics
from ui.app import create_app
from ui.reference_registry import get_reference_registry
from ui.state import AppState


@pytest.fixture(autouse=True, scope="module")
def _ensure_registered():
    discover_metrics()


@pytest.fixture(autouse=True)
def _clear_reference_registry():
    # ``ui/reference_registry.py`` es un singleton de proceso (mismo patrón que
    # ``ui/state.py::AppState``) -- se limpia entre pruebas para que una no herede
    # series dejadas por otra.
    get_reference_registry().clear()
    yield
    get_reference_registry().clear()


@pytest.fixture
def app_and_state(tmp_path, synthetic_hdf5, monkeypatch):
    # synthetic_hdf5 (tests/conftest.py): UHF con 4 señales cronológicas (t=1,2,105,106,
    # una inválida por vrange=0) y 2 eventos (t=104.0, t=0.5) -- suficiente variedad sin
    # pagar el costo del dataset real.
    state = AppState(cache_dir=tmp_path / "cache", warmup_on_load=False)
    state.load_dataset(synthetic_hdf5)
    monkeypatch.setattr(swc, "get_state", lambda: state)
    dash_app = create_app()
    return dash_app, state


def _wrapped(dash_app, key: str):
    assert key in dash_app.callback_map, f"clave no encontrada: {key}\ndisponibles: {list(dash_app.callback_map)}"
    return dash_app.callback_map[key]["callback"].__wrapped__


def _toggle_events_key(dash_app) -> str:
    for k in dash_app.callback_map:
        if "graph-timeseries" in k and "graph-metric" in k:
            return k
    raise AssertionError("no se encontró el callback de toggle de eventos")


def _disabled_key(dash_app) -> str:
    for k in dash_app.callback_map:
        if "smoothing-method" in k and "gap-threshold" in k:
            return k
    raise AssertionError("no se encontró el callback de disabled de suavizado")


# --- event-shapes: construcción única por dataset ----------------------------------


def test_event_shapes_built_once_per_dataset(app_and_state):
    dash_app, state = app_and_state
    fn = _wrapped(dash_app, "event-shapes.data")
    shapes = fn(state.dataset_version, "UHF")
    assert len(shapes) == 2
    assert all(s["type"] == "line" for s in shapes)


def test_event_shapes_empty_without_dataset(app_and_state, monkeypatch, tmp_path):
    dash_app, _ = app_and_state
    empty_state = AppState(cache_dir=tmp_path / "cache2", warmup_on_load=False)
    monkeypatch.setattr(swc, "get_state", lambda: empty_state)
    fn = _wrapped(dash_app, "event-shapes.data")
    assert fn(0, "UHF") == []


# --- show-events: deshabilitado y etiqueta cuando no hay eventos -------------------


def test_show_events_options_enabled_when_events_present(app_and_state):
    dash_app, state = app_and_state
    fn = _wrapped(dash_app, "show-events.options")
    opts = fn(state.dataset_version, "UHF")
    assert opts[0]["disabled"] is False
    assert opts[0]["label"] == " Mostrar eventos"


def test_show_events_options_disabled_when_no_events(app_and_state):
    dash_app, state = app_and_state
    state.dataset.events = EventSeries(timestamps=np.array([]), event_type=np.array([], dtype=object))
    fn = _wrapped(dash_app, "show-events.options")
    opts = fn(state.dataset_version, "UHF")
    assert opts[0]["disabled"] is True
    assert "sin eventos" in opts[0]["label"]


# --- Toggle de eventos: dash.Patch, sin recálculo -----------------------------------


def test_toggle_events_patch_hides_shapes_without_touching_anything_else(app_and_state):
    dash_app, state = app_and_state
    shapes_fn = _wrapped(dash_app, "event-shapes.data")
    shapes = shapes_fn(state.dataset_version, "UHF")
    fn_toggle = _wrapped(dash_app, _toggle_events_key(dash_app))

    # show_events_value=[] -> ocultar; línea de referencia apagada (no forma parte de
    # este caso de prueba).
    ts_patch, metric_patches = fn_toggle([], [], 60.0, shapes, [])
    ops = ts_patch.to_plotly_json()["operations"]
    assert len(ops) == 1
    assert ops[0]["operation"] == "Assign"
    assert ops[0]["location"] == ["layout", "shapes"]
    assert ops[0]["params"]["value"] == []
    assert metric_patches == []  # sin ninguna gráfica de métrica montada en este caso


def test_toggle_events_patch_restores_the_exact_precomputed_shapes(app_and_state):
    dash_app, state = app_and_state
    shapes_fn = _wrapped(dash_app, "event-shapes.data")
    shapes = shapes_fn(state.dataset_version, "UHF")
    fn_toggle = _wrapped(dash_app, _toggle_events_key(dash_app))

    ts_patch, _ = fn_toggle(["show"], [], 60.0, shapes, [])
    ops = ts_patch.to_plotly_json()["operations"]
    assert ops[0]["params"]["value"] == shapes


def test_toggle_events_patch_covers_every_matched_metric_graph(app_and_state):
    dash_app, state = app_and_state
    shapes_fn = _wrapped(dash_app, "event-shapes.data")
    shapes = shapes_fn(state.dataset_version, "UHF")
    fn_toggle = _wrapped(dash_app, _toggle_events_key(dash_app))

    metric_ids = [{"type": "graph-metric", "index": "puntual:rms"}, {"type": "graph-metric", "index": "grupo_intrinseca:tasa_pulsos"}]
    _, metric_patches = fn_toggle(["show"], [], 60.0, shapes, metric_ids)
    assert len(metric_patches) == 2
    for p in metric_patches:
        assert p.to_plotly_json()["operations"][0]["location"] == ["layout", "shapes"]
        assert p.to_plotly_json()["operations"][0]["params"]["value"] == shapes


# --- graph-timeseries: show_events + uirevision -------------------------------------


def test_refresh_timeseries_show_events_toggles_shape_count(app_and_state):
    dash_app, state = app_and_state
    fn = _wrapped(dash_app, "graph-timeseries.figure")
    fig_on = fn(state.dataset_version, state.filter_version, "UHF", ["show"])
    fig_off = fn(state.dataset_version, state.filter_version, "UHF", [])
    assert len(fig_on.layout.shapes) == 2
    assert len(fig_off.layout.shapes) == 0


def test_refresh_timeseries_uirevision_matches_sensor_and_dataset(app_and_state):
    dash_app, state = app_and_state
    fn = _wrapped(dash_app, "graph-timeseries.figure")
    fig = fn(state.dataset_version, state.filter_version, "UHF", ["show"])
    assert fig.layout.uirevision == f"UHF|{state.dataset.dataset_id}"


def test_refresh_timeseries_no_dataset_returns_empty_figure(app_and_state, monkeypatch, tmp_path):
    dash_app, _ = app_and_state
    empty_state = AppState(cache_dir=tmp_path / "cache3", warmup_on_load=False)
    monkeypatch.setattr(swc, "get_state", lambda: empty_state)
    fn = _wrapped(dash_app, "graph-timeseries.figure")
    fig = fn(0, 0, "UHF", ["show"])
    assert fig.data == ()


# --- metrics-graphs-container: régimen puntual (suavizado) vs. grupo (unión) -------


def test_refresh_metrics_puntual_regime_never_connects_raw_points(app_and_state):
    dash_app, state = app_and_state
    from ui.callbacks.helpers import encode_metric_option

    fn = _wrapped(dash_app, "metrics-graphs-container.children")
    graphs = fn(
        [encode_metric_option("puntual", "rms")],
        state.dataset_version, state.filter_version,
        "by_time", 60.0, "median", 75.0,
        [], [], "media_movil_temporal", 5.0, None,  # ambos suavizados apagados
        "UHF", ["show"], [], 60.0,
    )
    fig = graphs[0].children.figure
    assert [tr.mode for tr in fig.data] == ["markers"]  # sin suavizado -> sin línea, como antes


def test_refresh_metrics_puntual_regime_with_smoothing_adds_trend_line(app_and_state):
    dash_app, state = app_and_state
    from ui.callbacks.helpers import encode_metric_option

    fn = _wrapped(dash_app, "metrics-graphs-container.children")
    graphs = fn(
        [encode_metric_option("puntual", "rms")],
        state.dataset_version, state.filter_version,
        "by_time", 60.0, "median", 75.0,
        ["smooth"], [], "media_movil_temporal", 5.0, None,
        "UHF", ["show"], [], 60.0,
    )
    fig = graphs[0].children.figure
    modes = [tr.mode for tr in fig.data]
    assert "markers" in modes and "lines" in modes
    marker_trace = next(tr for tr in fig.data if tr.mode == "markers")
    assert marker_trace.opacity == 0.30  # atenuado porque la tendencia es el elemento principal


def test_refresh_metrics_group_regime_always_connects_points(app_and_state):
    dash_app, state = app_and_state
    from ui.callbacks.helpers import encode_metric_option

    fn = _wrapped(dash_app, "metrics-graphs-container.children")
    graphs = fn(
        [encode_metric_option("grupo_intrinseca", "tasa_pulsos")],
        state.dataset_version, state.filter_version,
        "by_time", 1.0, "median", 75.0,
        [], [], "media_movil_temporal", 5.0, None,  # smooth-grupo apagado (default GUI)
        "UHF", ["show"], [], 60.0,
    )
    fig = graphs[0].children.figure
    modes = {tr.mode for tr in fig.data}
    assert "lines" in modes and "markers" in modes


def test_refresh_metrics_group_regime_smoothing_off_by_default_matches_gui_default(app_and_state):
    # El checklist "smooth-grupo" parte con value=[] en control_panel.py -- confirma
    # que ese estado produce SOLO la unión directa, sin tendencia adicional.
    dash_app, state = app_and_state
    from ui.callbacks.helpers import encode_metric_option

    fn = _wrapped(dash_app, "metrics-graphs-container.children")
    graphs = fn(
        [encode_metric_option("grupo_intrinseca", "tasa_pulsos")],
        state.dataset_version, state.filter_version,
        "by_time", 1.0, "median", 75.0,
        ["smooth"], [], "media_movil_temporal", 5.0, None,  # smooth-puntual on, smooth-grupo off
        "UHF", ["show"], [], 60.0,
    )
    fig = graphs[0].children.figure
    # dos trazas: línea directa + marcadores (ninguna línea de tendencia adicional)
    assert len(fig.data) == 2


def test_refresh_metrics_uirevision_includes_option_and_dataset(app_and_state):
    dash_app, state = app_and_state
    from ui.callbacks.helpers import encode_metric_option

    opt = encode_metric_option("puntual", "rms")
    fn = _wrapped(dash_app, "metrics-graphs-container.children")
    graphs = fn(
        [opt], state.dataset_version, state.filter_version,
        "by_time", 60.0, "median", 75.0,
        [], [], "media_movil_temporal", 5.0, None,
        "UHF", ["show"], [], 60.0,
    )
    assert graphs[0].children.figure.layout.uirevision == f"{opt}|{state.dataset.dataset_id}"


def test_refresh_metrics_invalid_grouping_window_skips_group_graph_silently(app_and_state):
    dash_app, state = app_and_state
    from ui.callbacks.helpers import encode_metric_option

    fn = _wrapped(dash_app, "metrics-graphs-container.children")
    graphs = fn(
        [encode_metric_option("grupo_intrinseca", "tasa_pulsos")],
        state.dataset_version, state.filter_version,
        "by_time", 0.0, "median", 75.0,  # grouping_value inválido
        [], [], "media_movil_temporal", 5.0, None,
        "UHF", ["show"], [], 60.0,
    )
    assert graphs == []


# --- Controles dependientes: etiqueta dinámica y disabled ---------------------------


def test_smoothing_window_label_switches_with_method(app_and_state):
    dash_app, _ = app_and_state
    fn = _wrapped(dash_app, "smoothing-window-label.children")
    assert "min" in fn("media_movil_temporal")
    assert "puntos" in fn("mediana_movil_puntos")


def test_smoothing_controls_disabled_when_both_smoothings_off(app_and_state):
    dash_app, _ = app_and_state
    fn = _wrapped(dash_app, _disabled_key(dash_app))
    disabled_method, disabled_window, disabled_gap = fn([], [])
    assert (disabled_method, disabled_window, disabled_gap) == (True, True, True)


def test_smoothing_controls_enabled_when_either_smoothing_on(app_and_state):
    dash_app, _ = app_and_state
    fn = _wrapped(dash_app, _disabled_key(dash_app))
    assert fn(["smooth"], []) == (False, False, False)
    assert fn([], ["smooth"]) == (False, False, False)


def test_reference_line_control_disabled_by_default_and_when_off(app_and_state):
    dash_app, _ = app_and_state
    fn = _wrapped(dash_app, "reference-line-t.disabled")
    assert fn([]) is True
    assert fn(["show"]) is False


# --- Línea de referencia (archivos_md/prompt-linea-referencia.md) ------------------


def _rms_option():
    from ui.callbacks.helpers import encode_metric_option

    return encode_metric_option("puntual", "rms")


def test_refresh_metrics_off_by_default_draws_no_reference_line(app_and_state):
    dash_app, state = app_and_state
    fn = _wrapped(dash_app, "metrics-graphs-container.children")
    graphs = fn(
        [_rms_option()], state.dataset_version, state.filter_version,
        "by_time", 60.0, "median", 75.0,
        [], [], "media_movil_temporal", 5.0, None,
        "UHF", ["show"], [], 60.0,  # show-reference-line=[] -> apagado (default)
    )
    fig = graphs[0].children.figure
    assert len(fig.layout.shapes) == 2  # solo los 2 eventos, ninguna línea de referencia
    assert len(fig.layout.annotations) == 0


def test_refresh_metrics_enabled_draws_line_matching_independent_mean(app_and_state):
    dash_app, state = app_and_state
    block = state.dataset.blocks["UHF"]
    cfg = state.dataset.sensor_configs["UHF"]
    active_mask = state.get_active_mask("UHF")
    ts, vals = get_or_compute_puntual(state.cache, block, cfg, state.dataset.dataset_id, "rms", active_mask=active_mask)
    t_minutes_all = (ts - state.dataset.t0) / 60.0
    reference_t = float(t_minutes_all.max()) + 1.0  # cubre toda la serie

    fn = _wrapped(dash_app, "metrics-graphs-container.children")
    graphs = fn(
        [_rms_option()], state.dataset_version, state.filter_version,
        "by_time", 60.0, "median", 75.0,
        [], [], "media_movil_temporal", 5.0, None,
        "UHF", ["show"], ["show"], reference_t,
    )
    fig = graphs[0].children.figure
    ref_shapes = [s for s in fig.layout.shapes if s.xref == "paper"]
    assert len(ref_shapes) == 1
    expected_mean = float(np.mean(vals))
    assert ref_shapes[0].y0 == pytest.approx(expected_mean)
    assert len(fig.layout.annotations) == 1
    assert f"{expected_mean:.6g}" in fig.layout.annotations[0].text


def test_refresh_metrics_reference_value_unaffected_by_smoothing(app_and_state):
    # Criterio de aceptación 8: activar el suavizado no debe mover la línea.
    dash_app, state = app_and_state
    fn = _wrapped(dash_app, "metrics-graphs-container.children")

    def _render(smooth_puntual):
        return fn(
            [_rms_option()], state.dataset_version, state.filter_version,
            "by_time", 60.0, "median", 75.0,
            smooth_puntual, [], "media_movil_temporal", 5.0, None,
            "UHF", ["show"], ["show"], 1000.0,
        )

    fig_plain = _render([])[0].children.figure
    fig_smoothed = _render(["smooth"])[0].children.figure
    ref_plain = [s for s in fig_plain.layout.shapes if s.xref == "paper"][0]
    ref_smoothed = [s for s in fig_smoothed.layout.shapes if s.xref == "paper"][0]
    assert ref_plain.y0 == ref_smoothed.y0


def test_refresh_metrics_t_zero_shows_message_not_line(app_and_state):
    dash_app, state = app_and_state
    fn = _wrapped(dash_app, "metrics-graphs-container.children")
    graphs = fn(
        [_rms_option()], state.dataset_version, state.filter_version,
        "by_time", 60.0, "median", 75.0,
        [], [], "media_movil_temporal", 5.0, None,
        "UHF", ["show"], ["show"], 0.0,
    )
    fig = graphs[0].children.figure
    assert not any(s.xref == "paper" for s in fig.layout.shapes)
    assert len(fig.layout.annotations) == 1
    assert "mayor que 0" in fig.layout.annotations[0].text


def test_refresh_metrics_populates_registry_for_decorations_callback(app_and_state):
    dash_app, state = app_and_state
    fn = _wrapped(dash_app, "metrics-graphs-container.children")
    fn(
        [_rms_option()], state.dataset_version, state.filter_version,
        "by_time", 60.0, "median", 75.0,
        [], [], "media_movil_temporal", 5.0, None,
        "UHF", ["show"], [], 60.0,
    )
    registry = get_reference_registry()
    assert registry.mean_until(_rms_option(), 1000.0) is not None


def test_refresh_metrics_clears_registry_when_selection_emptied(app_and_state):
    dash_app, state = app_and_state
    fn = _wrapped(dash_app, "metrics-graphs-container.children")
    fn(
        [_rms_option()], state.dataset_version, state.filter_version,
        "by_time", 60.0, "median", 75.0,
        [], [], "media_movil_temporal", 5.0, None,
        "UHF", ["show"], [], 60.0,
    )
    registry = get_reference_registry()
    assert registry.mean_until(_rms_option(), 1000.0) is not None

    fn(
        [], state.dataset_version, state.filter_version,
        "by_time", 60.0, "median", 75.0,
        [], [], "media_movil_temporal", 5.0, None,
        "UHF", ["show"], [], 60.0,
    )
    assert registry.mean_until(_rms_option(), 1000.0) is None


def test_decorations_callback_patches_reference_line_from_registry(app_and_state):
    dash_app, state = app_and_state
    refresh_fn = _wrapped(dash_app, "metrics-graphs-container.children")
    refresh_fn(
        [_rms_option()], state.dataset_version, state.filter_version,
        "by_time", 60.0, "median", 75.0,
        [], [], "media_movil_temporal", 5.0, None,
        "UHF", ["show"], [], 60.0,
    )

    registry = get_reference_registry()
    expected = registry.mean_until(_rms_option(), 1000.0)
    assert expected is not None

    shapes_fn = _wrapped(dash_app, "event-shapes.data")
    event_shapes = shapes_fn(state.dataset_version, "UHF")
    decorations_fn = _wrapped(dash_app, _toggle_events_key(dash_app))
    metric_ids = [{"type": "graph-metric", "index": _rms_option()}]

    # Conmutar la visibilidad NO debe volver a pasar por _on_refresh_metrics: el
    # promedio se lee directamente del registro ya poblado (criterio de aceptación 7).
    _, metric_patches = decorations_fn(["show"], ["show"], 1000.0, event_shapes, metric_ids)
    ops = metric_patches[0].to_plotly_json()["operations"]
    shapes_op = next(op for op in ops if op["location"] == ["layout", "shapes"])
    ref_shapes = [s for s in shapes_op["params"]["value"] if s["xref"] == "paper"]
    assert len(ref_shapes) == 1
    assert ref_shapes[0]["y0"] == pytest.approx(expected)


# --- Auto-play (rama auto-play) ----------------------------------------------------


def _autoplay_toggle_key(dash_app) -> str:
    for k in dash_app.callback_map:
        if "autoplay-interval.disabled" in k:
            return k
    raise AssertionError("no se encontró el callback de conmutación de auto-play")


def _autoplay_speed_key(dash_app) -> str:
    for k in dash_app.callback_map:
        if "autoplay-interval.interval" in k:
            return k
    raise AssertionError("no se encontró el callback de velocidad de auto-play")


def test_autoplay_toggle_starts_and_stops_the_interval(app_and_state):
    dash_app, _ = app_and_state
    fn = _wrapped(dash_app, _autoplay_toggle_key(dash_app))

    # Arranca deshabilitado (no hay ticks hasta pulsar): primer clic -> reproduciendo.
    disabled, label, class_name = fn(1, True)
    assert disabled is False
    assert label == "⏸ Pausar"
    assert "playing" in class_name

    # Segundo clic -> pausa, y el botón vuelve a ofrecer reproducir.
    disabled, label, class_name = fn(2, False)
    assert disabled is True
    assert label == "▶ Auto-play"
    assert "playing" not in class_name


def test_autoplay_speed_sets_interval_and_label(app_and_state):
    dash_app, _ = app_and_state
    fn = _wrapped(dash_app, _autoplay_speed_key(dash_app))

    interval_ms, label = fn(2.0)
    assert interval_ms == 500
    assert label == "2.0 señales/s"

    # Velocidad fuera del selector: satura en el suelo seguro, no encola peticiones.
    interval_ms, _label = fn(1000.0)
    assert interval_ms == swc.autoplay_interval_ms(1000.0)


def _call_navigate(dash_app, state, monkeypatch, triggered_id, nav_value=None, sensor="UHF"):
    """Invoca ``_on_navigate`` simulando el contexto de Dash (``ctx.triggered_id``),
    que fuera de un dispatch HTTP real no existe."""
    from types import SimpleNamespace

    monkeypatch.setattr(swc, "ctx", SimpleNamespace(triggered_id=triggered_id, triggered=[{"value": None}]))
    fn = _wrapped(dash_app, "nav-index.value")
    return fn(None, None, nav_value, None, [], [], state.dataset_version, 1, sensor)


def _enable_2d_map(dash_app, state, monkeypatch, x_metric="vmax", y_metric="rms"):
    """Monta el mapa 2D vía ``_on_refresh_maps`` -- es lo que llena el registro
    (``ui/map_registry.py``) del que dependen el click y el lazo para traducir la
    posición de un punto a un índice de señal."""
    from types import SimpleNamespace

    monkeypatch.setattr(swc, "ctx", SimpleNamespace(triggered_id=None, triggered=[{"value": None}]))
    refresh_maps = _wrapped(dash_app, "maps-container.children")
    refresh_maps(
        ["show"], x_metric, y_metric,
        [], None, None, None,
        state.dataset_version, state.filter_version, "UHF", 0,
    )


def _map_event_ctx(monkeypatch, index, payload):
    from types import SimpleNamespace

    monkeypatch.setattr(
        swc, "ctx",
        SimpleNamespace(triggered_id={"type": "graph-map", "index": index}, triggered=[{"value": payload}]),
    )


# El fixture synthetic_hdf5 tiene 4 señales UHF ordenadas cronológicamente, con la de
# índice global 1 inválida (vrange=0) -- así que el mapa dibuja las señales 0, 2 y 3, y
# la posición 1 dentro de la traza corresponde a la señal global 2. Que estos dos
# números NO coincidan es justamente lo que hace válida la prueba.
_EXPECTED_SIGNAL_AT_POSITION_1 = 2


def test_click_on_map_navigates_to_signal_by_point_position(app_and_state, monkeypatch):
    # Payload con la forma REAL que Dash entrega desde el navegador: curveNumber y
    # pointNumber, SIN customdata (ver ui/components/graph_map_common.py). Antes esta
    # prueba usaba customdata y pasaba, pero el click no funcionaba en la app real.
    dash_app, state = app_and_state
    _enable_2d_map(dash_app, state, monkeypatch)
    state.set_active_index("UHF", 0)

    click_data = {"points": [{"curveNumber": 0, "pointNumber": 1, "x": 0.1, "y": 0.2}]}
    _map_event_ctx(monkeypatch, "2d", click_data)
    fn = _wrapped(dash_app, "nav-index.value")
    result = fn(None, None, None, None, [], [click_data], state.dataset_version, 1, "UHF")
    assert result == _EXPECTED_SIGNAL_AT_POSITION_1


def test_click_on_map_highlight_trace_prevents_update(app_and_state, monkeypatch):
    # curveNumber=1 es la traza de resaltado -- clicar sobre la propia señal ya
    # seleccionada no debe hacer nada (ni error, ni navegación).
    dash_app, state = app_and_state
    _enable_2d_map(dash_app, state, monkeypatch)

    click_data = {"points": [{"curveNumber": 1, "pointNumber": 0, "x": 0.1, "y": 0.2}]}
    _map_event_ctx(monkeypatch, "2d", click_data)
    fn = _wrapped(dash_app, "nav-index.value")
    with pytest.raises(PreventUpdate):
        fn(None, None, None, None, [], [click_data], state.dataset_version, 1, "UHF")


def test_click_on_map_without_registry_entry_prevents_update(app_and_state, monkeypatch):
    # Mapa deshabilitado (registro vacío): un evento en vuelo no debe reventar.
    from types import SimpleNamespace

    dash_app, state = app_and_state
    monkeypatch.setattr(swc, "ctx", SimpleNamespace(triggered_id=None, triggered=[{"value": None}]))
    _wrapped(dash_app, "maps-container.children")(
        [], None, None, [], None, None, None,
        state.dataset_version, state.filter_version, "UHF", 0,
    )

    click_data = {"points": [{"curveNumber": 0, "pointNumber": 0}]}
    _map_event_ctx(monkeypatch, "2d", click_data)
    fn = _wrapped(dash_app, "nav-index.value")
    with pytest.raises(PreventUpdate):
        fn(None, None, None, None, [], [click_data], state.dataset_version, 1, "UHF")


def _map_highlight_key(dash_app) -> str:
    for k in dash_app.callback_map:
        if "graph-map" in k and "figure" in k:
            return k
    raise AssertionError("no se encontró el callback de resaltado de los mapas")


def test_map_highlight_patch_moves_highlight_trace_without_recomputing(app_and_state, monkeypatch):
    # Habilita el mapa 2D (vmax/rms) vía _on_refresh_maps -- esto llena el registro
    # (ui/map_registry.py) que _on_refresh_map_highlight necesita para no recalcular.
    from types import SimpleNamespace

    dash_app, state = app_and_state
    monkeypatch.setattr(swc, "ctx", SimpleNamespace(triggered_id=None, triggered=[{"value": None}]))
    refresh_maps = _wrapped(dash_app, "maps-container.children")
    refresh_maps(
        ["show"], "vmax", "rms",
        [], None, None, None,
        state.dataset_version, state.filter_version, "UHF", 0,
    )

    highlight_fn = _wrapped(dash_app, _map_highlight_key(dash_app))
    map_ids = [{"index": "2d", "type": "graph-map"}]
    # Señal 2: tras el ordenamiento cronológico del fixture (ingest_sensor ordena por
    # timestamp explícitamente), el índice global 1 corresponde a la señal con
    # vrange=0 (inválida, excluida del mapa) -- 2 sí es una señal válida presente.
    patches = highlight_fn(2, map_ids)
    assert len(patches) == 1

    ops = {op["location"][-1]: op["params"]["value"] for op in patches[0].to_plotly_json()["operations"]}
    # Un solo punto por eje, sin volver a pasar por build_map_dataset.
    assert len(ops["x"]) == 1 and len(ops["y"]) == 1


def test_map_highlight_patch_empty_when_no_map_enabled(app_and_state, monkeypatch):
    from types import SimpleNamespace

    dash_app, state = app_and_state
    monkeypatch.setattr(swc, "ctx", SimpleNamespace(triggered_id=None, triggered=[{"value": None}]))
    refresh_maps = _wrapped(dash_app, "maps-container.children")
    refresh_maps(
        [], None, None,   # mapa 2D deshabilitado
        [], None, None, None,
        state.dataset_version, state.filter_version, "UHF", 0,
    )

    highlight_fn = _wrapped(dash_app, _map_highlight_key(dash_app))
    # Sin mapas montados, {"type": "graph-map", "index": ALL} degrada a lista vacía.
    with pytest.raises(PreventUpdate):
        highlight_fn(1, [])


# --- Lazo/caja en el mapa 2D -> filtrado (archivos_md/prompt-mapas2d3d.md §5.4) ------


def test_lasso_selection_on_2d_map_returns_signal_indices_by_position(app_and_state, monkeypatch):
    # Mismo Store "pending-exclusion-indices" que ya alimentan #1/#3. Payload real del
    # navegador: curveNumber/pointNumber, sin customdata.
    dash_app, state = app_and_state
    _enable_2d_map(dash_app, state, monkeypatch)

    selected_data = {"points": [
        {"curveNumber": 0, "pointNumber": 0, "x": 0.1, "y": 0.2},
        {"curveNumber": 0, "pointNumber": 1, "x": 0.3, "y": 0.4},
    ]}
    _map_event_ctx(monkeypatch, "2d", selected_data)
    fn = _wrapped(dash_app, "pending-exclusion-indices.data")
    result = fn(None, [], [selected_data], "UHF", "by_time", 60.0)
    # Posiciones 0 y 1 de la traza -> señales globales 0 y 2 (la 1 es inválida).
    assert result == [0, _EXPECTED_SIGNAL_AT_POSITION_1]


def test_lasso_selection_on_2d_map_feeds_the_existing_filter_pipeline(app_and_state, monkeypatch):
    # De extremo a extremo: el lazo deja la selección pendiente y "Filtrar selección"
    # (el botón que ya existía para #1/#3) la aplica sobre AppState -- sin filtro
    # paralelo (§4 del prompt).
    from types import SimpleNamespace

    dash_app, state = app_and_state
    _enable_2d_map(dash_app, state, monkeypatch)

    selected_data = {"points": [{"curveNumber": 0, "pointNumber": 1, "x": 0.3, "y": 0.4}]}
    _map_event_ctx(monkeypatch, "2d", selected_data)
    pending = _wrapped(dash_app, "pending-exclusion-indices.data")(
        None, [], [selected_data], "UHF", "by_time", 60.0
    )
    assert pending == [_EXPECTED_SIGNAL_AT_POSITION_1]

    activas_antes, _totales, _ops = state.get_filter_counts("UHF")
    monkeypatch.setattr(swc, "ctx", SimpleNamespace(triggered_id="btn-apply-filter", triggered=[{"value": None}]))
    _wrapped(dash_app, "filter-version.data")(1, None, None, None, pending, "UHF")

    activas_despues, _totales, n_ops = state.get_filter_counts("UHF")
    assert activas_despues == activas_antes - 1
    assert n_ops == 1
    assert state.get_active_mask("UHF")[_EXPECTED_SIGNAL_AT_POSITION_1] is np.False_


def test_lasso_selection_on_3d_map_prevents_update(app_and_state, monkeypatch):
    # Decisión D2: el mapa 3D nunca alimenta el filtrado, aunque en la práctica Plotly
    # no dispare selectedData sobre una escena 3D -- este es el guardarraíl defensivo.
    dash_app, _ = app_and_state
    selected_data = {"points": [{"curveNumber": 0, "pointNumber": 0}]}
    _map_event_ctx(monkeypatch, "3d", selected_data)
    fn = _wrapped(dash_app, "pending-exclusion-indices.data")
    with pytest.raises(PreventUpdate):
        fn(None, [], [selected_data], "UHF", "by_time", 60.0)


def test_autoplay_tick_advances_to_the_next_signal(app_and_state, monkeypatch):
    dash_app, state = app_and_state
    state.set_active_index("UHF", 0)
    assert _call_navigate(dash_app, state, monkeypatch, "autoplay-interval") == 1


def test_autoplay_tick_wraps_around_at_the_end(app_and_state, monkeypatch):
    dash_app, state = app_and_state
    state.set_active_index("UHF", 3)  # última de las 4 señales UHF del fixture
    assert _call_navigate(dash_app, state, monkeypatch, "autoplay-interval") == 0


def test_autoplay_tick_skips_filtered_signals(app_and_state, monkeypatch):
    dash_app, state = app_and_state
    state.apply_filter("UHF", np.array([1, 2]))  # deja activas la 0 y la 3
    state.set_active_index("UHF", 0)
    assert _call_navigate(dash_app, state, monkeypatch, "autoplay-interval") == 3
    state.set_active_index("UHF", 3)
    assert _call_navigate(dash_app, state, monkeypatch, "autoplay-interval") == 0


def test_next_button_also_skips_filtered_signals(app_and_state, monkeypatch):
    dash_app, state = app_and_state
    state.apply_filter("UHF", np.array([1, 2]))
    state.set_active_index("UHF", 0)
    assert _call_navigate(dash_app, state, monkeypatch, "btn-next") == 3
    # A diferencia del auto-play, el botón manual se queda en el extremo.
    state.set_active_index("UHF", 3)
    assert _call_navigate(dash_app, state, monkeypatch, "btn-next") == 3


def test_autoplay_tick_with_a_single_active_signal_prevents_update(app_and_state, monkeypatch):
    dash_app, state = app_and_state
    state.apply_filter("UHF", np.array([0, 1, 2]))  # solo queda activa la 3
    state.set_active_index("UHF", 3)
    with pytest.raises(PreventUpdate):
        _call_navigate(dash_app, state, monkeypatch, "autoplay-interval")
