"""Piezas compartidas entre las gráficas #4 (2D) y #5 (3D) -- ``archivos_md/
prompt-mapas2d3d.md``, ``archivos_md/PLAN_MAPAS_2D_3D.md``.

Sin clusters (decisión D1 del plan): un solo color de punto para ambos mapas, el mismo
tono que ya usan la gráfica #2 (``graph_signal.SIGNAL_COLOR``) y la #3
(``graph_metric.POINT_COLOR``), para que los mapas lean como parte de la misma
aplicación. El gris de anotación reutiliza el mismo tono neutro de
``ui/components/reference_line.py`` -- ninguno de los dos representa una serie de datos,
así que comparten el mismo color "informativo".
"""
from __future__ import annotations

import plotly.graph_objects as go

POINT_COLOR = "#4A7BB0"
HIGHLIGHT_COLOR = "#E8871E"
ANNOTATION_COLOR = "#7A7A7A"

POINT_MARKER_SIZE_2D = 6
POINT_MARKER_SIZE_3D = 3
HIGHLIGHT_MARKER_SIZE_2D = 14
HIGHLIGHT_MARKER_SIZE_3D = 6

# §3 del prompt: "si un eje no tiene métrica asignada, el mapa muestra un estado vacío
# informativo, no un error".
AXIS_NOT_ASSIGNED_MESSAGE = "Selecciona una métrica para cada eje."
NO_POINTS_MESSAGE = "Sin señales que graficar con la selección y el filtro actuales."
# §3 del prompt: "se permite repetir la misma métrica en dos ejes, pero muéstralo con
# una advertencia discreta".
DUPLICATE_AXIS_WARNING = "Misma métrica en dos ejes."


def build_omitted_message(omitted_counts: dict[str, int], axis_labels: dict[str, str]) -> str | None:
    """Texto del aviso "N señales sin valor para <métrica>" (§1 del prompt), uno por eje
    con omitidos, o ``None`` si ningún eje tuvo que omitir nada."""
    parts = [
        f"{count} sin valor para {axis_labels[axis]}"
        for axis, count in omitted_counts.items()
        if count > 0
    ]
    return " · ".join(parts) if parts else None


def build_map_info_annotation(lines: list[str], color: str = ANNOTATION_COLOR) -> dict:
    """Anotación informativa en la esquina superior derecha -- funciona igual sobre un
    lienzo 2D que sobre una escena 3D, porque ``xref``/``yref="paper"`` la ancla al
    lienzo completo de la figura, no a ningún eje de datos."""
    return dict(
        xref="paper", yref="paper",
        x=1, y=1, xanchor="right", yanchor="top",
        text="<br>".join(lines),
        showarrow=False,
        align="right",
        font=dict(color=color, size=11),
        bgcolor="rgba(255,255,255,0.7)",
    )


def build_empty_map_figure(message: str = AXIS_NOT_ASSIGNED_MESSAGE, height: int = 420) -> go.Figure:
    """Estado vacío informativo, mostrado cuando el mapa está habilitado pero le falta
    métrica en algún eje -- antes de intentar calcular nada (§3 del prompt)."""
    fig = go.Figure()
    fig.update_layout(
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        height=height,
        margin=dict(l=60, r=20, t=30, b=40),
        annotations=[
            dict(
                xref="paper", yref="paper", x=0.5, y=0.5,
                text=message, showarrow=False,
                font=dict(color=ANNOTATION_COLOR, size=13),
            )
        ],
    )
    return fig
