"""Gráfica tipo #3 — Evolución de métricas (PROMPT §5.3)."""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go

from core.models import EventSeries
from ui.components.event_lines import build_event_line_shapes
from ui.components.time_axis import TIME_AXIS_TITLE, to_elapsed_minutes

POINT_COLOR = "#4A7BB0"
PARTIAL_COLOR = "#C2A83E"
EVENT_COLOR = "#D14343"


def build_metric_figure(
    timestamps: np.ndarray,
    values: np.ndarray,
    events: EventSeries,
    t0: float,
    label: str,
    unit: str = "",
    is_partial: np.ndarray | None = None,
    x_range: tuple[float, float] | None = None,
) -> go.Figure:
    """Scatter puro (PROMPT §5.3: "Prohibido dibujar líneas de tendencia, ajustes,
    regresiones, medias móviles o cualquier elemento derivado. Solo los puntos.").

    Los puntos de un grupo parcial (``is_partial=True``, ver ``core/grouping.py``) se
    marcan con otro color -- información útil para el usuario, no una línea derivada.

    El eje horizontal se muestra en minutos transcurridos desde ``t0`` (misma referencia
    que la gráfica tipo #1, ``ui/state.py::compute_t0``), no en timestamp UNIX crudo.
    """
    fig = go.Figure()
    timestamps = to_elapsed_minutes(timestamps, t0)

    if timestamps.shape[0] > 0:
        colors: str | np.ndarray = POINT_COLOR
        if is_partial is not None:
            colors = np.where(is_partial, PARTIAL_COLOR, POINT_COLOR)
        fig.add_trace(
            go.Scatter(
                x=timestamps, y=values, mode="markers",
                marker=dict(size=6, color=colors), name=label,
            )
        )

    y_title = f"{label} ({unit})" if unit else label
    xaxis_kwargs: dict[str, object] = dict(title=TIME_AXIS_TITLE)
    if x_range is not None:
        # Ya debe venir en minutos transcurridos, mismo t0 que la gráfica tipo #1
        # (PROMPT §5.3: "mismo dominio y rango") -- hoy sin caller que lo pase.
        xaxis_kwargs["range"] = list(x_range)

    fig.update_layout(
        shapes=build_event_line_shapes(events, t0, EVENT_COLOR),
        xaxis=xaxis_kwargs,
        yaxis=dict(title=y_title),
        margin=dict(l=60, r=20, t=30, b=40),
        height=280,
        showlegend=False,
    )
    return fig
