"""Gráfica tipo #1 — Serie temporal global + variables ambientales (PROMPT §5.1)."""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go

from core.models import EnvironmentalSeries, EventSeries, SensorConfig, SignalBlock
from ui.components.event_lines import build_event_line_shapes
from ui.components.time_axis import TIME_AXIS_TITLE, to_elapsed_minutes
from utils.profiling import stage
from viz.decimation import build_vertical_segments

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
) -> go.Figure:
    """Envolvente min/max de **todas** las señales + ambientales en eje secundario + eventos.

    El par (min, max) por señal ya viene precalculado desde la ingesta (Fase 1,
    ``SignalBlock.minmax``) -- nunca se recalcula aquí (PROMPT §5.1: "nunca se recalcula
    en tiempo de render").

    **Sin diezmado**: se dibuja un segmento vertical por cada señal activa, aunque sean
    decenas de miles -- ningún punto se agrega ni se descarta, así que lo que se ve en
    pantalla es el conjunto completo y cada segmento corresponde a una señal real. Por
    eso la traza es ``Scattergl`` (WebGL) y no ``Scatter`` (SVG): con ~2×10⁴ señales el
    renderizador SVG tendría que crear ~6×10⁴ elementos DOM.

    El eje horizontal se muestra en minutos transcurridos desde ``t0`` (referencia común
    del experimento, ``ui/state.py::compute_t0``), no en timestamp UNIX crudo.

    ``active_mask`` (Fase 6, PROMPT §7): máscara de filtrado interactivo del usuario,
    combinada con ``valid_mask`` -- las señales excluidas desaparecen de la envolvente,
    igual que las de metadato inválido. Ambientales/eventos no son "señales" y no se ven
    afectados.
    """
    with stage("render.grafica1", sensor=sensor_config.name) as ctx:
        fig = go.Figure()

        valid = block.valid_mask & active_mask
        ctx["n_senales"] = int(valid.sum())
        if valid.any():
            t = to_elapsed_minutes(block.timestamps[valid], t0)
            mm = block.minmax[valid]
            xs, ys = build_vertical_segments(t, mm[:, 0], mm[:, 1])
            fig.add_trace(
                go.Scattergl(
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

        fig.update_layout(
            shapes=build_event_line_shapes(events, t0, EVENT_COLOR),
            xaxis=dict(title=TIME_AXIS_TITLE),
            yaxis=dict(title=f"Amplitud {sensor_config.name} (cruda)"),
            yaxis2=dict(title="Temp. (°C) / Humedad (%)", overlaying="y", side="right"),
            legend=dict(orientation="h", y=1.08),
            margin=dict(l=60, r=60, t=30, b=40),
            height=280,
        )
        return fig
