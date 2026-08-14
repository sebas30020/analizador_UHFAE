import numpy as np
import pytest

from metrics.registry import MetricContext, discover_metrics, get_metric
from metrics.spectral import compute_spectrum


@pytest.fixture(autouse=True, scope="module")
def _ensure_registered():
    discover_metrics()


# --- Frec. Aprox. (Ec. 23): ZCR=0.10, F_s=1GHz -> f_aprox ≈ 50 MHz ---
def test_f_aprox_pdf_example():
    # 11 muestras con signos [+]*10 + [-] -> 1 cambio en 10 transiciones -> ZCR=0.10
    data = np.array([[1.0] * 10 + [-1.0]])
    ctx = MetricContext(signal_matrix=data, fs_hz=1e9)
    result = get_metric("f_aprox").compute(ctx, dead_zone=0.0)
    assert np.isclose(result[0], 5e7, rtol=1e-9)  # 50 MHz en Hz


def test_feq_close_to_dominant_frequency_of_pure_tone():
    fs = 3e9
    f0 = 50e6
    n = 3000
    t = np.arange(n) / fs
    signal = np.sin(2 * np.pi * f0 * t)[np.newaxis, :]

    spectrum = compute_spectrum(signal, fs_hz=fs, freq_limit_hz=100e6)
    ctx = MetricContext(spectrum=spectrum)
    result = get_metric("feq").compute(ctx)

    assert np.isclose(result[0], f0 / 1e6, rtol=0.1)  # en MHz, tolerancia 10%


def test_feq_reuses_precomputed_spectrum_no_internal_fft():
    # No debe requerir signal_matrix -- solo el espectro centralizado.
    definition = get_metric("feq")
    assert definition.requires_spectrum is True
