"""Gráfica tipo #1 — Serie temporal global + variables ambientales (PROMPT §5.1)."""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go

from core.models import EnvironmentalSeries, EventSeries, SensorConfig, SignalBlock
from ui.components.time_axis import TIME_AXIS_TITLE, to_elapsed_minutes
from viz.decimation import build_vertical_segments, decimate_minmax_by_pixel

SIGNAL_COLOR = "#4A7BB0"
TEMPERATURE_COLOR = "#E07B39"
HUMIDITY_COLOR = "#39A0A0"
EVENT_COLOR = "#D14343"


def build_timeseries_figure(
    sensor_config: SensorConfig,
    block: SignalBlock,
    environmental: EnvironmentalSeries,
    events: EventSeries,
    t0: float,
    active_mask: np.ndarray,
    n_pixels: int = 1600,
) -> go.Figure:
    """Envolvente min/max diezmada por señal + ambientales en eje secundario + eventos.

    El par (min, max) por señal ya viene precalculado desde la ingesta (Fase 1,
    ``SignalBlock.minmax``) -- nunca se recalcula aquí, solo se diezma por píxel si hace
    falta (PROMPT §5.1: "nunca se recalcula en tiempo de render").

    El eje horizontal se muestra en minutos transcurridos desde ``t0`` (referencia común
    del experimento, ``ui/state.py::compute_t0``), no en timestamp UNIX crudo -- la
    conversión ocurre aquí, a la entrada, así que el diezmado por píxel (afín-invariante,
    ``viz/decimation.py``) produce bins idénticos a los que daría diezmar en segundos.

    ``active_mask`` (Fase 6, PROMPT §7): máscara de filtrado interactivo del usuario,
    combinada con ``valid_mask`` -- las señales excluidas desaparecen de la envolvente,
    igual que las de metadato inválido. Ambientales/eventos no son "señales" y no se ven
    afectados.
    """
    fig = go.Figure()

    valid = block.valid_mask & active_mask
    if valid.any():
        t = to_elapsed_minutes(block.timestamps[valid], t0)
        mm = block.minmax[valid]
        t_dec, y_min, y_max = decimate_minmax_by_pixel(t, mm, n_pixels)
        xs, ys = build_vertical_segments(t_dec, y_min, y_max)
        fig.add_trace(
            go.Scatter(
                x=xs, y=ys, mode="lines+markers",
                line=dict(color=SIGNAL_COLOR, width=1), marker=dict(size=3, color=SIGNAL_COLOR),
                name=f"Señal {sensor_config.name} (envolvente)", yaxis="y1",
            )
        )

    if environmental.timestamps.shape[0] > 0:
        t_env = to_elapsed_minutes(environmental.timestamps, t0)
        fig.add_trace(
            go.Scatter(
                x=t_env, y=environmental.temperature, mode="lines",
                name="Temperatura (°C)", line=dict(color=TEMPERATURE_COLOR), yaxis="y2",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=t_env, y=environmental.humidity, mode="lines",
                name="Humedad (%)", line=dict(color=HUMIDITY_COLOR, dash="dot"), yaxis="y2",
            )
        )

    for t_ev in to_elapsed_minutes(events.timestamps, t0):
        fig.add_vline(x=float(t_ev), line=dict(color=EVENT_COLOR, dash="dash", width=1))

    fig.update_layout(
        xaxis=dict(title=TIME_AXIS_TITLE),
        yaxis=dict(title=f"Amplitud {sensor_config.name} (cruda)"),
        yaxis2=dict(title="Temp. (°C) / Humedad (%)", overlaying="y", side="right"),
        legend=dict(orientation="h", y=1.08),
        margin=dict(l=60, r=60, t=30, b=40),
        height=280,
    )
    return fig
