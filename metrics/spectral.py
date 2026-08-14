"""Cálculo espectral centralizado (PROMPT maestro §4.3).

Una sola FFT por señal, calculada aquí y reutilizada por todas las métricas de dominio
frecuencia que la necesiten dentro de la misma pasada del motor — ninguna métrica
recalcula su propia FFT. Vectorizado sobre el eje de señales con ``scipy.fft``.

Reutiliza exactamente la misma formulación que ``calcular_feq``/``calcular_tf_map_data``
de ``analizador_nuevo/metricas.py`` (rfft + magnitud al cuadrado, recortado a
``freq_limit_hz``), no la variante Welch (``welch_1ghz`` es un cálculo aparte para el
panel de espectro de la UI, fuera del alcance de las métricas puntuales de Fase 2).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import scipy.fft as sfft


@dataclass
class SpectrumResult:
    freqs_hz: np.ndarray   # (F,) frecuencias <= freq_limit_hz
    mag2: np.ndarray       # (N, F) magnitud al cuadrado del espectro, por señal


def compute_spectrum(signal_matrix: np.ndarray, fs_hz: float, freq_limit_hz: float) -> SpectrumResult:
    """FFT real vectorizada sobre ``(N, M)``, recortada a ``freq_limit_hz``.

    Filas con NaN (señales inválidas, ver ``core.normalization``) producen magnitud NaN
    en su fila correspondiente -- se preservan en la salida, el llamador decide si las
    descarta (el motor las excluye antes de retornar puntos, nunca aquí).
    """
    n_points = signal_matrix.shape[-1]
    fft_vals = sfft.rfft(signal_matrix, axis=-1, workers=-1)
    mag2 = np.abs(fft_vals) ** 2
    freqs = sfft.rfftfreq(n_points, d=1.0 / fs_hz)

    mask = freqs <= freq_limit_hz
    return SpectrumResult(freqs_hz=freqs[mask], mag2=mag2[..., mask])
