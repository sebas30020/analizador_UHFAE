"""Energía disipada en una resistencia R (Ecuación 9 del PDF de referencia)."""
from __future__ import annotations

import numpy as np

from metrics.registry import MetricContext, metric


@metric(
    id="energia_joules",
    label="Energía Joules",
    regimen="puntual",
    dominio="tiempo",
    unit="J",
    version=1,
    params_schema={"R": 50.0},
)
def compute(ctx: MetricContext, R: float = 50.0, **params: object) -> np.ndarray:
    """E_i,J = (T_s/R) * sum(x_i[n]^2). Aplica al circuito de medición, no a la energía
    física depositada en el aislador (advertencia del PDF, Ec. 9)."""
    s = ctx.signal_matrix
    assert s is not None
    fs = ctx.fs_hz
    assert fs is not None
    ts = 1.0 / fs
    return (ts / R) * np.sum(s**2, axis=1)
