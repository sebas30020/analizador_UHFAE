"""Separación temporal directa entre pulsos consecutivos (Ecuación 11 del PDF)."""
from __future__ import annotations

import numpy as np

from metrics.registry import MetricContext, metric


@metric(
    id="delta_t",
    label="Delta T",
    regimen="puntual",
    dominio="tiempo",
    unit="s",
    version=1,
    requires_global_timestamps=True,
)
def compute(ctx: MetricContext, **params: object) -> np.ndarray:
    """Δt_i = t_i - t_{i-1} (i>=2, Ec. 11). Convención: Δt del primer pulso = 0 (el PDF
    lo deja indefinido para i=1; se documenta como corresponde con
    AUDITORIA_FORMULAS_PDF_vs_metricas.md §5).

    ``ctx.timestamps`` debe ser el vector de timestamps de las señales **válidas**
    solamente (responsabilidad de ``metrics/engine.py``, ver docstring de
    ``requires_global_timestamps`` ahí) -- así Δt_i siempre mide contra el pulso válido
    inmediatamente anterior, nunca contra uno excluido de las métricas.
    """
    ts = ctx.timestamps
    assert ts is not None
    dt = np.zeros_like(ts, dtype=np.float64)
    if ts.shape[0] > 1:
        dt[1:] = np.diff(ts)
    return dt
