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
"""
from __future__ import annotations

from dash import dcc, html

from core.models import SensorName
from metrics.registry import list_metrics
from ui.callbacks.helpers import encode_metric_option


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
) -> html.Div:
    return html.Div(
        className="control-panel",
        children=[
            html.Button("Seleccionar base de datos…", id="btn-select-db", n_clicks=0),
            html.Div(id="db-path-label", children=initial_db_label, className="db-path-label"),
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
        ],
    )
