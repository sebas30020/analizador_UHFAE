"""Tasa de Energía (Ecuación 10 del PDF de referencia). Métrica de grupo intrínseca."""
from __future__ import annotations

import numpy as np

from metrics.registry import MetricContext, metric


@metric(id="tasa_energia", label="Tasa de Energía", regimen="grupo", dominio="tiempo", unit="rel/s", version=1)
def compute(ctx: MetricContext, **params: object) -> float:
    """Ė_w = sum(E_i,rel para i en el grupo) / T_w, con E_i,rel = sum(x_i[n]^2) (Ec. 7).

    ``T_w`` siempre explícito (mismo motivo que ``tasa_pulsos.py``). El grupo aquí ya
    contiene solo señales válidas (``metrics/engine.py``).
    """
    s = ctx.signal_matrix
    assert s is not None
    T_w = ctx.T_w
    assert T_w is not None and T_w > 0
    if s.shape[0] == 0:
        return 0.0
    e_rel_total = np.sum(s**2)
    return float(e_rel_total / T_w)
