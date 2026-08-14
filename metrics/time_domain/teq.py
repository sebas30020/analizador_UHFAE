"""Duración equivalente Teq del pulso. Sin ecuación en el PDF de referencia --
extensión de ``analizador_nuevo``, se porta tal cual (momento de 2º orden de v²(t)
respecto al tiempo)."""
from __future__ import annotations

import numpy as np

from metrics.registry import MetricContext, metric


@metric(id="teq", label="Tiempo Eq.", regimen="puntual", dominio="tiempo", unit="us", version=1)
def compute(ctx: MetricContext, **params: object) -> np.ndarray:
    s = ctx.signal_matrix
    assert s is not None
    fs = ctx.fs_hz
    assert fs is not None

    n_signals, n_points = s.shape
    t = np.arange(n_points) / fs
    s2 = s**2
    sum_s2 = np.sum(s2, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        t0 = np.sum(t[np.newaxis, :] * s2, axis=1) / sum_s2
        t_diff = t[np.newaxis, :] - t0[:, np.newaxis]
        t_eq = np.sqrt(np.sum((t_diff**2) * s2, axis=1) / sum_s2)
    return t_eq * 1e6  # microsegundos
