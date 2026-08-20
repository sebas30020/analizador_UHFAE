"""Gráfica #4 -- Mapa de separación 2D (``archivos_md/prompt-mapas2d3d.md``).

Un punto = una señal (§1 del prompt): las coordenadas son los valores de dos métricas
puntuales para esa señal, ya resueltas y alineadas por ``viz/maps.py``. Este componente
no calcula nada, solo dibuja -- mismo reparto de responsabilidades que
``ui/components/graph_metric.py``.

Navegación (§5.7 del prompt: "zoom y paneo en 2D, botón de reset de vista"): la
provee el modebar de Plotly por defecto, sin configuración adicional -- ninguna de las
gráficas existentes del proyecto (#1, #3) pasa un ``config=`` propio a ``dcc.Graph``
(``ui/components/sensor_window.py``), y este mapa sigue esa misma convención. El lazo y
la caja de selección (§5.4, exclusivos de este mapa por decisión D2 del plan) también
son iconos del modebar por defecto -- lo que activa el filtrado es el callback que lee
``selectedData``, no un ``dragmode`` propio.
"""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go

from ui.components.graph_map_common import (
    DUPLICATE_AXIS_WARNING,
    HIGHLIGHT_COLOR,
    HIGHLIGHT_MARKER_SIZE_2D,
    NO_POINTS_MESSAGE,
    POINT_COLOR,
    POINT_MARKER_SIZE_2D,
    build_map_info_annotation,
    build_omitted_message,
)
from viz.maps import MapDataset

MAP_2D_HEIGHT = 420


def build_map_2d_figure(
    dataset: MapDataset,
    same_metric_warning: bool = False,
    selected_signal_index: int | None = None,
    uirevision: str | None = None,
) -> go.Figure:
    """Scatter 2D sobre ``dataset.coords["x"]``/``["y"]`` -- ya viene sin NaN/inf y con
    ``dataset.signal_indices`` alineado punto a punto (``viz.maps.build_map_dataset``,
    llamado con ``{"x": metric_id_x, "y": metric_id_y}``).

    ``selected_signal_index``: índice global de señal actualmente activa
    (``AppState``/``nav-index``, mismo bus que ya usan #1 y #3). Si está presente en el
    mapa, se redibuja resaltada encima de la nube de puntos -- resaltado bidireccional
    con la Gráfica #2 y el mapa #5 (§5.3 del prompt).

    ``same_metric_warning``: la misma métrica en ambos ejes es válida (§3 del prompt),
    pero se avisa con una anotación discreta en vez de silenciarlo.
    """
    x, y = dataset.coords["x"], dataset.coords["y"]
    indices = dataset.signal_indices

    fig = go.Figure()
    fig.add_trace(
        go.Scattergl(
            x=x, y=y, mode="markers",
            marker=dict(size=POINT_MARKER_SIZE_2D, color=POINT_COLOR),
            customdata=indices,
            hovertemplate=(
                "Señal %{customdata}<br>"
                f"{dataset.axis_labels['x']}: %{{x:.6g}}<br>"
                f"{dataset.axis_labels['y']}: %{{y:.6g}}<extra></extra>"
            ),
            name="Señales",
        )
    )

    if selected_signal_index is not None:
        pos = np.where(indices == selected_signal_index)[0]
        if pos.size > 0:
            i = int(pos[0])
            fig.add_trace(
                go.Scattergl(
                    x=[x[i]], y=[y[i]], mode="markers",
                    marker=dict(
                        size=HIGHLIGHT_MARKER_SIZE_2D, color=HIGHLIGHT_COLOR,
                        symbol="circle-open", line=dict(width=2),
                    ),
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
        xaxis=dict(title=dataset.axis_labels["x"]),
        yaxis=dict(title=dataset.axis_labels["y"]),
        margin=dict(l=60, r=20, t=30, b=40),
        height=MAP_2D_HEIGHT,
        showlegend=False,
        annotations=[build_map_info_annotation(lines)] if lines else [],
        uirevision=uirevision,
    )
    return fig
