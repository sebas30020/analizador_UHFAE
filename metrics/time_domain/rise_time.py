"""Tiempo de subida (10%-90% del pico en el flanco ascendente). Sin ecuación en el PDF
de referencia -- extensión de ``analizador_nuevo``, se porta tal cual."""
from __future__ import annotations

import numpy as np

from metrics.registry import MetricContext, metric


@metric(
    id="rise_time",
    label="Rise Time",
    regimen="puntual",
    dominio="tiempo",
    unit="ns",
    version=1,
    params_schema={"lower_pct": 0.1, "upper_pct": 0.9},
)
def compute(ctx: MetricContext, lower_pct: float = 0.1, upper_pct: float = 0.9, **params: object) -> np.ndarray:
    """Tiempo entre el primer cruce >= 10% del pico y el primer cruce >= 90% del pico,
    buscando solo hasta el índice del pico absoluto (flanco ascendente)."""
    s = ctx.signal_matrix
    assert s is not None
    fs = ctx.fs_hz
    assert fs is not None

    n_signals, n_points = s.shape
    s_abs = np.abs(s)
    rise_times = np.full(n_signals, np.nan, dtype=np.float64)

    for i in range(n_signals):
        row = s_abs[i]
        if np.any(np.isnan(row)):
            continue
        idx_max = int(np.argmax(row))
        peak_val = row[idx_max]
        if peak_val == 0:
            rise_times[i] = 0.0
            continue

        v10 = lower_pct * peak_val
        v90 = upper_pct * peak_val
        sub_sig = row[: idx_max + 1]
        idx10 = np.where(sub_sig >= v10)[0]
        idx90 = np.where(sub_sig >= v90)[0]
        if idx10.size > 0 and idx90.size > 0:
            rise_times[i] = max(0.0, (idx90[0] - idx10[0]) / fs * 1e9)
        else:
            rise_times[i] = 0.0

    return rise_times
