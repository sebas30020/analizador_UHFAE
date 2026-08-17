"""Suavizado de presentación para las gráficas de métricas (archivos_md/prompt-mejora-
graficas.md §3). Funciones puras: sin dependencia de Plotly ni del motor de métricas, la
misma implementación sirve a régimen puntual y a régimen de grupo (§5, "separación de
responsabilidades").

**No es cálculo de métricas.** Nada de este módulo toca ``metrics/engine.py``,
``cache/service.py`` ni ``core/grouping.py`` -- se aplica siempre después, dentro de
``ui/components/graph_metric.py``, sobre valores ya calculados y ya cacheados.

Dos piezas independientes:

- :func:`smooth` -- media móvil o mediana móvil, vectorizada.
- :func:`split_on_gaps` -- inserta separadores ``NaN`` donde el eje X salta más de un
  umbral, para que Plotly corte la línea en vez de unir a través de un hueco de
  adquisición real (mismo idioma que ``viz/decimation.py``: el ``NaN`` como separador,
  no como error).

Elección de método (documentada en la nota de entrega): el eje X del proyecto **no es
equiespaciado** -- los timestamps son reales, con huecos de adquisición estructurales
(``archivos_md/esquema_med_5_ago_3.md``: no todos los chunks tienen señales de ambos
sensores). Por eso el método por defecto es una **media móvil de ventana temporal**
(``"media_movil_temporal"``), no una media móvil por número de puntos: con espaciado
irregular, una ventana por conteo de puntos abarca un intervalo de tiempo distinto según
la densidad local, lo que distorsiona la tendencia. Savitzky-Golay y LOWESS quedan
descartados por la misma razón (exigen o se benefician de muestreo regular) y por costo
computacional con 10⁴-10⁵ puntos.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

SmoothingMethod = Literal["media_movil_temporal", "mediana_movil_puntos"]


@dataclass(frozen=True)
class SmoothingSpec:
    """Parámetros de suavizado que ``ui/components/graph_metric.py`` recibe desde la
    GUI. ``window`` está en las mismas unidades que el eje X del llamador -- en este
    proyecto, minutos transcurridos (``ui/components/time_axis.py``) para
    ``"media_movil_temporal"``, número de puntos para ``"mediana_movil_puntos"``."""

    method: SmoothingMethod = "media_movil_temporal"
    window: float = 5.0

# Punto de referencia (no un límite duro) para decidir cuántas abscisas evaluar en
# régimen puntual: evaluar la curva sobre miles de puntos en vez de decenas de miles
# la deja visualmente idéntica y evita duplicar el volumen de JSON que ya reporta
# docs/RENDIMIENTO.md (§9.2: "1,9 MB / 2,5 MB de JSON por refresco" solo para la #1).
DEFAULT_MAX_QUERY_POINTS = 2000

# Ventana máxima admitida por la mediana móvil por puntos: sliding_window_view crea una
# vista de (n, ventana) elementos: con n~20000 y ventana grande el costo de
# nanmedian crece proporcional al producto, así se acota explícitamente en vez de
# descubrirlo con el dataset real en producción.
MAX_MEDIAN_WINDOW_POINTS = 501


def auto_gap_threshold(x: np.ndarray) -> float:
    """Umbral de hueco por defecto: 5 veces la mediana de los incrementos positivos de
    ``x``. Robusto a un puñado de huecos grandes reales (que son justamente lo que se
    quiere detectar) porque la mediana no se deja arrastrar por ellos, a diferencia de
    la media.
    """
    if x.shape[0] < 2:
        return float("inf")
    diffs = np.diff(x)
    diffs = diffs[diffs > 0]
    if diffs.shape[0] == 0:
        return float("inf")
    return float(5.0 * np.median(diffs))


def split_on_gaps(x: np.ndarray, y: np.ndarray, max_gap: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Inserta un separador ``NaN`` en ``y`` (y el punto medio del hueco en ``x``, solo
    para mantener ambos arrays del mismo tamaño) en cada posición donde ``diff(x)`` supera
    ``max_gap``. ``max_gap=None`` resuelve el umbral con :func:`auto_gap_threshold`.

    Precondición: ``x`` ordenado ascendente (garantizado aguas arriba por
    ``data/ingest.py`` y por ``core/grouping.py`` -- no se reordena aquí).

    Con Plotly y ``connectgaps=False`` (el valor por omisión), un ``NaN`` en ``y`` corta
    la línea sin borrar los puntos vecinos -- mismo mecanismo que ya usa
    ``viz/decimation.py::build_vertical_segments`` para separar segmentos.
    """
    if x.shape[0] == 0:
        return x, y
    threshold = auto_gap_threshold(x) if max_gap is None else max_gap
    gaps = np.where(np.diff(x) > threshold)[0]
    if gaps.shape[0] == 0:
        return x, y
    insert_at = gaps + 1
    x_mid = (x[gaps] + x[gaps + 1]) / 2.0
    x_out = np.insert(x, insert_at, x_mid)
    y_out = np.insert(y.astype(np.float64, copy=False), insert_at, np.nan)
    return x_out, y_out


def _rolling_mean_time(x: np.ndarray, y: np.ndarray, window: float, x_query: np.ndarray) -> np.ndarray:
    """Media móvil centrada en ventana temporal, evaluada en ``x_query``.

    Vectorizada vía suma de prefijos + ``searchsorted``: dos búsquedas binarias y una
    resta por punto de consulta, sin iterar señal a señal. Los ``NaN`` de entrada se
    excluyen del promedio (se cuentan aparte, no se tratan como cero) -- si una ventana
    no contiene ningún valor finito, el resultado en ese punto es ``NaN``: no se
    interpola ni se rellena, coherente con ``viz/decimation.py`` ("bins vacíos se omiten,
    no se rellenan con 0").

    Los bordes no requieren tratamiento especial: la ventana simplemente se encoge donde
    no hay datos a un lado, sin recorte visual ni artefacto artificial.
    """
    finite = np.isfinite(y)
    y_filled = np.where(finite, y, 0.0)
    cs = np.concatenate(([0.0], np.cumsum(y_filled)))
    cn = np.concatenate(([0], np.cumsum(finite)))

    half = window / 2.0
    lo = np.searchsorted(x, x_query - half, side="left")
    hi = np.searchsorted(x, x_query + half, side="right")

    count = cn[hi] - cn[lo]
    total = cs[hi] - cs[lo]
    return np.where(count > 0, total / np.maximum(count, 1), np.nan)


def _rolling_median_points(y: np.ndarray, window_points: int) -> np.ndarray:
    """Mediana móvil centrada por número de puntos (no por tiempo). Solo válida cuando
    se evalúa exactamente en los puntos de entrada, no en un ``x_query`` arbitrario --
    por eso :func:`smooth` la restringe a ``x_query is None``.

    ``sliding_window_view`` no copia datos (vistas con stride), así que el costo real es
    el de ``np.nanmedian`` sobre ``(n, window_points)`` -- por eso ``window_points`` está
    acotado por :data:`MAX_MEDIAN_WINDOW_POINTS`.
    """
    n = y.shape[0]
    w = min(window_points | 1, MAX_MEDIAN_WINDOW_POINTS, n if n % 2 == 1 else n - 1)
    if w < 1:
        return np.full(n, np.nan)
    half = w // 2
    padded = np.concatenate((np.full(half, np.nan), y.astype(np.float64, copy=False), np.full(half, np.nan)))
    windows = np.lib.stride_tricks.sliding_window_view(padded, w)
    with np.errstate(invalid="ignore"):
        return np.nanmedian(windows, axis=1)


def smooth(
    x: np.ndarray,
    y: np.ndarray,
    method: SmoothingMethod = "media_movil_temporal",
    window: float = 300.0,
    x_query: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Punto de entrada único de suavizado. Retorna ``(x_out, y_out)`` ya suavizados,
    listos para dibujar como segunda traza superpuesta a los puntos crudos.

    ``window``: mismas unidades que ``x`` (``"media_movil_temporal"``) o número de
    puntos, redondeado a impar (``"mediana_movil_puntos"``).

    ``x_query``: abscisas donde evaluar la curva. Si es ``None``, se evalúa en los
    propios puntos de ``x`` (mediana móvil) o en un subconjunto de hasta
    :data:`DEFAULT_MAX_QUERY_POINTS` abscisas equiespaciadas (media móvil temporal) --
    la curva se ve idéntica con menos puntos y evita duplicar el volumen de datos que
    viaja al navegador en régimen puntual (10⁴-10⁵ puntos).
    """
    if x.shape[0] == 0:
        return x, y

    if method == "media_movil_temporal":
        if x_query is None:
            if x.shape[0] > DEFAULT_MAX_QUERY_POINTS:
                x_query = np.linspace(x[0], x[-1], DEFAULT_MAX_QUERY_POINTS)
            else:
                x_query = x
        y_out = _rolling_mean_time(x, y, window, x_query)
        return x_query, y_out

    if method == "mediana_movil_puntos":
        # La mediana por conteo de puntos solo tiene sentido evaluada en los propios
        # puntos de entrada -- reasignar a un x_query arbitrario requeriría reinterpolar,
        # lo que el proyecto evita deliberadamente (§3.4: "no se interpola").
        y_out = _rolling_median_points(y, int(round(window)))
        return x, y_out

    raise ValueError(f"Método de suavizado desconocido: '{method}'")
