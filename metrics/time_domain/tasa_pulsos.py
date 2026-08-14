"""Tasa de Pulsos (Ecuación 15 del PDF de referencia). Métrica de grupo intrínseca."""
from __future__ import annotations

from metrics.registry import MetricContext, metric


@metric(id="tasa_pulsos", label="Tasa de Pulsos", regimen="grupo", dominio="tiempo", unit="Hz", version=1)
def compute(ctx: MetricContext, **params: object) -> float:
    """λ_w = N_w / T_w. ``T_w`` es siempre la duración declarada de la ventana, nunca
    inferida (AUDITORIA_FORMULAS_PDF_vs_metricas.md §4) -- la provee
    ``metrics/engine.py`` desde ``core/grouping.py``, nunca este plugin.

    ``ctx.timestamps`` contiene solo las señales **válidas** del grupo (ver nota de
    diseño en ``metrics/engine.py``): N_w cuenta pulsos con metadatos utilizables.
    """
    ts = ctx.timestamps
    assert ts is not None
    T_w = ctx.T_w
    assert T_w is not None and T_w > 0
    return ts.shape[0] / T_w
