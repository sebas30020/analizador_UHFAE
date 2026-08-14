"""Kurtosis (Ecuaciones 24-27 del PDF de referencia)."""
from __future__ import annotations

import numpy as np
from scipy.stats import kurtosis as _scipy_kurtosis

from metrics.registry import MetricContext, metric


@metric(
    id="kurtosis",
    label="Kurtosis",
    regimen="puntual",
    dominio="tiempo",
    unit="",
    version=1,
    params_schema={"type": "pearson"},
)
def compute(ctx: MetricContext, type: str = "pearson", **params: object) -> np.ndarray:
    """K_i (Ec. 26, Pearson, gaussiana=3) o K_exceso,i (Ec. 27, gaussiana=0).

    ``scipy.stats.kurtosis`` con los defaults (``fisher=True, bias=True``) calcula
    directamente K_exceso,i con momentos poblacionales (divisor N_i, igual que Ec. 24-25
    del PDF). Para 'pearson' se suma 3 (Ec. 27 -> Ec. 26).

    Deviación consciente frente a ``analizador_nuevo/metricas.py``: aquí NO se aplica
    ``np.nan_to_num`` para enmascarar señales degeneradas (varianza cero) con un valor
    falso (0, o 3 en Pearson). Un pulso con desviación estándar 0 tiene kurtosis
    matemáticamente indefinida (0/0); se deja como NaN y ``metrics/engine.py`` decide
    si excluye ese punto, en vez de fabricar un valor que aparente ser gaussiano real.
    """
    s = ctx.signal_matrix
    assert s is not None
    k_exceso = _scipy_kurtosis(s, axis=1, nan_policy="omit")
    if type == "pearson":
        return k_exceso + 3.0
    return k_exceso
