"""Gráfica tipo #1 sin diezmado (Fase 7) y orden vertical del panel central.

La regresión que cuidan estas pruebas es que la envolvente vuelva a agregar señales en
bins de píxel: el requisito es ver **todas** las señales, por muchas que sean.
"""
import numpy as np

from core.models import EnvironmentalSeries, EventSeries, SensorConfig, SignalBlock
from ui.components.graph_timeseries import build_timeseries_figure
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


def test_event_lines_are_batched_shapes_not_add_vline():
    n = 100
    block = _block(n)
    env, _ = _empty_environment()
    t0 = float(block.timestamps[0])
    events = EventSeries(
        timestamps=block.timestamps[[10, 20, 30]],
        event_type=np.array(["SHOT"] * 3, dtype=object),
    )
    fig = build_timeseries_figure(UHF_CONFIG, block, env, events, t0, np.ones(n, dtype=bool))
    shapes = fig.layout.shapes
    assert len(shapes) == 3
    assert [s.x0 for s in shapes] == [(block.timestamps[i] - t0) / 60.0 for i in (10, 20, 30)]
    assert all(s.type == "line" and s.yref == "y domain" for s in shapes)


def test_show_events_false_yields_no_shapes():
    # Conmutar la visibilidad de eventos no debe requerir volver a llamar a esta
    # función con datos distintos -- basta con show_events=False para vaciar las
    # shapes (archivos_md/prompt-mejora-graficas.md §2).
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
    assert len(fig_on.layout.shapes) == 3
    assert len(fig_off.layout.shapes) == 0


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
