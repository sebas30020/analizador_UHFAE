"""Voltaje pico a pico. Extensión de analizador_nuevo sin ecuación de referencia en el PDF."""
from __future__ import annotations

import numpy as np

from metrics.registry import MetricContext, metric


@metric(id="vpp", label="VPP", regimen="puntual", dominio="tiempo", unit="V", version=1)
def compute(ctx: MetricContext, **params: object) -> np.ndarray:
    """VPP_i = max(x_i[n]) - min(x_i[n])."""
    s = ctx.signal_matrix
    assert s is not None
    return np.max(s, axis=1) - np.min(s, axis=1)
