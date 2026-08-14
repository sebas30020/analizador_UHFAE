"""Diezmado min/max por bin de píxel (PROMPT maestro §5.1, §5.2).

"Esta agregación debe ser exacta, no un submuestreo" -- cada bin de píxel agrega el
**mínimo de los mínimos** y el **máximo de los máximos** de todos los puntos que caen
en él (`np.minimum.at`/`np.maximum.at`, no una selección aleatoria/regular de puntos).

Dos usos, misma función núcleo (:func:`bin_reduce_minmax`):
- Gráfica tipo #1: diezmar el min/max **ya precalculado por señal** (Fase 1) cuando el
  número de señales visibles supera los píxeles horizontales disponibles.
- Gráfica tipo #2 (AE): diezmar una **traza cruda continua** (10000 muestras) a min/max
  por bin cuando se ve completa; se restaura resolución completa al hacer zoom (el
  llamador decide cuándo diezmar según el rango visible, no esta función).
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


def decimate_minmax_by_pixel(
    timestamps: np.ndarray, minmax: np.ndarray, n_pixels: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Diezma el min/max por señal (gráfica tipo #1). Si el número de señales ya cabe
    en los píxeles disponibles, retorna los datos sin diezmar (resolución completa)."""
    if timestamps.shape[0] <= n_pixels:
        return timestamps, minmax[:, 0], minmax[:, 1]
    return bin_reduce_minmax(timestamps, minmax[:, 0], minmax[:, 1], n_pixels)


def decimate_signal_by_pixel(
    t: np.ndarray, y: np.ndarray, n_pixels: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Diezma una traza cruda continua (gráfica tipo #2, AE) a min/max por bin. Si el
    número de muestras ya cabe en los píxeles disponibles, retorna la traza completa
    (``y_min = y_max = y``, sin agregación real -- resolución completa)."""
    if t.shape[0] <= n_pixels:
        return t, y, y
    return bin_reduce_minmax(t, y, y, n_pixels)


def build_vertical_segments(
    x: np.ndarray, y_min: np.ndarray, y_max: np.ndarray
) -> tuple[list, list]:
    """Intercala ``[y_min, y_max, None]`` por punto para dibujar N segmentos verticales
    en un solo trace de Plotly (patrón estándar "OHLC sin cuerpo"), evitando crear miles
    de traces individuales que degradarían el render."""
    n = x.shape[0]
    xs: list = [None] * (3 * n)
    ys: list = [None] * (3 * n)
    xs[0::3] = x
    xs[1::3] = x
    ys[0::3] = y_min
    ys[1::3] = y_max
    return xs, ys
