"""Pruebas de las métricas puntuales de tiempo, contra los "Ejemplo inmediato" del PDF
de referencia (resultado analítico conocido, PROMPT §11) cuando el PDF trae uno
numérico; contra un resultado analítico calculado a mano cuando no lo trae.
"""
import numpy as np
import pytest

from metrics.registry import MetricContext, discover_metrics, get_metric


@pytest.fixture(autouse=True, scope="module")
def _ensure_registered():
    discover_metrics()


def _ctx(data, fs_hz=1e9, timestamps=None):
    arr = np.atleast_2d(np.asarray(data, dtype=np.float64))
    return MetricContext(signal_matrix=arr, fs_hz=fs_hz, timestamps=timestamps)


# --- RMS (Ec. 29): x=[1,-1,1,-1] -> RMS=1 ---
def test_rms_pdf_example():
    result = get_metric("rms").compute(_ctx([1, -1, 1, -1]))
    assert np.isclose(result[0], 1.0)


# --- Crest Factor (Ec. 30): pico=5V, RMS=1V -> CF=5 ---
def test_crest_factor_pdf_example():
    data = [5.0] + [0.0] * 24  # peak=5, sum(x^2)=25, N=25 -> RMS=1
    result = get_metric("crest_factor").compute(_ctx(data))
    assert np.isclose(result[0], 5.0)


# --- Kurtosis (Ec. 26/27): calculado a mano para x=[1,-1,1,-1] ---
def test_kurtosis_known_analytic_result():
    # mu=0, sigma^2=mean(x^2)=1, mean(x^4)=1 -> K_pearson = 1/1 = 1.0, K_exceso = -2.0
    pearson = get_metric("kurtosis").compute(_ctx([1, -1, 1, -1]), type="pearson")
    excess = get_metric("kurtosis").compute(_ctx([1, -1, 1, -1]), type="excess")
    assert np.isclose(pearson[0], 1.0)
    assert np.isclose(excess[0], -2.0)
    assert np.isclose(pearson[0] - excess[0], 3.0)


# --- Skewness (Ec. 32): señal simétrica -> S=0 ---
def test_skewness_symmetric_signal_is_zero():
    result = get_metric("skewness").compute(_ctx([1, -1, 1, -1]))
    assert np.isclose(result[0], 0.0, atol=1e-12)


# --- Entropía Shannon (Ec. 16-20): p=[0.50,0.25,0.25,0.00] -> H=1.5 bits, H_norm=0.75 ---
def test_shannon_entropy_pdf_example():
    data = [-1.0] * 50 + [-0.1] * 25 + [0.4] * 25  # 100 muestras, peak=1.0
    result = get_metric("shannon_entropy").compute(_ctx(data), bins=4)
    assert np.isclose(result[0], 0.75, atol=1e-9)


# --- ZCR (Ec. 22): signos [+,+,-,-,+] -> 2 cambios en 4 transiciones -> ZCR=0.50 ---
def test_zcr_pdf_example():
    result = get_metric("zcr").compute(_ctx([1, 1, -1, -1, 1]), dead_zone=0.0)
    assert np.isclose(result[0], 0.5)


# --- Energía Relativa (Ec. 7): x=[1,-2,1] -> E_rel=6 ---
def test_energia_relativa_pdf_example():
    result = get_metric("energia_relativa").compute(_ctx([1, -2, 1]))
    assert np.isclose(result[0], 6.0)


# --- Energía V²s (Ec. 8): Ts=1ns, sum(x^2)=6 -> E_v2s=6e-9 ---
def test_energia_v2s_pdf_example():
    ctx = _ctx([1, -2, 1], fs_hz=1e9)  # Ts = 1/1e9 = 1ns
    result = get_metric("energia_v2s").compute(ctx)
    assert np.isclose(result[0], 6e-9)


# --- Energía Joules (Ec. 9): R=50, Ts=1ns, sum(x^2)=6 -> E_J=1.2e-10 ---
def test_energia_joules_pdf_example():
    ctx = _ctx([1, -2, 1], fs_hz=1e9)
    result = get_metric("energia_joules").compute(ctx, R=50.0)
    assert np.isclose(result[0], 1.2e-10)


# --- Delta T (Ec. 11): t_4=12.10, t_5=12.40 -> Δt_5=0.30 ---
def test_delta_t_pdf_example():
    ctx = MetricContext(timestamps=np.array([12.10, 12.40]))
    result = get_metric("delta_t").compute(ctx)
    assert result[0] == 0.0  # primer pulso, convención documentada
    assert np.isclose(result[1], 0.30)


# --- Log Delta T (Ec. 13): Δt=0.10, ε despreciable -> ln(0.10)=-2.303 ---
def test_log_delta_t_pdf_example():
    ctx = MetricContext(timestamps=np.array([0.0, 0.10]))
    result = get_metric("log_delta_t").compute(ctx, epsilon=1e-9)
    assert np.isclose(result[1], -2.303, atol=1e-3)


# --- VPP / Vmax: sanity checks (sin ecuación en el PDF) ---
def test_vpp_and_vmax_basic():
    ctx = _ctx([-3.0, 5.0, 0.0, -1.0])
    assert np.isclose(get_metric("vpp").compute(ctx)[0], 8.0)     # 5 - (-3)
    assert np.isclose(get_metric("vmax").compute(ctx)[0], 5.0)


def test_kurtosis_degenerate_signal_is_nan_not_masked():
    # Deviación consciente frente a analizador_nuevo/metricas.py (kurtosis.py docstring):
    # señal constante -> varianza 0 -> kurtosis indefinida, debe quedar NaN.
    ctx = _ctx([2.0, 2.0, 2.0, 2.0])
    result = get_metric("kurtosis").compute(ctx, type="excess")
    assert np.isnan(result[0])


def test_rise_time_on_synthetic_ramp():
    fs = 1e6  # 1 MHz -> 1 us por muestra
    ramp = np.linspace(0, 10, 101)  # rampa perfecta 0..10 en 101 muestras (100 us)
    ctx = _ctx(ramp, fs_hz=fs)
    result = get_metric("rise_time").compute(ctx, lower_pct=0.1, upper_pct=0.9)
    # 10% -> muestra ~10 (valor 1.0), 90% -> muestra ~90 (valor 9.0) => 80 muestras => 80 us = 80000 ns
    assert np.isclose(result[0], 80_000.0, rtol=0.05)
