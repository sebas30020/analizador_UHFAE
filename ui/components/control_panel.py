"""Panel de control: selección de BD, selector múltiple de métricas, criterio de
agrupamiento y opciones de visualización.

Fase 6: la antigua selección única por ``RadioItems`` (una gráfica tipo #3 a la vez) se
reemplazó por un selector múltiple -- cada métrica agregada apila una gráfica más en la
ventana de sensor (``ui/components/sensor_window.py``), sin tope artificial: la propia
página hace scroll, así que ya no hace falta la "ventana adicional" que existía en la
Fase 5 para controlar la saturación (ver ``ui/callbacks/sensor_window_callbacks.py``).

El bloque "Opciones de visualización" (archivos_md/prompt-mejora-graficas.md) agrupa
los controles de presentación de las gráficas #1 y #3: visibilidad de eventos y
suavizado de la línea de tendencia. Son controles de presentación puros -- no cambian
qué se calcula, solo cómo se dibuja lo ya calculado (ver ``viz/smoothing.py`` y
``ui/components/graph_metric.py``).

``show-reference-line`` / ``reference-line-t`` (archivos_md/prompt-linea-referencia.md):
mismo bloque y mismo criterio -- controles de presentación puros, el promedio se calcula
en ``viz/reference_line.py`` sobre datos ya obtenidos, nunca aquí.
"""
from __future__ import annotations

from dash import dcc, html

from core.models import SensorName
from metrics.registry import list_metrics
from ui.callbacks.helpers import encode_metric_option

# Duración típica de un experimento en el dataset real de referencia (med_5_ago_3.hdf5,
# ver archivos_md/PLAN_LINEA_REFERENCIA.md): ~350 minutos. 10 min es un valor por defecto
# que refleja un tramo inicial representativo sin necesitar ajuste manual antes de ver
# algo útil.
_DEFAULT_REFERENCE_T_MIN = 10.0


def _puntual_metric_axis_options() -> list[dict]:
    """Catálogo de los selectores de eje de los mapas de separación #4/#5
    (``archivos_md/prompt-mapas2d3d.md``): solo régimen puntual (decisión D3,
    ``archivos_md/PLAN_MAPAS_2D_3D.md``) -- es el único que produce un escalar por
    señal, así que no hace falta codificar el régimen en el valor de la opción como en
    ``_all_metric_options`` (aquí ``value`` es directamente el ``metric_id``)."""
    options = []
    for d in list_metrics(regimen="puntual"):
        unit = f" ({d.unit})" if d.unit else ""
        options.append({"label": f"{d.label}{unit}", "value": d.id})
    return options


def _all_metric_options() -> list[dict]:
    """Cada métrica puntual aparece dos veces (puntual y grupo-reducción) más las
    intrínsecas de grupo, codificando ``"<regimen>:<metric_id>"`` porque un mismo
    ``metric_id`` (p. ej. "kurtosis") puede aparecer en dos regímenes distintos con
    significados distintos."""
    options = []
    for d in list_metrics(regimen="puntual"):
        unit = f" ({d.unit})" if d.unit else ""
        dominio_label = "tiempo" if d.dominio == "tiempo" else "frecuencia"
        options.append({
            "label": f"{d.label}{unit} — puntual/{dominio_label}",
            "value": encode_metric_option("puntual", d.id),
        })
    for d in list_metrics(regimen="grupo"):
        unit = f" ({d.unit})" if d.unit else ""
        options.append({"label": f"{d.label}{unit} — grupo (tasa)", "value": encode_metric_option("grupo_intrinseca", d.id)})
    for d in list_metrics(regimen="puntual"):
        unit = f" ({d.unit})" if d.unit else ""
        options.append({"label": f"{d.label}{unit} — grupo (reducción)", "value": encode_metric_option("grupo_reduccion", d.id)})
    return options


def build_control_panel(
    sensor: SensorName,
    initial_db_label: str = "Ningún archivo cargado",
    initial_filter_status: str = "0/0 señales activas · 0 filtro(s) aplicado(s)",
    initial_partition: str = "resultantes",
) -> html.Div:
    return html.Div(
        className="control-panel",
        children=[
            html.Button("Seleccionar base de datos…", id="btn-select-db", n_clicks=0),
            html.Div(id="db-path-label", children=initial_db_label, className="db-path-label"),
            html.Div(
                title="Cambia al instante qué señales se ven, recargando el archivo ya abierto. "
                      "Solo aplica si el archivo cargado es una exportación filtrada de esta misma "
                      "herramienta (botón \"Exportar datos filtrados…\" más abajo); se ignora para "
                      "cualquier otro origen (med_5_ago_3.hdf5, Keysight). Cambiar de partición "
                      "reinicia el filtrado, porque cada partición es un conjunto de señales distinto.",
                children=[
                    html.Label("Partición visible (solo archivos exportados)"),
                    dcc.RadioItems(
                        id="load-partition",
                        options=[
                            {"label": " Resultantes (activas)", "value": "resultantes"},
                            {"label": " Filtradas (excluidas)", "value": "filtradas"},
                            {"label": " Ambas (conjunto completo)", "value": "ambas"},
                        ],
                        value=initial_partition,
                    ),
                ],
            ),
            html.Hr(),

            html.H4("Métricas"),
            dcc.Dropdown(
                id="metrics-picker",
                options=_all_metric_options(),
                value=[],
                multi=True,
                placeholder="Seleccionar una o más métricas",
            ),

            html.Hr(),
            html.Label(
                "Reductor de grupo",
                title="Función de agregación aplicada a una métrica puntual dentro de cada grupo. Solo aplica a las métricas de grupo por reducción.",
            ),
            dcc.Dropdown(
                id="grouping-reducer",
                options=[
                    {"label": "Mediana", "value": "median"},
                    {"label": "Media", "value": "mean"},
                    {"label": "Percentil", "value": "percentile"},
                ],
                value="median",
                clearable=False,
            ),
            html.Label("Percentil (%)", title="Percentil usado cuando el reductor de grupo es \"Percentil\"."),
            dcc.Input(id="grouping-percentile-q", type="number", value=75.0, min=0, max=100, style={"width": "100%"}),
            html.Label("Criterio de agrupamiento"),
            dcc.Dropdown(
                id="grouping-mode",
                options=[
                    {"label": "Por ventana temporal (s)", "value": "by_time"},
                    {"label": "Por cantidad de señales", "value": "by_count"},
                ],
                value="by_time",
                clearable=False,
            ),
            html.Label(
                "Ventana de agrupamiento",
                title="Duración de la ventana en segundos (agrupamiento por ventana temporal) o número de señales por grupo (agrupamiento por cantidad).",
            ),
            dcc.Input(id="grouping-value", type="number", value=60.0, min=0.000001, style={"width": "100%"}),

            html.Hr(),
            html.H4("Opciones de visualización"),
            html.Div(
                title="Muestra u oculta las líneas verticales de evento en la serie temporal global y en las gráficas de métricas. No recalcula ningún dato.",
                children=dcc.Checklist(
                    id="show-events",
                    options=[{"label": " Mostrar eventos", "value": "show"}],
                    value=["show"],
                ),
            ),
            html.Div(
                title="Superpone una línea de tendencia suavizada sobre los puntos de las métricas individuales, que se muestran atenuados debajo.",
                children=dcc.Checklist(
                    id="smooth-puntual",
                    options=[{"label": " Suavizar métricas individuales", "value": "smooth"}],
                    value=["smooth"],
                ),
            ),
            html.Div(
                title="Aplica el mismo suavizado a la línea de tendencia de las métricas de grupo, que ya se muestran unidas por una línea directa.",
                children=dcc.Checklist(
                    id="smooth-grupo",
                    options=[{"label": " Suavizar tendencia agrupada", "value": "smooth"}],
                    value=[],
                ),
            ),
            html.Label("Método de suavizado"),
            dcc.Dropdown(
                id="smoothing-method",
                options=[
                    {"label": "Media móvil (ventana temporal)", "value": "media_movil_temporal"},
                    {"label": "Mediana móvil (ventana por cantidad de puntos)", "value": "mediana_movil_puntos"},
                ],
                value="media_movil_temporal",
                clearable=False,
            ),
            html.Label(id="smoothing-window-label", children="Ventana de suavizado (min)"),
            dcc.Input(id="smoothing-window", type="number", value=5.0, min=0.01, style={"width": "100%"}),
            html.Label(
                "Cortar la línea en huecos mayores a (min)",
                title="Umbral de tiempo a partir del cual la línea se corta en vez de unir a través de un hueco de adquisición. Vacío: se calcula automáticamente a partir del espaciado real de los datos.",
            ),
            dcc.Input(id="gap-threshold", type="number", placeholder="Automático", style={"width": "100%"}),

            html.Div(
                title="Dibuja, en cada gráfica de métrica, una línea horizontal con el promedio de esa métrica entre el inicio del experimento y el minuto definido más abajo. No recalcula ninguna métrica ni altera el zoom o el paneo ya aplicados.",
                children=dcc.Checklist(
                    id="show-reference-line",
                    options=[{"label": " Mostrar línea de referencia", "value": "show"}],
                    value=[],
                ),
            ),
            html.Label(
                "Definir intervalo de referencia (min)",
                title="Minuto hasta el cual se promedian los datos para calcular la línea de referencia, contado desde el inicio del experimento.",
            ),
            dcc.Input(
                id="reference-line-t",
                type="number", value=_DEFAULT_REFERENCE_T_MIN, min=0.000001,
                debounce=True, style={"width": "100%"},
            ),

            html.Hr(),
            html.H4("Mapas de separación"),
            html.Div(
                title="Gráfica #4: mapa 2D donde cada punto es una señal, ubicada por los valores de dos métricas puntuales cualesquiera. Reutiliza el mismo conjunto de señales del filtrado activo.",
                children=dcc.Checklist(
                    id="map-2d-enabled",
                    options=[{"label": " Habilitar mapa 2D", "value": "show"}],
                    value=[],
                ),
            ),
            html.Label("Eje X (mapa 2D)"),
            dcc.Dropdown(
                id="map-2d-x-metric", options=_puntual_metric_axis_options(),
                value=None, clearable=True, placeholder="Métrica para el eje X",
            ),
            html.Label("Eje Y (mapa 2D)"),
            dcc.Dropdown(
                id="map-2d-y-metric", options=_puntual_metric_axis_options(),
                value=None, clearable=True, placeholder="Métrica para el eje Y",
            ),
            html.Div(
                title="Gráfica #5: igual que el mapa 2D con un tercer eje. Solo lectura para navegación (Plotly no ofrece lazo/caja de selección dentro de una escena 3D) -- el filtrado por selección se hace desde el mapa 2D.",
                children=dcc.Checklist(
                    id="map-3d-enabled",
                    options=[{"label": " Habilitar mapa 3D", "value": "show"}],
                    value=[],
                ),
            ),
            html.Label("Eje X (mapa 3D)"),
            dcc.Dropdown(
                id="map-3d-x-metric", options=_puntual_metric_axis_options(),
                value=None, clearable=True, placeholder="Métrica para el eje X",
            ),
            html.Label("Eje Y (mapa 3D)"),
            dcc.Dropdown(
                id="map-3d-y-metric", options=_puntual_metric_axis_options(),
                value=None, clearable=True, placeholder="Métrica para el eje Y",
            ),
            html.Label("Eje Z (mapa 3D)"),
            dcc.Dropdown(
                id="map-3d-z-metric", options=_puntual_metric_axis_options(),
                value=None, clearable=True, placeholder="Métrica para el eje Z",
            ),

            html.Hr(),
            html.H4("Filtrado"),
            html.Div(id="filter-status", children=initial_filter_status, className="filter-status"),
            html.Div(
                className="filter-buttons",
                children=[
                    html.Button("Filtrar selección", id="btn-apply-filter", n_clicks=0),
                    html.Button("Deshacer", id="btn-undo-filter", n_clicks=0),
                    html.Button("Rehacer", id="btn-redo-filter", n_clicks=0),
                    html.Button("Restablecer todo", id="btn-reset-filters", n_clicks=0),
                ],
            ),
            html.Div(
                title="Exporta TODOS los sensores del dataset cargado a un archivo HDF5 nuevo, con las "
                      "señales resultantes (activas) y las filtradas (excluidas) en particiones separadas "
                      "-- cada una con su matriz de trazas crudas, metadatos, y los ambientales/eventos del "
                      "experimento. La escritura corre en segundo plano, sin bloquear la interfaz.",
                children=[
                    html.Button("Exportar datos filtrados…", id="btn-export-filtered", n_clicks=0),
                    html.Div(id="export-status", children="", className="export-status"),
                ],
            ),
        ],
    )
