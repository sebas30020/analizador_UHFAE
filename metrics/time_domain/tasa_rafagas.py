"""Tasa de Ráfagas (Ecuaciones 43-44, 47 del PDF de referencia). Métrica de grupo intrínseca."""
from __future__ import annotations

import numpy as np

from metrics.registry import MetricContext, metric


def count_bursts(timestamps: np.ndarray, tau: float, n_min: int) -> int:
    """Cuenta ráfagas: corridas máximas de pulsos consecutivos con Δt_i <= τ (Ec. 43)
    cuyo tamaño sea >= n_min (Ec. 44).

    Vectorizado mediante detección de bordes de corridas ("run-length"), equivalente al
    bucle de ``analizador_nuevo/metricas.py::calcular_rafagas`` (misma semántica: una
    corrida de L pares consecutivos con Δt<=τ forma una ráfaga de L+1 pulsos).
    """
    n = timestamps.shape[0]
    if n < n_min:
        return 0

    close = np.diff(timestamps) <= tau
    padded = np.concatenate(([False], close, [False]))
    edges = np.diff(padded.astype(np.int8))
    run_starts = np.where(edges == 1)[0]
    run_ends = np.where(edges == -1)[0]
    pulse_counts = (run_ends - run_starts) + 1
    return int(np.sum(pulse_counts >= n_min))


@metric(
    id="tasa_rafagas",
    label="Tasa de Ráfagas",
    regimen="grupo",
    dominio="tiempo",
    unit="Hz",
    version=1,
    params_schema={"tau": 0.01, "n_min": 5},
)
def compute(ctx: MetricContext, tau: float = 0.01, n_min: int = 5, **params: object) -> float:
    """R_burst,w = N_burst,w / T_w."""
    ts = ctx.timestamps
    assert ts is not None
    T_w = ctx.T_w
    assert T_w is not None and T_w > 0
    n_bursts = count_bursts(ts, tau=tau, n_min=n_min)
    return n_bursts / T_w
