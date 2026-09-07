"""Gráfica tipo #1 sin diezmado (Fase 7) y orden vertical del panel central.

La regresión que cuidan estas pruebas es que la envolvente vuelva a agregar señales en
bins de píxel: el requisito es ver **todas** las señales, por muchas que sean.
"""
import numpy as np

from core.models import EnvironmentalSeries, EventSeries, SensorConfig, SignalBlock
from ui.components.graph_timeseries import EMPTY_ACTIVE_SET_MESSAGE, SELECTION_ANCHOR_NAME, build_timeseries_figure
from ui.components.sensor_window import build_sensor_window_layout

UHF_CONFIG = SensorConfig(
    name="UHF", hdf5_group="signals", fs_hz=1e9, n_samples=8, freq_limit_hz=5e8,
    axis_unit="us", axis_scale=1e6, target_block_bytes=1 << 20,
)


def _block(n: int) -> SignalBlock:
    return SignalBlock(
        data=np.zeros((n, UHF_CONFIG.n_samples), dtype=np.float32),
        timestamps=np.linspace(1_000.0, 1_000.0 + 60 * n, n),
        trigger=np.full(n, 0.02),
        vrange=np.full(n, 0.5),
        minmax=np.column_stack([-np.arange(n, dtype=np.float64), np.arange(n, dtype=np.float64)]),
        valid_mask=np.ones(n, dtype=bool),
    )


def _empty_environment():
    env = EnvironmentalSeries(timestamps=np.array([]), temperature=np.array([]), humidity=np.array([]))
    events = EventSeries(timestamps=np.array([]), event_type=np.array([], dtype=object))
    return env, events


def _envelope_figure(block: SignalBlock, active_mask: np.ndarray):
    env, events = _empty_environment()
    fig = build_timeseries_figure(UHF_CONFIG, block, env, events, float(block.timestamps[0]), active_mask)
    return fig.data[0]


def test_envelope_plots_every_signal_without_decimating():
    # Muy por encima del antiguo tope de 1600 píxeles: antes se agregaba en bins,
    # ahora cada señal debe aportar su propio segmento vertical (min, max, separador).
    n = 12_000
    block = _block(n)
    trace = _envelope_figure(block, np.ones(n, dtype=bool))
    assert len(trace.x) == 3 * n
    assert len(trace.y) == 3 * n


def test_envelope_preserves_exact_minmax_of_each_signal():
    n = 5_000
    block = _block(n)
    trace = _envelope_figure(block, np.ones(n, dtype=bool))
    # Sin agregación, el par (min, max) de la señal i está en las posiciones 3i y 3i+1.
    for i in (0, 1234, n - 1):
        assert trace.y[3 * i] == block.minmax[i, 0]
        assert trace.y[3 * i + 1] == block.minmax[i, 1]


def test_event_lines_are_single_collapsed_trace_not_shapes():
    n = 100
    block = _block(n)
    env, _ = _empty_environment()
    t0 = float(block.timestamps[0])
    events = EventSeries(
        timestamps=block.timestamps[[10, 20, 30]],
        event_type=np.array(["SHOT"] * 3, dtype=object),
    )
    fig = build_timeseries_figure(UHF_CONFIG, block, env, events, t0, np.ones(n, dtype=bool))
    assert len(fig.layout.shapes or ()) == 0
    traces = {tr.name: tr for tr in fig.data}
    assert "Eventos" in traces
    ev_tr = traces["Eventos"]
    assert ev_tr.mode == "lines"
    assert ev_tr.hoverinfo == "skip"
    assert ev_tr.showlegend is False
    assert len(ev_tr.x) == 3 * 3
    # Separador NaN cada 3 elementos
    assert np.isnan(ev_tr.x[2]) and np.isnan(ev_tr.x[5]) and np.isnan(ev_tr.x[8])
    expected_times = [(block.timestamps[i] - t0) / 60.0 for i in (10, 20, 30)]
    assert ev_tr.x[0] == expected_times[0] and ev_tr.x[3] == expected_times[1] and ev_tr.x[6] == expected_times[2]
    # Posicionado después de la envolvente (curveNumber > 0)
    trace_names = [tr.name for tr in fig.data]
    assert trace_names.index("Eventos") > 0


def test_show_events_controls_event_trace_visibility():
    n = 100
    block = _block(n)
    env, _ = _empty_environment()
    t0 = float(block.timestamps[0])
    events = EventSeries(
        timestamps=block.timestamps[[10, 20, 30]],
        event_type=np.array(["SHOT"] * 3, dtype=object),
    )
    fig_on = build_timeseries_figure(UHF_CONFIG, block, env, events, t0, np.ones(n, dtype=bool), show_events=True)
    fig_off = build_timeseries_figure(UHF_CONFIG, block, env, events, t0, np.ones(n, dtype=bool), show_events=False)
    ev_on = next(tr for tr in fig_on.data if tr.name == "Eventos")
    ev_off = next(tr for tr in fig_off.data if tr.name == "Eventos")
    assert ev_on.visible is True
    assert ev_off.visible is False
    assert len(fig_on.layout.shapes or ()) == 0
    assert len(fig_off.layout.shapes or ()) == 0


def test_ambient_curves_decimated_when_points_exceed_2000():
    n = 10
    block = _block(n)
    t0 = float(block.timestamps[0])
    k = 5000
    t_env = np.linspace(1000.0, 5000.0, k)
    temp = np.full(k, 22.0)
    temp[2500] = 45.0  # Extremo aislado que debe preservarse
    temp[1000] = 10.0
    hum = np.full(k, 50.0)
    env = EnvironmentalSeries(timestamps=t_env, temperature=temp, humidity=hum)
    events = EventSeries(timestamps=np.array([]), event_type=np.array([], dtype=object))
    fig = build_timeseries_figure(UHF_CONFIG, block, env, events, t0, np.ones(n, dtype=bool))
    traces = {tr.name: tr for tr in fig.data}
    temp_tr = traces["Temperatura (°C)"]
    hum_tr = traces["Humedad (%)"]
    assert len(temp_tr.x) <= 2000
    assert len(hum_tr.x) <= 2000
    assert np.nanmax(temp_tr.y) >= 30.0  # El pico queda representado en el bin
    assert "Temp = %{y:.1f} °C" in temp_tr.hovertemplate


def test_ambient_curves_not_decimated_when_points_under_2000():
    n = 10
    block = _block(n)
    t0 = float(block.timestamps[0])
    k = 500
    t_env = np.linspace(1000.0, 2000.0, k)
    env = EnvironmentalSeries(timestamps=t_env, temperature=np.full(k, 25.0), humidity=np.full(k, 55.0))
    events = EventSeries(timestamps=np.array([]), event_type=np.array([], dtype=object))
    fig = build_timeseries_figure(UHF_CONFIG, block, env, events, t0, np.ones(n, dtype=bool))
    traces = {tr.name: tr for tr in fig.data}
    assert len(traces["Temperatura (°C)"].x) == 500


def test_signal_envelope_zero_decimation_invariant_with_ambient_and_events():
    n = 2500
    block = _block(n)
    t0 = float(block.timestamps[0])
    k = 4000
    env = EnvironmentalSeries(
        timestamps=np.linspace(1000.0, 5000.0, k),
        temperature=np.full(k, 20.0),
        humidity=np.full(k, 60.0),
    )
    events = EventSeries(
        timestamps=block.timestamps[:20],
        event_type=np.array(["SHOT"] * 20, dtype=object),
    )
    fig = build_timeseries_figure(UHF_CONFIG, block, env, events, t0, np.ones(n, dtype=bool))
    # Envolvente: exacta, 3 * n puntos, traza 0
    env_trace = fig.data[0]
    assert env_trace.name == "Señal UHF (envolvente)"
    assert len(env_trace.x) == 3 * n
    assert len(env_trace.y) == 3 * n
    # Ambientales diezmadas <= 2000
    temp_trace = fig.data[1]
    hum_trace = fig.data[2]
    assert len(temp_trace.x) <= 2000
    assert len(hum_trace.x) <= 2000
    # Eventos: 3 * 20 puntos
    event_trace = fig.data[3]
    assert event_trace.name == "Eventos"
    assert len(event_trace.x) == 3 * 20


def test_uirevision_is_passed_through_to_layout():
    n = 10
    block = _block(n)
    env, events = _empty_environment()
    t0 = float(block.timestamps[0])
    fig = build_timeseries_figure(
        UHF_CONFIG, block, env, events, t0, np.ones(n, dtype=bool), uirevision="UHF|dataset-1"
    )
    assert fig.layout.uirevision == "UHF|dataset-1"


def test_envelope_draws_only_active_and_valid_signals():
    n = 4_000
    block = _block(n)
    block.valid_mask[:100] = False
    active = np.ones(n, dtype=bool)
    active[-500:] = False
    trace = _envelope_figure(block, active)
    assert len(trace.x) == 3 * (n - 100 - 500)


def test_panel_central_order_signal_then_envelope_then_metrics():
    layout = build_sensor_window_layout("UHF")
    panel_central = next(child for child in layout.children if getattr(child, "className", None) == "panel-central")
    ids = [getattr(c, "id", None) or getattr(c, "className", None) for c in panel_central.children]
    assert ids.index("graph-signal") < ids.index("graph-timeseries") < ids.index("metrics-graphs-container")


def test_envelope_trace_is_excluded_from_hover():
    # Es LA corrección del congelamiento de la pestaña: por debajo de TOO_MANY_POINTS
    # (1e5) Plotly no indexa la traza y recorre sus 61 722 puntos en cada evento de
    # hover, cada 50 ms. Medido sobre la figura real: 22,5 ms sin skip contra 3,3 ms
    # con skip. Si esta prueba falla, la página vuelve a bloquearse al pasar el ratón
    # -- no se revierte sin volver a medir (ver docs/RENDIMIENTO.md §1.2).
    n = 100
    block = _block(n)
    trace = _envelope_figure(block, np.ones(n, dtype=bool))
    assert trace.hoverinfo == "skip"


def test_selection_anchor_trace_keeps_lasso_available():
    # Plotly retira select2d/lasso2d de la barra si NINGUNA traza tiene marcadores
    # (isSelectable, components/modebar/manage.js), y las cuatro trazas de esta gráfica
    # son de líneas. Sin esta traza ancla no hay forma de invocar el lazo desde la
    # interfaz, por mucho que el filtrado del servidor siga intacto.
    n = 100
    block = _block(n)
    env, events = _empty_environment()
    t0 = float(block.timestamps[0])
    fig = build_timeseries_figure(UHF_CONFIG, block, env, events, t0, np.ones(n, dtype=bool))

    anchor = fig.data[-1]
    assert anchor.name == SELECTION_ANCHOR_NAME
    assert "markers" in anchor.mode
    # Invisible y gratuita: no se ve, no cuenta en la leyenda y no entra al hover.
    assert anchor.marker.opacity == 0
    assert anchor.showlegend is False
    assert anchor.hoverinfo == "skip"
    # Un solo punto, y dentro del rango de los datos para no arrastrar el autorango.
    assert len(anchor.x) == 1
    assert anchor.x[0] == 0.0                      # primera señal activa, t0
    assert anchor.y[0] == block.minmax[0, 0]


def test_selection_anchor_goes_last_and_preserves_curve_numbers():
    # ui/callbacks/filtering.py resuelve por curveNumber: el ancla no puede desplazar
    # a la envolvente (0) ni a ninguna otra traza existente.
    n = 100
    block = _block(n)
    t0 = float(block.timestamps[0])
    env = EnvironmentalSeries(
        timestamps=np.array([1000.0, 1060.0]),
        temperature=np.array([22.5, 23.0]),
        humidity=np.array([45.0, 46.5]),
    )
    events = EventSeries(
        timestamps=block.timestamps[[10, 20]], event_type=np.array(["SHOT"] * 2, dtype=object)
    )
    fig = build_timeseries_figure(UHF_CONFIG, block, env, events, t0, np.ones(n, dtype=bool))
    names = [tr.name for tr in fig.data]
    assert names == [
        "Señal UHF (envolvente)",
        "Temperatura (°C)",
        "Humedad (%)",
        "Eventos",
        SELECTION_ANCHOR_NAME,
    ]


def test_layout_enables_clickanywhere_and_closest_hover():
    n = 10
    block = _block(n)
    env, events = _empty_environment()
    t0 = float(block.timestamps[0])
    fig = build_timeseries_figure(UHF_CONFIG, block, env, events, t0, np.ones(n, dtype=bool))
    assert fig.layout.clickanywhere is True
    assert fig.layout.hovermode == "closest"


def test_environmental_traces_have_informative_hovertemplate():
    n = 10
    block = _block(n)
    t0 = float(block.timestamps[0])
    env = EnvironmentalSeries(
        timestamps=np.array([1000.0, 1060.0]),
        temperature=np.array([22.5, 23.0]),
        humidity=np.array([45.0, 46.5]),
    )
    events = EventSeries(timestamps=np.array([]), event_type=np.array([], dtype=object))
    fig = build_timeseries_figure(UHF_CONFIG, block, env, events, t0, np.ones(n, dtype=bool))
    traces = {tr.name: tr for tr in fig.data}
    assert "Temperatura (°C)" in traces
    assert "Humedad (%)" in traces
    assert "Temp = %{y:.1f} °C" in traces["Temperatura (°C)"].hovertemplate
    assert "<extra></extra>" in traces["Temperatura (°C)"].hovertemplate
    assert "Humedad = %{y:.1f} %" in traces["Humedad (%)"].hovertemplate
    assert "<extra></extra>" in traces["Humedad (%)"].hovertemplate


def test_timeseries_figure_with_no_active_signals_omits_envelope_trace():
    n = 10
    block = _block(n)
    t0 = float(block.timestamps[0])
    env = EnvironmentalSeries(
        timestamps=np.array([1000.0]),
        temperature=np.array([22.5]),
        humidity=np.array([45.0]),
    )
    events = EventSeries(timestamps=np.array([]), event_type=np.array([], dtype=object))
    fig = build_timeseries_figure(UHF_CONFIG, block, env, events, t0, np.zeros(n, dtype=bool))
    assert not any("envolvente" in (tr.name or "") for tr in fig.data)
    # Temperatura, humedad y traza ancla invisible preservada para modebar lasso
    assert len(fig.data) == 3
    assert fig.data[-1].name == SELECTION_ANCHOR_NAME
    assert any(EMPTY_ACTIVE_SET_MESSAGE in str(ann.text) for ann in fig.layout.annotations)


def test_envelope_trace_mode_is_lines_without_markers():
    # Sin marcadores no hay 3*N nodos de marcador que dibujar ni que serializar, y la
    # selección se resuelve igual en el servidor por geometría. Quien mantiene el lazo
    # disponible pese a esto es la traza ancla, no la envolvente
    # (test_selection_anchor_trace_keeps_lasso_available).
    n = 50
    block = _block(n)
    trace = _envelope_figure(block, np.ones(n, dtype=bool))
    assert trace.mode == "lines"
    assert getattr(trace, "marker", None) is None or getattr(trace.marker, "size", None) is None


def test_envelope_trace_uses_float32_transport_encoding():
    # Fase 3 / R2: La envolvente emite arrays np.float32 para transporte ligero base64 'f4'
    n = 50
    block = _block(n)
    trace = _envelope_figure(block, np.ones(n, dtype=bool))
    assert isinstance(trace.x, np.ndarray)
    assert isinstance(trace.y, np.ndarray)
    assert trace.x.dtype == np.float32
    assert trace.y.dtype == np.float32


def test_envelope_decimates_when_active_signals_exceed_limit():
    from viz.decimation import ENVELOPE_DECIMATION_BINS, ENVELOPE_EXACT_LIMIT
    n = ENVELOPE_EXACT_LIMIT + 100
    ts = np.linspace(1000.0, 5000.0, n)
    trig = np.full(n, 0.02)
    vr = np.full(n, 0.5)
    vm = np.ones(n, dtype=bool)
    mm = np.column_stack([-np.ones(n), np.ones(n)])

    block = SignalBlock(
        data=None,
        timestamps=ts,
        trigger=trig,
        vrange=vr,
        valid_mask=vm,
        minmax=mm,
        n_samples=UHF_CONFIG.n_samples,
    )
    env, events = _empty_environment()
    fig = build_timeseries_figure(UHF_CONFIG, block, env, events, float(ts[0]), np.ones(n, dtype=bool))
    env_trace = fig.data[0]
    assert len(env_trace.x) <= 3 * ENVELOPE_DECIMATION_BINS
    assert len(env_trace.x) < 3 * n
    assert fig.data[-1].name == SELECTION_ANCHOR_NAME



