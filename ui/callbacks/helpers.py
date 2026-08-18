"""Lógica pura extraída de los callbacks de Dash, para poder probarla sin arrancar un
servidor ni un navegador (los ``@app.callback`` en ``sensor_window_callbacks.py`` son
envoltorios delgados sobre estas funciones)."""
from __future__ import annotations

from typing import Callable


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


def resolve_nav_index(
    triggered_id: str | None,
    nav_index_value: int | None,
    current_index: int,
    n_total: int,
    click_target_index: int | None = None,
) -> int:
    """Resuelve el nuevo índice activo según qué disparó el callback de navegación.

    - ``btn-prev``/``btn-next``: +-1 sobre el índice **actual conocido por el estado**
      (no sobre lo que hubiera en el campo numérico, que podría estar a medio escribir).
    - ``nav-index``: usa el valor tecleado directamente.
    - clic en gráfica #1 o #3: usa ``click_target_index`` (ya resuelto por
      ``AppState.nearest_index_for_timestamp``).
    - cualquier otro disparo (carga inicial): mantiene el índice actual.
    """
    if triggered_id == "btn-prev":
        target = current_index - 1
    elif triggered_id == "btn-next":
        target = current_index + 1
    elif triggered_id == "nav-index":
        target = nav_index_value if nav_index_value is not None else current_index
    elif triggered_id in ("graph-timeseries", "graph-metric") and click_target_index is not None:
        target = click_target_index
    else:
        target = current_index
    return clamp_index(int(target), n_total)


VALID_SENSORS = ("UHF", "AE")
DEFAULT_SENSOR = "UHF"


def parse_route(pathname: str | None) -> dict:
    """Interpreta la ruta de la URL (PROMPT §6.1 -- ventanas gemelas por sensor):

    - ``/`` o cualquier ruta no reconocida -> ventana de sensor por defecto (UHF).
    - ``/sensor/<UHF|AE>`` -> ventana de sensor completa.
    """
    if pathname:
        parts = [p for p in pathname.split("/") if p]
        if len(parts) == 2 and parts[0] == "sensor" and parts[1] in VALID_SENSORS:
            return {"page": "sensor", "sensor": parts[1]}

    return {"page": "sensor", "sensor": DEFAULT_SENSOR}


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
