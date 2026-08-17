"""Gráfica tipo #3 — Evolución de métricas.

El PROMPT maestro §5.3 (``archivos_md/PROMPT_Analizador_Señales_UHF_AE.md:185``)
prohibía originalmente dibujar líneas de tendencia o cualquier elemento derivado
("scatter puro"). Ese criterio queda **superado** por
``archivos_md/prompt-mejora-graficas.md`` (Tarea 2), que exige lo contrario: unir los
puntos con una línea para poder evaluar tendencia. La decisión, documentada en la nota
de entrega correspondiente, es que el requerimiento más reciente prevalece; el PROMPT
maestro no se edita, queda como bitácora histórica de la decisión anterior.
"""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go

from core.models import EventSeries
from ui.components.event_lines import build_event_line_shapes
from ui.components.time_axis import TIME_AXIS_TITLE, to_elapsed_minutes
from utils.profiling import stage
from viz.smoothing import SmoothingSpec, smooth, split_on_gaps

POINT_COLOR = "#4A7BB0"
PARTIAL_COLOR = "#C2A83E"
EVENT_COLOR = "#D14343"
TREND_COLOR = "#39A0A0"  # mismo tono que HUMIDITY_COLOR en graph_timeseries.py

# Régimen puntual con suavizado activo: los puntos crudos bajan de opacidad para que la
# línea de tendencia lea como elemento principal, sin perder la dispersión real de
# fondo (archivos_md/prompt-mejora-graficas.md §3.2: rango sugerido 0.25-0.35).
RAW_POINT_OPACITY_DIMMED = 0.30
RAW_POINT_SIZE_DIMMED = 4
RAW_POINT_SIZE_DEFAULT = 6


def build_metric_figure(
    timestamps: np.ndarray,
    values: np.ndarray,
    events: EventSeries,
    t0: float,
    label: str,
    unit: str = "",
    is_partial: np.ndarray | None = None,
    x_range: tuple[float, float] | None = None,
    show_events: bool = True,
    connect_points: bool = False,
    smoothing: SmoothingSpec | None = None,
    gap_threshold: float | None = None,
    uirevision: str | None = None,
) -> go.Figure:
    """Puntos de métrica (puntual o de grupo), con unión y suavizado de presentación
    opcionales -- ver ``viz/smoothing.py`` para la implementación pura reutilizada por
    ambos regímenes.

    ``connect_points``: une los puntos crudos con una línea directa. Pensado para
    régimen de grupo, donde la agregación previa ya redujo el ruido (§3.3); en régimen
    puntual se deja en ``False`` porque la unión directa de miles de puntos sueltos
    produce una sierra sin información (§3.1) -- ahí la única línea es la suavizada.

    ``smoothing``: si no es ``None``, superpone una línea de tendencia (media o
    mediana móvil, según ``smoothing.method``) por encima de los puntos/línea
    crudos. Reutiliza siempre :func:`viz.smoothing.smooth`, nunca una implementación
    paralela (§3.3: "reutilizar la misma implementación de suavizado, sin duplicar
    lógica").

    ``gap_threshold``: umbral (minutos transcurridos) por encima del cual una línea se
    corta en vez de unir a través de un hueco de adquisición real. ``None`` resuelve un
    umbral automático a partir del propio espaciado de los datos
    (:func:`viz.smoothing.auto_gap_threshold`).

    ``uirevision``: estable frente a redibujados que no deban perder el zoom del
    usuario (p. ej. conmutar la visibilidad de eventos); cambia cuando cambia el
    dataset, que es cuando el zoom debe reiniciarse.

    Los puntos de un grupo parcial (``is_partial=True``, ver ``core/grouping.py``) se
    marcan con otro color -- información propia del dato, no un elemento derivado.

    El eje horizontal se muestra en minutos transcurridos desde ``t0`` (misma
    referencia que la gráfica tipo #1, ``ui/state.py::compute_t0``), no en timestamp
    UNIX crudo.
    """
    with stage("render.grafica3", metrica=label) as ctx:
        fig = go.Figure()
        x = to_elapsed_minutes(timestamps, t0)
        ctx["n_puntos"] = int(x.shape[0])

        if x.shape[0] > 0:
            dim_markers = smoothing is not None and not connect_points

            if connect_points:
                x_line, y_line = split_on_gaps(x, values, gap_threshold)
                fig.add_trace(
                    go.Scatter(
                        x=x_line, y=y_line, mode="lines",
                        line=dict(color=POINT_COLOR, width=1.5),
                        name=label, hoverinfo="skip",
                    )
                )

            colors: str | np.ndarray = POINT_COLOR
            if is_partial is not None:
                colors = np.where(is_partial, PARTIAL_COLOR, POINT_COLOR)
            marker_size = RAW_POINT_SIZE_DIMMED if dim_markers else RAW_POINT_SIZE_DEFAULT
            fig.add_trace(
                go.Scatter(
                    x=x, y=values, mode="markers",
                    marker=dict(size=marker_size, color=colors),
                    opacity=RAW_POINT_OPACITY_DIMMED if dim_markers else 1.0,
                    name=label,
                )
            )

            if smoothing is not None:
                x_smooth, y_smooth = smooth(x, values, method=smoothing.method, window=smoothing.window)
                x_smooth, y_smooth = split_on_gaps(x_smooth, y_smooth, gap_threshold)
                fig.add_trace(
                    go.Scatter(
                        x=x_smooth, y=y_smooth, mode="lines",
                        line=dict(color=TREND_COLOR, width=2.5),
                        name=f"{label} (tendencia)", hoverinfo="skip",
                    )
                )

        y_title = f"{label} ({unit})" if unit else label
        xaxis_kwargs: dict[str, object] = dict(title=TIME_AXIS_TITLE)
        if x_range is not None:
            # Ya debe venir en minutos transcurridos, mismo t0 que la gráfica tipo #1
            # ("mismo dominio y rango") -- hoy sin caller que lo pase.
            xaxis_kwargs["range"] = list(x_range)

        fig.update_layout(
            shapes=build_event_line_shapes(events, t0, EVENT_COLOR, visible=show_events),
            xaxis=xaxis_kwargs,
            yaxis=dict(title=y_title),
            margin=dict(l=60, r=20, t=30, b=40),
            height=280,
            showlegend=False,
            uirevision=uirevision,
        )
        return fig
