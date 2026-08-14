"""Entropía de Shannon normalizada (Ecuaciones 16-20 del PDF de referencia)."""
from __future__ import annotations

import numpy as np

from metrics.registry import MetricContext, metric


@metric(
    id="shannon_entropy",
    label="Entropía Shannon",
    regimen="puntual",
    dominio="tiempo",
    # Corrección respecto a analizador_datos.py (AUDITORIA...md §3): la fórmula ya
    # calcula H_i,norm (Ec. 20, adimensional en [0,1]), no H_i en bits (Ec. 19).
    # La GUI antigua etiquetaba "(bits)" por error; aquí la unidad declarada es correcta.
    unit="",
    version=1,
    params_schema={"bins": 64},
)
def compute(ctx: MetricContext, bins: int = 64, **params: object) -> np.ndarray:
    """H_i,norm = H_i / log2(B), con y_i[n]=x_i[n]/A_i, A_i=max|x_i[n]|, histograma en [-1,1].

    Vectorizado sobre el eje de señales (§9.1 del PROMPT: prohibido iterar señal por
    señal en Python) mediante un truco de ``np.bincount`` con offset por fila, en vez
    de ``np.histogram`` por fila.

    ``core.normalization.normalize`` deja una fila entera en NaN si la señal es
    inválida (nunca NaN parcial) -- se aprovecha esa garantía para detectar filas
    inválidas con una sola comprobación por fila.
    """
    s = ctx.signal_matrix
    assert s is not None
    n_signals, n_points = s.shape

    row_valid = ~np.any(np.isnan(s), axis=1)
    peak = np.max(np.abs(s), axis=1)
    usable = row_valid & (peak > 0)

    safe_peak = np.where(usable, peak, 1.0)
    y = np.where(usable[:, np.newaxis], s / safe_peak[:, np.newaxis], 0.0)
    y = np.clip(y, -1.0, 1.0 - 1e-12)  # el borde +1.0 exacto debe caer en el último bin, no fuera de rango

    bin_width = 2.0 / bins
    bin_idx = np.floor((y + 1.0) / bin_width).astype(np.int64)
    bin_idx = np.clip(bin_idx, 0, bins - 1)

    row_offset = (np.arange(n_signals) * bins)[:, np.newaxis]
    flat_idx = (bin_idx + row_offset).ravel()
    counts = np.bincount(flat_idx, minlength=n_signals * bins).reshape(n_signals, bins).astype(np.float64)

    p = counts / n_points
    with np.errstate(divide="ignore", invalid="ignore"):
        log2_p = np.where(p > 0, np.log2(p), 0.0)
    h_i = -np.sum(p * log2_p, axis=1)
    h_norm = h_i / np.log2(bins)

    return np.where(usable, h_norm, np.nan)
