"""Punto de entrada de la aplicación Dash (PROMPT maestro §12, Fase 5-6).

Ruteo multi-página con ``dcc.Location`` (FASE0_DISENO...md §3.4: "cada ventana nueva es
literalmente ``window.open()`` a una nueva ruta del mismo servidor"):

- ``/`` o ruta desconocida -> ventana de sensor UHF (por defecto).
- ``/sensor/UHF`` , ``/sensor/AE`` -> ventana de sensor completa (§6.1, "ventanas
  gemelas" = pestañas de navegador separadas contra el mismo proceso).

Todos los callbacks se registran **una sola vez** para todo el proceso; qué sensor está
activo se resuelve en tiempo de render vía ``ui/callbacks/helpers.py::parse_route`` y el
Store ``page-sensor`` sembrado en cada layout, no por identidad de componente.

Fase 6: se eliminó la "ventana adicional de métricas" de la Fase 5 -- cada métrica que
se agrega desde el selector múltiple del panel de control apila una gráfica más dentro
de la misma ventana de sensor, con scroll de página (ver ``ui/components/sensor_window.py``).
"""
from __future__ import annotations

import logging

from dash import Dash, Input, Output, dcc, html

from ui.callbacks.helpers import parse_route
from ui.callbacks.sensor_window_callbacks import register_callbacks
from ui.components.sensor_window import build_sensor_window_layout
from ui.state import DEFAULT_SENSORS_CONFIG_PATH
from utils.profiling import configure_from_config

_INLINE_CSS = """
body { background:#F5F6F8; color:#23262D; font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; margin:0; }
.sensor-window { display:flex; flex-direction:row; gap:16px; padding:16px; }
.panel-lateral { width:300px; flex-shrink:0; background:#FFFFFF; border:1px solid #E1E4E9; border-radius:8px; padding:14px;
                  box-shadow: 0 1px 3px rgba(20,22,28,0.06); }
.panel-central { flex:1; display:flex; flex-direction:column; gap:10px; }
.panel-lateral h4 { margin:14px 0 4px 0; color:#5B6472; font-size:13px; text-transform:uppercase; }
.panel-lateral h5 { margin:8px 0 4px 0; color:#7A8290; font-size:12px; }
.panel-lateral label { display:block; margin-top:8px; font-size:12px; color:#5B6472; }
.db-path-label { font-size:12px; color:#7A8290; margin-top:6px; word-break:break-all; }
.signal-nav { display:flex; gap:8px; align-items:center; margin:6px 0; flex-wrap:wrap; }
.metadata-panel { display:flex; gap:18px; flex-wrap:wrap; background:#FFFFFF; border:1px solid #E1E4E9;
                   border-radius:6px; padding:8px 12px; font-size:13px; box-shadow: 0 1px 3px rgba(20,22,28,0.06); }
.meta-decimated { color:#A6740A; font-weight:600; }
.selected-metric-label { font-size:13px; color:#5B6472; }
.twin-window-links { display:flex; flex-direction:column; gap:4px; margin-bottom:10px; }
.twin-window-links a { font-size:12px; color:#2B6CB0; text-decoration:none; }
.twin-window-links a:hover { text-decoration:underline; }
button { background:#FFFFFF; color:#23262D; border:1px solid #D3D7DD; border-radius:6px; padding:6px 10px; cursor:pointer; }
button:hover { background:#EFF1F4; }
input, .Select-control { background:#FFFFFF; color:#23262D; border:1px solid #D3D7DD; border-radius:4px; }
.metrics-graphs-container { display:flex; flex-direction:column; gap:14px; }
.metrics-graph-slot { flex-shrink:0; background:#FFFFFF; border:1px solid #E1E4E9; border-radius:8px;
                       box-shadow: 0 1px 3px rgba(20,22,28,0.06); }
.metrics-graph-error { padding:14px 16px; color:#8A2C1E; background:#FDEDEA; border-color:#F3C7BE; }
.filter-status { font-size:12px; color:#5B6472; margin:4px 0 8px 0; }
.filter-buttons { display:flex; flex-wrap:wrap; gap:6px; }
.export-status { font-size:12px; color:#5B6472; margin:8px 0 4px 0; }
.sensor-empty-notice { background:#FDF6E8; border:1px solid #E9D9A8; color:#8A6A0A; border-radius:6px;
                        padding:8px 12px; font-size:13px; margin-bottom:4px; }
.sensor-empty-notice.hidden { display:none; }
.btn-autoplay.playing { background:#E8F1FA; border-color:#9FC2E3; color:#20527D; font-weight:600; }
.autoplay-speed { display:flex; align-items:center; gap:6px; width:190px; }
.autoplay-speed label { margin:0; font-size:12px; color:#5B6472; white-space:nowrap; }
.autoplay-speed .rc-slider { flex:1; margin:0 6px; }
.autoplay-status { font-size:12px; color:#7A8290; min-width:88px; }
"""


def create_app() -> Dash:
    # Fase 7 (§9.3): la instrumentación se resuelve una sola vez, al construir la app.
    # Apagada por defecto (config/sensors.yaml -> profiling.enabled), se enciende con
    # ANALIZADOR_PROFILING=1 sin tocar la configuración del proyecto.
    if configure_from_config(DEFAULT_SENSORS_CONFIG_PATH):
        logging.basicConfig(format="%(asctime)s %(name)s %(message)s", level=logging.INFO)
        logging.getLogger("analizador.profiling").info("etapa=profiling.activado")
    compress_enabled = False
    try:
        import flask_compress  # noqa: F401
        compress_enabled = True
    except ImportError:
        pass

    app = Dash(
        __name__,
        title="Analizador UHF/AE",
        suppress_callback_exceptions=True,
        compress=compress_enabled,
    )
    app.index_string = app.index_string.replace("{%css%}", f"{{%css%}}<style>{_INLINE_CSS}</style>")
    app.layout = html.Div([dcc.Location(id="url", refresh=False), html.Div(id="page-content")])

    @app.callback(Output("page-content", "children"), Input("url", "pathname"))
    def _render_page(pathname: str | None):
        route = parse_route(pathname)
        return build_sensor_window_layout(route["sensor"])

    register_callbacks(app)
    return app


def main() -> None:
    app = create_app()
    app.run(debug=True, host="127.0.0.1", port=8050)


if __name__ == "__main__":
    main()
