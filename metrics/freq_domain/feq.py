"""Frecuencia equivalente. Sin ecuación en el PDF de referencia -- extensión de
``analizador_nuevo`` (momento de 2º orden del espectro de potencia), se porta tal cual."""
from __future__ import annotations

import numpy as np

from metrics.registry import MetricContext, metric


@metric(
    id="feq",
    label="Frecuencia Eq.",
    regimen="puntual",
    dominio="frecuencia",
    unit="MHz",
    version=1,
    requires_spectrum=True,
)
def compute(ctx: MetricContext, **params: object) -> np.ndarray:
    """F_eq = sqrt(sum(f^2 * |X(f)|^2) / sum(|X(f)|^2)), recortado a freq_limit_hz.

    Usa el espectro centralizado (``metrics/spectral.py``) calculado una sola vez por
    bloque y compartido entre todas las métricas espectrales de la pasada (§4.3 del
    PROMPT) -- no vuelve a llamar a la FFT aquí.
    """
    spectrum = ctx.spectrum
    assert spectrum is not None
    freqs = spectrum.freqs_hz
    mag2 = spectrum.mag2

    sum_mag2 = np.sum(mag2, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        f_eq = np.sqrt(np.sum((freqs[np.newaxis, :] ** 2) * mag2, axis=1) / sum_mag2)
    return f_eq / 1e6
