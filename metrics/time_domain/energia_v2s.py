"""Energía escalada por tiempo (Ecuación 8 del PDF de referencia)."""
from __future__ import annotations

import numpy as np

from metrics.registry import MetricContext, metric


@metric(id="energia_v2s", label="Energía V²s", regimen="puntual", dominio="tiempo", unit="V²s", version=1)
def compute(ctx: MetricContext, **params: object) -> np.ndarray:
    """E_i,V²s = T_s * sum(x_i[n]^2), T_s = 1/F_s."""
    s = ctx.signal_matrix
    assert s is not None
    fs = ctx.fs_hz
    assert fs is not None
    ts = 1.0 / fs
    return ts * np.sum(s**2, axis=1)
