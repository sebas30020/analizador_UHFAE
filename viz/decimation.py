"""Diezmado min/max por bin de píxel (PROMPT maestro §5.2).

"Esta agregación debe ser exacta, no un submuestreo" -- cada bin de píxel agrega el
**mínimo de los mínimos** y el **máximo de los máximos** de todos los puntos que caen
en él (`np.minimum.at`/`np.maximum.at`, no una selección aleatoria/regular de puntos).

Un solo uso desde la Fase 7: la gráfica tipo #2 (AE), que diezma una **traza cruda
continua** (10000 muestras) a min/max por bin cuando se ve completa y restaura
resolución completa al hacer zoom (el llamador decide cuándo diezmar según el rango
visible, no esta función).

La gráfica tipo #1 **ya no diezma nada**: dibuja el min/max de todas las señales activas
a resolución completa (ver ``ui/components/graph_timeseries.py``). De ahí que aquí solo
quede :func:`build_vertical_segments`, compartida por ambas gráficas.
"""
from __future__ import annotations

import numpy as np


def bin_reduce_minmax(
    t: np.ndarray, y_min: np.ndarray, y_max: np.ndarray, n_bins: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Agrega ``(y_min, y_max)`` en ``n_bins`` bins fijos sobre el rango ``[t.min(), t.max()]``.

    Retorna ``(centros_de_bin, min_agregado, max_agregado)``, solo para bins con al
    menos un punto (bins vacíos se omiten, no se rellenan con 0 ni se interpolan).
    """
    if t.shape[0] == 0:
        empty = np.array([], dtype=np.float64)
        return empty, empty.copy(), empty.copy()

    t_min, t_max = float(t.min()), float(t.max())
    if t_min == t_max:
        return np.array([t_min]), np.array([float(y_min.min())]), np.array([float(y_max.max())])

    edges = np.linspace(t_min, t_max, n_bins + 1)
    bin_idx = np.clip(np.searchsorted(edges, t, side="right") - 1, 0, n_bins - 1)

    out_min = np.full(n_bins, np.inf, dtype=np.float64)
    out_max = np.full(n_bins, -np.inf, dtype=np.float64)
    np.minimum.at(out_min, bin_idx, y_min)
    np.maximum.at(out_max, bin_idx, y_max)

    occupied = out_min <= out_max
    centers = (edges[:-1] + edges[1:]) / 2.0
    return centers[occupied], out_min[occupied], out_max[occupied]


def decimate_signal_by_pixel(
    t: np.ndarray, y: np.ndarray, n_pixels: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Diezma una traza cruda continua (gráfica tipo #2, AE) a min/max por bin. Si el
    número de muestras ya cabe en los píxeles disponibles, retorna la traza completa
    (``y_min = y_max = y``, sin agregación real -- resolución completa)."""
    if t.shape[0] <= n_pixels:
        return t, y, y
    return bin_reduce_minmax(t, y, y, n_pixels)


# Entradas que aporta cada señal a la traza de segmentos verticales: mínimo, máximo y
# separador ``NaN``, en ese orden (ver :func:`build_vertical_segments`).
#
# Es **contractual**, no un detalle interno: la señal a la que pertenece el punto
# ``pointNumber`` de un evento de Plotly sobre esa traza es ``pointNumber //
# ENTRIES_PER_SEGMENT`` -- así resuelve el filtrado por lazo de la gráfica #1 qué señales
# encerró el usuario (``ui/callbacks/filtering.py::resolve_timeseries_selection_indices``).
ENTRIES_PER_SEGMENT = 3


def build_vertical_segments(
    x: np.ndarray, y_min: np.ndarray, y_max: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Intercala ``[y_min, y_max, NaN]`` por punto para dibujar N segmentos verticales
    en un solo trace de Plotly (patrón estándar "OHLC sin cuerpo"), evitando crear miles
    de traces individuales que degradarían el render. El ``NaN`` es el separador que
    corta la línea entre segmentos (Plotly lo serializa como ``null``).

    La posición de cada señal dentro del array resultante es fija y derivable en ambos
    sentidos (``señal k`` ocupa ``3k``, ``3k+1`` y ``3k+2``), ver
    :data:`ENTRIES_PER_SEGMENT`.

    Retorna arrays de numpy, no listas de Python: con decenas de miles de señales sin
    diezmar (gráfica #1 desde la Fase 7) una lista de objetos hace que la validación de
    Plotly tarde segundos, mientras que un array ``float64`` pasa por la ruta rápida
    (medido: ~500 ms contra ~40 ms para 12 484 señales UHF).
    """
    n = x.shape[0]
    xs = np.full(3 * n, np.nan, dtype=np.float64)
    ys = np.full(3 * n, np.nan, dtype=np.float64)
    xs[0::3] = x
    xs[1::3] = x
    ys[0::3] = y_min
    ys[1::3] = y_max
    return xs, ys
