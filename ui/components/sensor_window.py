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
from ui.callbacks.helpers import (
    AUTOPLAY_DEFAULT_SPEED_HZ,
    AUTOPLAY_SPEED_OPTIONS_HZ,
    VALID_SENSORS,
    autoplay_interval_ms,
    format_db_path_label,
    resolve_sensor_availability_notice,
)
from ui.components.control_panel import build_control_panel
from ui.components.metadata_panel import build_metadata_panel
from ui.state import get_state


def build_sensor_window_layout(sensor: SensorName) -> html.Div:
    empty_fig = go.Figure()
    empty_fig.update_layout(margin=dict(l=60, r=20, t=30, b=40), height=280)

    state = get_state()
    dataset = state.dataset
    initial_db_label = (
        format_db_path_label(dataset.source_path, dataset.partition)
        if dataset is not None
        else "Ningún archivo cargado"
    )
    # Una pestaña recién abierta debe mostrar la partición REALMENTE cargada, no el valor
    # por defecto del control: si no, el selector diría "resultantes" mientras se están
    # viendo las filtradas, y volver a "resultantes" parecería no hacer nada.
    initial_partition = dataset.partition if dataset is not None and dataset.partition else "resultantes"
    other_sensors = [s for s in VALID_SENSORS if s != sensor]
    initial_filter_status = format_filter_status(*state.get_filter_counts(sensor))
    sensor_has_signals = dataset is not None and sensor in dataset.blocks and dataset.blocks[sensor].data.shape[0] > 0
    initial_notice = resolve_sensor_availability_notice(sensor, dataset is not None, sensor_has_signals)

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
            # Shapes de línea de evento precalculadas una vez por dataset (archivos_md/
            # prompt-mejora-graficas.md §2): el toggle "Mostrar eventos" las parchea con
            # dash.Patch sin volver a construirlas ni tocar ningún dato de métricas.
            dcc.Store(id="event-shapes", data=[]),

            # Fase 6: la escritura de "Exportar datos filtrados…" corre en un hilo
            # aparte (``AppState.export_status``, sondeado en vez de empujado porque un
            # hilo de fondo no puede escribir directamente en un Output de Dash) -- nace
            # deshabilitado, igual que el Interval de auto-play.
            dcc.Interval(id="export-status-poll", interval=700, disabled=True),

            # Fusión de bases de datos: selección de archivos 1 y 2, e intervalo de sondeo
            dcc.Store(id="merge-path-1", data=None),
            dcc.Store(id="merge-path-2", data=None),
            dcc.Interval(id="merge-status-poll", interval=700, disabled=True),

            html.Div(
                className="panel-lateral",
                children=[
                    html.Div(
                        className="twin-window-links",
                        children=[
                            html.A(f"Abrir ventana {other} ↗", href=f"/sensor/{other}", target="_blank")
                            for other in other_sensors
                        ],
                    ),
                    build_control_panel(
                        sensor,
                        initial_db_label=initial_db_label,
                        initial_filter_status=initial_filter_status,
                        initial_partition=initial_partition,
                    ),
                ],
            ),
            html.Div(
                className="panel-central",
                children=[
                    html.H3(f"Sensor: {sensor}"),
                    html.Div(
                        id="sensor-availability-notice",
                        children=initial_notice or "",
                        className="sensor-empty-notice" if initial_notice else "sensor-empty-notice hidden",
                    ),

                    # Orden vertical (Fase 7): señal individual arriba, envolvente global
                    # debajo y las métricas apiladas al final.
                    html.Div(
                        className="signal-nav",
                        children=[
                            html.Button("◀ Anterior", id="btn-prev", n_clicks=0),
                            dcc.Input(id="nav-index", type="number", value=0, min=0, step=1),
                            html.Button("Siguiente ▶", id="btn-next", n_clicks=0),

                            # Auto-play: recorre las señales activas en bucle, como pulsar
                            # "Siguiente" de forma sostenida. El Interval nace deshabilitado
                            # (no hay ticks hasta que se pulsa el botón) y su periodo lo fija
                            # el selector de velocidad -- ver ui/callbacks/helpers.py para por
                            # qué las opciones son una lista cerrada y no un campo libre.
                            html.Button("▶ Auto-play", id="btn-autoplay", n_clicks=0, className="btn-autoplay"),
                            html.Div(
                                className="autoplay-speed",
                                children=[
                                    html.Label("Velocidad", htmlFor="autoplay-speed"),
                                    dcc.Slider(
                                        id="autoplay-speed",
                                        min=min(AUTOPLAY_SPEED_OPTIONS_HZ),
                                        max=max(AUTOPLAY_SPEED_OPTIONS_HZ),
                                        value=AUTOPLAY_DEFAULT_SPEED_HZ,
                                        step=None,  # solo los valores marcados: velocidades medidas
                                        marks={
                                            v: {"label": (f"{v:g}"), "style": {"fontSize": "10px"}}
                                            for v in AUTOPLAY_SPEED_OPTIONS_HZ
                                        },
                                        tooltip={"placement": "bottom"},
                                    ),
                                ],
                            ),
                            html.Span(id="autoplay-status", className="autoplay-status"),
                            dcc.Interval(
                                id="autoplay-interval",
                                interval=autoplay_interval_ms(AUTOPLAY_DEFAULT_SPEED_HZ),
                                disabled=True,
                            ),

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

                    # Bloque de cola garantizado por estructura de layout (archivos_md/
                    # PLAN_MAPAS_2D_3D.md, etapa 3): hermano y SIEMPRE posterior a
                    # "metrics-graphs-container" -- agregar o quitar una métrica nunca
                    # reordena este contenedor, así que "los mapas siempre quedan
                    # últimos" (§2 del prompt) no depende de ningún callback, es una
                    # propiedad del árbol de componentes.
                    html.Div(id="maps-container", className="metrics-graphs-container"),
                ],
            ),
        ],
    )
