"""Gráfica #5 -- Mapa de separación 3D (``archivos_md/prompt-mapas2d3d.md``).

Mismo reparto de responsabilidades que ``graph_map_2d.py``: un punto = una señal, tres
métricas puntuales por eje, ya resueltas y alineadas por ``viz/maps.py``. Reutiliza la
misma librería de graficado que el resto del proyecto (Plotly 6.9.0, ``requirements.txt``)
-- ``go.Scatter3d`` es soporte 3D nativo, cero dependencias nuevas.

Navegación (§5.7 del prompt: "zoom, paneo y rotación en 3D, botón de reset de vista"):
la provee el modebar y el arrastre de mouse de toda escena ``scene`` de Plotly por
defecto (rotación con arrastre, zoom con rueda, "Reset camera to default" en el
modebar) -- no requiere código propio.

Decisión D2 (``archivos_md/PLAN_MAPAS_2D_3D.md``): Plotly no ofrece lazo/caja de
selección dentro de una escena 3D, así que este mapa es de solo lectura para
navegación (click, hover, órbita) -- no alimenta el filtrado. Esa responsabilidad queda
únicamente en el mapa 2D (#4).
"""
from __future__ import annotations

import plotly.graph_objects as go

from ui.components.graph_map_common import (
    DUPLICATE_AXIS_WARNING,
    HIGHLIGHT_COLOR,
    HIGHLIGHT_MARKER_SIZE_3D,
    NO_POINTS_MESSAGE,
    POINT_COLOR,
    POINT_MARKER_SIZE_3D,
    build_map_info_annotation,
    build_omitted_message,
)
from viz.maps import MapDataset, resolve_map_highlight_coords

# Misma convención que ``graph_map_2d.HIGHLIGHT_TRACE_INDEX``: la traza de resaltado es
# SIEMPRE la segunda (índice 1), para que el parche ligero de navegación
# (``_on_refresh_map_highlight``) no tenga que buscarla por nombre.
HIGHLIGHT_TRACE_INDEX = 1

MAP_3D_HEIGHT = 480


def build_map_3d_figure(
    dataset: MapDataset,
    same_metric_warning: bool = False,
    selected_signal_index: int | None = None,
    uirevision: str | None = None,
) -> go.Figure:
    """Scatter 3D sobre ``dataset.coords["x"]``/``["y"]``/``["z"]`` -- mismo contrato de
    entrada que :func:`ui.components.graph_map_2d.build_map_2d_figure`, con
    ``metric_ids={"x":..., "y":..., "z":...}`` al construir el ``dataset``.

    ``selected_signal_index``: la traza de resaltado (segunda traza,
    ``HIGHLIGHT_TRACE_INDEX``) SIEMPRE está presente -- vacía o con un punto, ver
    docstring de :func:`ui.components.graph_map_2d.build_map_2d_figure`.

    ``same_metric_warning``: mismo significado que en el mapa 2D.
    """
    x, y, z = dataset.coords["x"], dataset.coords["y"], dataset.coords["z"]
    indices = dataset.signal_indices

    fig = go.Figure()
    fig.add_trace(
        go.Scatter3d(
            x=x, y=y, z=z, mode="markers",
            marker=dict(size=POINT_MARKER_SIZE_3D, color=POINT_COLOR),
            customdata=indices,
            hovertemplate=(
                "Señal %{customdata}<br>"
                f"{dataset.axis_labels['x']}: %{{x:.6g}}<br>"
                f"{dataset.axis_labels['y']}: %{{y:.6g}}<br>"
                f"{dataset.axis_labels['z']}: %{{z:.6g}}<extra></extra>"
            ),
            name="Señales",
        )
    )

    highlight = resolve_map_highlight_coords(dataset, selected_signal_index)
    fig.add_trace(
        go.Scatter3d(
            x=highlight["x"], y=highlight["y"], z=highlight["z"], mode="markers",
            marker=dict(size=HIGHLIGHT_MARKER_SIZE_3D, color=HIGHLIGHT_COLOR, symbol="circle"),
            hoverinfo="skip", showlegend=False, name="Seleccionada",
        )
    )

    lines: list[str] = []
    if x.shape[0] == 0:
        lines.append(NO_POINTS_MESSAGE)
    omitted_message = build_omitted_message(dataset.omitted_counts, dataset.axis_labels)
    if omitted_message:
        lines.append(omitted_message)
    if same_metric_warning:
        lines.append(DUPLICATE_AXIS_WARNING)

    fig.update_layout(
        scene=dict(
            xaxis=dict(title=dataset.axis_labels["x"]),
            yaxis=dict(title=dataset.axis_labels["y"]),
            zaxis=dict(title=dataset.axis_labels["z"]),
        ),
        margin=dict(l=0, r=0, t=30, b=0),
        height=MAP_3D_HEIGHT,
        showlegend=False,
        annotations=[build_map_info_annotation(lines)] if lines else [],
        uirevision=uirevision,
    )
    return fig
