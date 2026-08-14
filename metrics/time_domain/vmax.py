"""Amplitud pico (con signo). Extensión de analizador_nuevo sin ecuación de referencia en el PDF."""
from __future__ import annotations

import numpy as np

from metrics.registry import MetricContext, metric


@metric(id="vmax", label="Vmax", regimen="puntual", dominio="tiempo", unit="V", version=1)
def compute(ctx: MetricContext, **params: object) -> np.ndarray:
    """Vmax_i = max(x_i[n]) (con signo, no valor absoluto -- distinto de A_i del PDF)."""
    s = ctx.signal_matrix
    assert s is not None
    return np.max(s, axis=1)
