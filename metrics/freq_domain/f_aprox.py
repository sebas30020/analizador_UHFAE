"""Frecuencia aproximada equivalente vía ZCR (Ecuación 23 del PDF de referencia)."""
from __future__ import annotations

import numpy as np

from metrics.registry import MetricContext, metric
from metrics.time_domain.zcr import compute_zcr_array


@metric(
    id="f_aprox",
    label="Frec. Aprox.",
    regimen="puntual",
    dominio="frecuencia",
    unit="Hz",
    version=1,
    params_schema={"dead_zone": 0.01},
)
def compute(ctx: MetricContext, dead_zone: float = 0.01, **params: object) -> np.ndarray:
    """f_aprox ≈ ZCR_i * F_s / 2. Solo razonable para señales casi sinusoidales (aviso
    del PDF); para frecuencia real usar FFT/espectrograma/energía por bandas."""
    s = ctx.signal_matrix
    assert s is not None
    fs = ctx.fs_hz
    assert fs is not None
    zcr = compute_zcr_array(s, dead_zone=dead_zone)
    return (zcr * fs) / 2.0
