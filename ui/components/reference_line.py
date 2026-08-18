"""Shape y anotación de la línea horizontal de referencia de las gráficas #3
(``archivos_md/prompt-linea-referencia.md`` §2.3). Constructor puro de diccionarios de
layout de Plotly -- el cálculo del valor vive en ``viz/reference_line.py``, este módulo
no calcula nada, mismo reparto de responsabilidades que
``ui/components/event_lines.py``.

Se dibuja como **shape** de layout, no como traza: ``xref="paper"`` la extiende a lo
ancho de todo el lienzo de forma estable frente a zoom y paneo (una traza con extremos
en coordenadas de datos se quedaría corta al desplazar la vista), y ``layer="below"`` la
deja detrás de la serie principal sin depender del orden de inserción de trazas. Como es
un shape, es parcheable con ``dash.Patch`` -- mismo mecanismo que ya usa
``ui/callbacks/sensor_window_callbacks.py::_on_toggle_events`` para los eventos, sin
tocar ninguna traza ni pasar por el caché.

Color: ninguno de los tonos ya definidos en el proyecto es un color "auxiliar" o "de
referencia" -- todos están atados a una serie concreta (puntos, parcial, evento,
tendencia). Se introduce un gris neutro dedicado, que no compite con ninguna de esas
series.
"""
from __future__ import annotations

REFERENCE_LINE_COLOR = "#7A7A7A"
REFERENCE_LINE_OPACITY = 0.5


def build_reference_shape(value: float, color: str = REFERENCE_LINE_COLOR, opacity: float = REFERENCE_LINE_OPACITY) -> dict:
    """Línea horizontal punteada y semitransparente a lo ancho de todo el lienzo, por
    detrás de la serie principal (criterio de aceptación 4)."""
    return dict(
        type="line", xref="paper", yref="y",
        x0=0, x1=1, y0=value, y1=value,
        line=dict(color=color, width=1.5, dash="dot"),
        opacity=opacity,
        layer="below",
    )


def build_reference_annotation(
    value: float, unit: str = "", color: str = REFERENCE_LINE_COLOR
) -> dict:
    """Etiqueta con el valor numérico del promedio (criterio de aceptación 5). La
    figura de la gráfica #3 tiene ``showlegend=False`` (``ui/components/
    graph_metric.py``), así que la leyenda no es una opción para mostrar este valor."""
    unit_suffix = f" {unit}" if unit else ""
    return dict(
        xref="paper", yref="y domain",
        x=1, y=1, xanchor="right", yanchor="top",
        text=f"Referencia: {value:.6g}{unit_suffix}",
        showarrow=False,
        font=dict(color=color, size=11),
        bgcolor="rgba(255,255,255,0.7)",
    )


def build_reference_message_annotation(message: str, color: str = REFERENCE_LINE_COLOR) -> dict:
    """Anotación textual para los casos borde del §2.2 (``t <= 0`` o sin muestras en el
    intervalo): informa la situación sin dibujar la línea y sin bloquear el resto de la
    gráfica."""
    return dict(
        xref="paper", yref="y domain",
        x=1, y=1, xanchor="right", yanchor="top",
        text=message,
        showarrow=False,
        font=dict(color=color, size=11),
        bgcolor="rgba(255,255,255,0.7)",
    )
