"""Logaritmo natural del intervalo entre pulsos (Ecuación 13 del PDF)."""
from __future__ import annotations

import numpy as np

from metrics.registry import MetricContext, metric
from metrics.time_domain.delta_t import compute as _compute_delta_t


@metric(
    id="log_delta_t",
    label="Log Delta T",
    regimen="puntual",
    dominio="tiempo",
    unit="",
    version=1,
    params_schema={"epsilon": 1e-9},
    requires_global_timestamps=True,
)
def compute(ctx: MetricContext, epsilon: float = 1e-9, **params: object) -> np.ndarray:
    """ℓ_i = ln((Δt_i + ε) / 1s)."""
    dt = _compute_delta_t(ctx)
    return np.log(dt + epsilon)
