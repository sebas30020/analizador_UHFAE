"""Lógica pura extraída de los callbacks de Dash, para poder probarla sin arrancar un
servidor ni un navegador (los ``@app.callback`` en ``sensor_window_callbacks.py`` son
envoltorios delgados sobre estas funciones)."""
from __future__ import annotations

from typing import Callable

import numpy as np


def parse_compare_indices(text: str | None, n_total: int, exclude: int | None = None) -> list[int]:
    """Parsea "450,451,452" a una lista de índices válidos (dentro de rango, sin
    duplicados, sin el índice principal ``exclude``). Entradas no numéricas o fuera de
    rango se descartan silenciosamente -- es un campo de conveniencia, no un formulario
    validado con mensajes de error.
    """
    if not text:
        return []
    result: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            idx = int(part)
        except ValueError:
            continue
        if 0 <= idx < n_total and idx != exclude and idx not in result:
            result.append(idx)
    return result


def clamp_index(index: int, n_total: int) -> int:
    if n_total <= 0:
        return 0
    return max(0, min(index, n_total - 1))


AUTOPLAY_TRIGGER_ID = "autoplay-interval"

# --- Auto-play: límites de velocidad -------------------------------------------------
#
# El techo NO lo pone el servidor: construir la gráfica #2 cuesta 3-5 ms en los tres
# sensores (UHF 3.0 ms, AE 4.5 ms, UHF_KS 5.2 ms sobre datos reales, ver
# archivos_md/AUTOPLAY_ENTREGA.md §3). Lo que manda es el repintado de Plotly en el
# navegador más el ida y vuelta del callback. Si los ticks del ``dcc.Interval`` llegan
# más rápido de lo que el navegador alcanza a dibujar, las peticiones se encolan: la
# reproducción se ve a tirones y hasta el botón de pausa tarda en responder.
#
# Por eso la velocidad se ofrece como una lista cerrada de opciones medidas, y
# ``autoplay_interval_ms`` satura por debajo -- ningún valor alcanzable produce
# encolamiento.
AUTOPLAY_MIN_INTERVAL_MS = 150
AUTOPLAY_SPEED_OPTIONS_HZ: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0, 6.0)
AUTOPLAY_DEFAULT_SPEED_HZ = 2.0


def autoplay_interval_ms(speed_hz: float | None) -> int:
    """Traduce velocidad (señales/s) al periodo del ``dcc.Interval``, en milisegundos.

    Satura en :data:`AUTOPLAY_MIN_INTERVAL_MS`: es el guardarraíl que garantiza que
    ninguna velocidad -- ni una inyectada fuera del selector -- pida al navegador más
    cuadros de los que puede dibujar. Un valor nulo o no positivo cae al de por defecto
    en vez de reventar o quedarse quieto.
    """
    if not speed_hz or speed_hz <= 0:
        speed_hz = AUTOPLAY_DEFAULT_SPEED_HZ
    return max(AUTOPLAY_MIN_INTERVAL_MS, int(round(1000.0 / speed_hz)))


def step_to_adjacent_active(
    current_index: int,
    n_total: int,
    direction: int,
    active_indices: np.ndarray | None = None,
    wrap: bool = False,
) -> int:
    """Índice de la señal **activa** inmediatamente anterior/siguiente a ``current_index``.

    ``active_indices``: índices cronológicos globales no excluidos por el filtrado del
    usuario, en orden ascendente (``np.where(mask)[0]``). Avanzar salta las señales
    filtradas en vez de aterrizar en una que ninguna otra gráfica muestra -- el mismo
    criterio con el que las gráficas #1 y #3 dibujan solo lo activo.

    ``wrap=True`` (auto-play): al pasar del último activo vuelve al primero, cerrando el
    bucle. ``wrap=False`` (botones Anterior/Siguiente): se queda en el extremo, que es el
    comportamiento que esos botones ya tenían.

    Sin ``active_indices`` (o con todo excluido) degrada al paso simple de ±1 acotado:
    no hay información de filtrado que respetar, y quedarse inmóvil sería peor que
    moverse.
    """
    if n_total <= 0:
        return 0
    if active_indices is None or active_indices.shape[0] == 0:
        return clamp_index(current_index + direction, n_total)

    if direction >= 0:
        pos = int(np.searchsorted(active_indices, current_index, side="right"))
        if pos < active_indices.shape[0]:
            return int(active_indices[pos])
        return int(active_indices[0]) if wrap else int(active_indices[-1])

    pos = int(np.searchsorted(active_indices, current_index, side="left")) - 1
    if pos >= 0:
        return int(active_indices[pos])
    return int(active_indices[-1]) if wrap else int(active_indices[0])


def resolve_nav_index(
    triggered_id: str | None,
    nav_index_value: int | None,
    current_index: int,
    n_total: int,
    click_target_index: int | None = None,
    active_indices: np.ndarray | None = None,
) -> int:
    """Resuelve el nuevo índice activo según qué disparó el callback de navegación.

    - ``btn-prev``/``btn-next``: al activo anterior/siguiente sobre el índice **actual
      conocido por el estado** (no sobre lo que hubiera en el campo numérico, que podría
      estar a medio escribir). Se detienen en los extremos.
    - ``autoplay-interval``: igual que ``btn-next`` pero en bucle -- del último activo
      vuelve al primero.
    - ``nav-index``: usa el valor tecleado directamente, **sin** saltar a un activo:
      teclear un índice es una petición explícita de ver esa señal concreta, incluso si
      está excluida del análisis.
    - clic en gráfica #1 o #3: usa ``click_target_index`` (ya resuelto por
      ``AppState.nearest_index_for_timestamp``). Esas gráficas solo dibujan señales
      activas, así que el objetivo ya viene filtrado por construcción.
    - clic en el mapa 2D o 3D (``"graph-map"``, archivos_md/prompt-mapas2d3d.md §5.1):
      usa ``click_target_index``, ya resuelto por :func:`resolve_map_click_signal_index`
      -- viene directo de ``customdata`` del punto clicado, no de una búsqueda por
      timestamp.
    - cualquier otro disparo (carga inicial): mantiene el índice actual.

    ``active_indices``: ver :func:`step_to_adjacent_active`.
    """
    if triggered_id == "btn-prev":
        return step_to_adjacent_active(current_index, n_total, -1, active_indices, wrap=False)
    if triggered_id == "btn-next":
        return step_to_adjacent_active(current_index, n_total, +1, active_indices, wrap=False)
    if triggered_id == AUTOPLAY_TRIGGER_ID:
        return step_to_adjacent_active(current_index, n_total, +1, active_indices, wrap=True)

    if triggered_id == "nav-index":
        target = nav_index_value if nav_index_value is not None else current_index
    elif triggered_id in ("graph-timeseries", "graph-metric", "graph-map") and click_target_index is not None:
        target = click_target_index
    else:
        target = current_index
    return clamp_index(int(target), n_total)


def resolve_map_click_signal_index(click_data: dict | None) -> int | None:
    """Índice global de señal del punto clicado en el mapa 2D o 3D (#4/#5,
    ``archivos_md/prompt-mapas2d3d.md`` §5.1).

    A diferencia de las gráficas #1/#3 (que dibujan minutos transcurridos y necesitan
    ``AppState.nearest_index_for_timestamp`` para volver al índice de señal), los mapas
    ya sembraron el índice global de señal como ``customdata`` de cada punto
    (``ui/components/graph_map_2d.py``, ``graph_map_3d.py``) -- viene exacto, sin
    búsqueda por vecino más cercano.

    ``None`` si no hay puntos clicados o si el punto clicado no trae ``customdata`` (la
    traza de resaltado, ``HIGHLIGHT_TRACE_INDEX``, no lo tiene -- clicar sobre la propia
    señal ya seleccionada no debe hacer nada, no es un error).
    """
    if not click_data:
        return None
    points = click_data.get("points") or []
    if not points:
        return None
    customdata = points[0].get("customdata")
    if customdata is None:
        return None
    if isinstance(customdata, (list, tuple)):
        if not customdata:
            return None
        customdata = customdata[0]
    return int(customdata)


VALID_SENSORS = ("UHF", "AE", "UHF_KS")
DEFAULT_SENSOR = "UHF"


def parse_route(pathname: str | None) -> dict:
    """Interpreta la ruta de la URL (PROMPT §6.1 -- ventanas gemelas por sensor):

    - ``/`` o cualquier ruta no reconocida -> ventana de sensor por defecto (UHF).
    - ``/sensor/<UHF|AE|UHF_KS>`` -> ventana de sensor completa. ``UHF_KS`` es el
      osciloscopio Keysight en memoria segmentada (rama ``lectura_keysight``).
    """
    if pathname:
        parts = [p for p in pathname.split("/") if p]
        if len(parts) == 2 and parts[0] == "sensor" and parts[1] in VALID_SENSORS:
            return {"page": "sensor", "sensor": parts[1]}

    return {"page": "sensor", "sensor": DEFAULT_SENSOR}


def resolve_sensor_availability_notice(sensor: str, has_dataset: bool, sensor_has_signals: bool) -> str | None:
    """Texto del aviso de "esta ventana no aplica a este dataset" (rama
    ``lectura_keysight``, §4.5 del plan), o ``None`` si no hace falta mostrar nada.

    Con tres sensores posibles y cada origen entregando solo un subconjunto (un archivo
    Keysight no tiene AE, ``med_5_ago_3.hdf5`` no tiene ``UHF_KS``), una ventana sin
    señales de su sensor debe decirlo -- la alternativa es dejar tres gráficas vacías
    sin explicación, indistinguible de un error. Sin dataset cargado no hay nada que
    avisar todavía (mensaje distinto, "Ningún archivo cargado", ya cubierto en otra
    parte de la interfaz).
    """
    if not has_dataset or sensor_has_signals:
        return None
    return f"El archivo cargado no contiene señales del sensor {sensor}."


def encode_metric_option(regimen: str, metric_id: str) -> str:
    """Codifica una opción del selector múltiple de métricas (un mismo ``metric_id``
    puede aparecer en más de un régimen, p. ej. "kurtosis" puntual y "kurtosis"
    reducido por mediana son selecciones distintas)."""
    return f"{regimen}:{metric_id}"


def decode_metric_option(value: str) -> tuple[str, str]:
    regimen, metric_id = value.split(":", 1)
    return regimen, metric_id


# Mensajes de los casos borde de la línea de referencia (archivos_md/prompt-linea-
# referencia.md §2.2): "informar la situación con un mensaje breve y formal, sin
# bloquear el resto de la gráfica" -- nunca una excepción, nunca una gráfica en blanco.
REFERENCE_T_INVALID_MESSAGE = "Intervalo de referencia no definido: introduzca un valor mayor que 0."
REFERENCE_NO_DATA_MESSAGE = "Sin datos disponibles en el intervalo definido para la línea de referencia."


def resolve_reference_line_display(
    enabled: bool,
    t_minutes: float | None,
    mean_lookup: Callable[[float], float | None],
) -> tuple[float | None, str | None]:
    """Resuelve qué debe mostrar una gráfica #3 para la línea de referencia: un valor
    numérico ya promediado, un mensaje breve de caso borde, o nada si el selector de
    visibilidad está apagado (estado por defecto).

    ``mean_lookup``: closure que ya conoce la serie de la gráfica concreta -- en
    producción, ``functools.partial(get_reference_registry().mean_until,
    option_value)`` -- para que esta función se pueda probar sin depender del registro
    de proceso ni de arrays reales.

    ``t <= 0`` y "sin muestras en el intervalo" (``mean_lookup`` devuelve ``None``) son
    el mismo caso desde la perspectiva de la interfaz: no hay línea que dibujar, y se
    informa por qué. ``t`` mayor que la duración del experimento no es un caso borde
    aquí -- ``mean_lookup`` ya lo resuelve usando todos los datos disponibles
    (:func:`viz.reference_line.mean_until`).
    """
    if not enabled:
        return None, None
    if t_minutes is None or t_minutes <= 0:
        return None, REFERENCE_T_INVALID_MESSAGE
    value = mean_lookup(t_minutes)
    if value is None:
        return None, REFERENCE_NO_DATA_MESSAGE
    return value, None


def resolve_map_axis_status(metric_ids: list[str | None]) -> tuple[bool, bool]:
    """Resuelve el estado de los selectores de eje de un mapa de separación
    (``archivos_md/prompt-mapas2d3d.md`` §3): ``(todos_asignados, hay_metrica_repetida)``.

    ``todos_asignados=False``: al menos un eje no tiene métrica -- el llamador debe
    mostrar el estado vacío informativo (``ui.components.graph_map_common.
    build_empty_map_figure``) en vez de intentar calcular nada. La duplicidad no se
    evalúa en ese caso (no aplica hasta que todos los ejes tengan métrica).

    ``hay_metrica_repetida``: la misma métrica en dos o más ejes es válida (§3: "se
    permite repetir"), pero el llamador debe mostrarlo con una advertencia discreta.
    """
    if any(m is None for m in metric_ids):
        return False, False
    return True, len(set(metric_ids)) != len(metric_ids)
