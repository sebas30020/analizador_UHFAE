"""Valor RMS (Ecuación 29 del PDF de referencia)."""
from __future__ import annotations

import numpy as np

from metrics.registry import MetricContext, metric


@metric(id="rms", label="Valor RMS", regimen="puntual", dominio="tiempo", unit="V", version=1)
def compute(ctx: MetricContext, **params: object) -> np.ndarray:
    """RMS_i = sqrt((1/N_i) * sum(x_i[n]^2))."""
    s = ctx.signal_matrix
    assert s is not None
    return np.sqrt(np.mean(s**2, axis=1))
