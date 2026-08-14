"""Energía relativa (Ecuación 7 del PDF de referencia)."""
from __future__ import annotations

import numpy as np

from metrics.registry import MetricContext, metric


@metric(id="energia_relativa", label="Energía Relativa", regimen="puntual", dominio="tiempo", unit="", version=1)
def compute(ctx: MetricContext, **params: object) -> np.ndarray:
    """E_i,rel = sum(x_i[n]^2). Unidad: amplitud^2 * muestras (adimensional relativa)."""
    s = ctx.signal_matrix
    assert s is not None
    return np.sum(s**2, axis=1)
