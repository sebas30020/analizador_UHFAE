"""Crest Factor (Ecuación 30 del PDF de referencia)."""
from __future__ import annotations

import numpy as np

from metrics.registry import MetricContext, metric


@metric(id="crest_factor", label="Factor de cresta", regimen="puntual", dominio="tiempo", unit="", version=1)
def compute(ctx: MetricContext, **params: object) -> np.ndarray:
    """CF_i = max_n|x_i[n]| / RMS_i.

    El PDF advierte: "Si RMS_i es muy pequeña, CF_i puede volverse artificialmente
    grande. No calcule CF en pulsos sin señal real" -- no se aplica ningún piso
    artificial al RMS aquí; un RMS cercano a 0 producirá un CF grande de forma honesta,
    en vez de silenciarlo con un valor mínimo arbitrario.
    """
    s = ctx.signal_matrix
    assert s is not None
    v_peak = np.max(np.abs(s), axis=1)
    rms = np.sqrt(np.mean(s**2, axis=1))
    with np.errstate(divide="ignore", invalid="ignore"):
        return v_peak / rms
