"""Skewness (Ecuación 32 del PDF de referencia)."""
from __future__ import annotations

import numpy as np
from scipy.stats import skew as _scipy_skew

from metrics.registry import MetricContext, metric


@metric(id="skewness", label="Skewness", regimen="puntual", dominio="tiempo", unit="", version=1)
def compute(ctx: MetricContext, **params: object) -> np.ndarray:
    """S_i = (1/N_i) * sum(((x_i[n]-mu_i)/sigma_i)^3).

    ``scipy.stats.skew`` con defaults (``bias=True``) usa momentos poblacionales,
    coincide exactamente. Sin ``np.nan_to_num`` (ver nota en ``kurtosis.py``): señales
    de varianza cero producen NaN, no un 0 fabricado.
    """
    s = ctx.signal_matrix
    assert s is not None
    return _scipy_skew(s, axis=1, nan_policy="omit")
