"""Líneas verticales de eventos (disparos/SHOT) compartidas por las gráficas #1 y #3.

Se construyen como una lista de *shapes* que el llamador pasa de una sola vez a
``fig.update_layout(shapes=...)``, en vez de llamar ``fig.add_vline`` una vez por
evento: ``add_vline`` revalida la figura entera en cada llamada, así que el coste crece
con el cuadrado del número de eventos (medido con los 98 eventos del dataset real:
~4,8 s en el bucle contra ~1 ms construyendo la lista y asignándola una vez).
"""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go

from core.models import EventSeries
from ui.components.time_axis import to_elapsed_minutes

EVENT_COLOR = "#D14343"


def build_event_lines_trace(
    events: EventSeries,
    t0: float,
    y_min: float = 0.0,
    y_max: float = 1.0,
    color: str = EVENT_COLOR,
    visible: bool = True,
) -> go.Scatter | None:
    """Traza única con separadores NaN que dibuja todas las líneas de evento (Fase 1, H3).

    Sustituye a N `layout.shapes` individuales (que creaban N nodos en el DOM y
    penalizaban el hover en ~3-5 ms) por una sola traza SVG `mode="lines"`.

    Cada evento aporta un segmento vertical [y_min, y_max, NaN].
    """
    if events.timestamps.shape[0] == 0:
        return None
    t_ev = to_elapsed_minutes(events.timestamps, t0)
    n = t_ev.shape[0]
    xs = np.full(3 * n, np.nan, dtype=np.float64)
    ys = np.full(3 * n, np.nan, dtype=np.float64)
    xs[0::3] = t_ev
    xs[1::3] = t_ev
    ys[0::3] = y_min
    ys[1::3] = y_max
    return go.Scatter(
        x=xs,
        y=ys,
        mode="lines",
        line=dict(color=color, dash="dash", width=1),
        name="Eventos",
        hoverinfo="skip",
        showlegend=False,
        visible=visible,
        yaxis="y1",
    )


def build_event_line_shapes(
    events: EventSeries, t0: float, color: str = EVENT_COLOR, visible: bool = True
) -> list[dict]:
    """Shapes de línea vertical (una por evento) en minutos transcurridos desde ``t0``.

    ``yref="y domain"`` con ``y0=0``/``y1=1`` reproduce exactamente lo que hacía
    ``add_vline``: la línea cruza todo el alto del área de dibujo, independiente de la
    escala del eje Y (y sin arrastrar el autorango).

    ``visible=False`` es el punto único de corte del control "Mostrar eventos": los
    eventos son shapes de layout puros (sin traza, sin entrada de leyenda, sin
    anotación), así que devolver la lista vacía los oculta por completo con un único
    ``if``, sin tener que tocar cada gráfica que los consume.
    """
    if not visible:
        return []
    return [
        dict(
            type="line", xref="x", yref="y domain",
            x0=float(t_ev), x1=float(t_ev), y0=0, y1=1,
            line=dict(color=color, dash="dash", width=1),
        )
        for t_ev in to_elapsed_minutes(events.timestamps, t0)
    ]
