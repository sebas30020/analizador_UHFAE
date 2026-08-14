"""Ventana de sensor completa (PROMPT §6.2): panel de control a la izquierda, las
gráficas distribuidas verticalmente a la derecha.

Parametrizada por ``sensor`` (mismo componente reutilizable para UHF y AE, §6.1). Desde
la Fase 5, UHF y AE son URLs distintas del mismo servidor Dash (``/sensor/UHF``,
``/sensor/AE``) -- "ventanas gemelas" son pestañas de navegador separadas, no dos
layouts montados a la vez en la misma página (ver ``ui/app.py`` para el ruteo).

Fase 6: la gráfica tipo #3 ya no es una sola -- cada métrica que se agrega desde el
selector múltiple del panel de control se apila debajo de las anteriores en
``metrics-graphs-container`` (altura fija por gráfica, scroll de página). Reemplaza a
la "ventana adicional de métricas" de la Fase 5, que ya no existe.
"""
from __future__ import annotations

import plotly.graph_objects as go
from dash import dcc, html

from core.models import SensorName
from ui.callbacks.filtering import format_filter_status
from ui.callbacks.helpers import VALID_SENSORS
from ui.components.control_panel import build_control_panel
from ui.components.metadata_panel import build_metadata_panel
from ui.state import get_state


def build_sensor_window_layout(sensor: SensorName) -> html.Div:
    empty_fig = go.Figure()
    empty_fig.update_layout(margin=dict(l=60, r=20, t=30, b=40), height=280)

    state = get_state()
    dataset = state.dataset
    initial_db_label = str(dataset.source_path) if dataset is not None else "Ningún archivo cargado"
    other_sensors = [s for s in VALID_SENSORS if s != sensor]
    initial_filter_status = format_filter_status(*state.get_filter_counts(sensor))

    return html.Div(
        className="sensor-window",
        children=[
            # Sembrado con el estado del proceso al construir la página: una pestaña
            # recién abierta ve de inmediato un dataset ya cargado por otra (§6.1) --
            # no hay empuje en vivo entre pestañas ya abiertas, ver FASE5_ENTREGA.md.
            dcc.Store(id="page-sensor", data=sensor),
            dcc.Store(id="dataset-version", data=state.dataset_version or None),
            # Fase 6 (§7.2): contador de versión del estado de filtrado (mismo patrón que
            # dataset-version) + selección pendiente de confirmar con "Filtrar selección".
            dcc.Store(id="filter-version", data=state.filter_version or None),
            dcc.Store(id="pending-exclusion-indices", data=[]),

            html.Div(
                className="panel-lateral",
                children=[
                    html.Div(
                        className="twin-window-links",
                        children=[
                            html.A(f"Abrir ventana {other}  ↗", href=f"/sensor/{other}", target="_blank")
                            for other in other_sensors
                        ],
                    ),
                    build_control_panel(sensor, initial_db_label=initial_db_label, initial_filter_status=initial_filter_status),
                ],
            ),
            html.Div(
                className="panel-central",
                children=[
                    html.H3(f"Sensor: {sensor}"),

                    # Orden vertical (Fase 7): señal individual arriba, envolvente global
                    # debajo y las métricas apiladas al final.
                    html.Div(
                        className="signal-nav",
                        children=[
                            html.Button("◀ Anterior", id="btn-prev", n_clicks=0),
                            dcc.Input(id="nav-index", type="number", value=0, min=0, step=1),
                            html.Button("Siguiente ▶", id="btn-next", n_clicks=0),
                            dcc.Input(id="compare-indices", type="text", placeholder="Comparar con índices (ej. 450,451)", style={"width": "260px"}),
                            dcc.Checklist(
                                id="show-raw-signal",
                                options=[{"label": " Ver señal cruda (sin normalizar)", "value": "raw"}],
                                value=[],
                            ),
                        ],
                    ),
                    dcc.Graph(id="graph-signal", figure=empty_fig),
                    html.Div(
                        id="metadata-panel-container",
                        children=[build_metadata_panel(0, 0, 0.0, 0.0, 0.0, is_decimated=False)],
                    ),

                    dcc.Graph(id="graph-timeseries", figure=empty_fig),

                    html.Div(id="metrics-graphs-container", className="metrics-graphs-container"),
                ],
            ),
        ],
    )
