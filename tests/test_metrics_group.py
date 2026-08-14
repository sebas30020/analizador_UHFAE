import numpy as np
import pytest

from metrics.registry import MetricContext, discover_metrics, get_metric
from metrics.time_domain.tasa_rafagas import count_bursts


@pytest.fixture(autouse=True, scope="module")
def _ensure_registered():
    discover_metrics()


# --- Tasa de Pulsos (Ec. 15): 300 pulsos en 60s -> 5 Hz ---
def test_tasa_pulsos_pdf_example():
    ctx = MetricContext(timestamps=np.arange(300, dtype=np.float64), T_w=60.0)
    result = get_metric("tasa_pulsos").compute(ctx)
    assert np.isclose(result, 5.0)


# --- Tasa de Energía (Ec. 10): energía total 300 u.r. en ventana 60s -> 5 u.r./s ---
def test_tasa_energia_matches_sum_of_energia_relativa_over_t_w():
    # 3 señales con E_rel = 100 cada una (sum(x^2)=100 -> x=[10,0,...] 1 muestra de 10)
    data = np.array([[10.0], [10.0], [10.0]])
    ctx = MetricContext(signal_matrix=data, T_w=60.0)
    result = get_metric("tasa_energia").compute(ctx)
    assert np.isclose(result, 300.0 / 60.0)  # 5.0


# --- Tasa de Ráfagas (Ec. 47): 6 ráfagas en 60s -> 0.1 ráfagas/s ---
def test_tasa_rafagas_pdf_example_rate():
    # Construir timestamps con exactamente 6 ráfagas de 5 pulsos cada una, separadas
    # ampliamente entre sí (> tau) dentro de una ventana declarada de 60s.
    tau = 0.01
    ts_parts = []
    t0 = 0.0
    for _ in range(6):
        burst = t0 + np.arange(5) * (tau / 2)  # 5 pulsos muy juntos (misma ráfaga)
        ts_parts.append(burst)
        t0 = burst[-1] + 1.0  # separación grande entre ráfagas
    timestamps = np.concatenate(ts_parts)

    ctx = MetricContext(timestamps=timestamps, T_w=60.0)
    result = get_metric("tasa_rafagas").compute(ctx, tau=tau, n_min=5)
    assert np.isclose(result, 6 / 60.0)


# --- count_bursts: ejemplos textuales del PDF §17 ---
def test_count_bursts_close_pulses_same_burst_far_pulses_not():
    tau = 0.010  # 10 ms
    # dos pulsos separados 4ms -> misma ráfaga (si hay suficientes para n_min)
    ts = np.array([0.0, 0.004])
    assert count_bursts(ts, tau=tau, n_min=2) == 1
    # dos pulsos separados 50ms -> no
    ts2 = np.array([0.0, 0.050])
    assert count_bursts(ts2, tau=tau, n_min=2) == 0


def test_count_bursts_n_min_threshold():
    tau = 0.010
    # grupo de 3 pulsos cercanos (n_min=5) -> NO cuenta
    close_3 = np.array([0.0, 0.004, 0.008])
    assert count_bursts(close_3, tau=tau, n_min=5) == 0
    # grupo de 8 pulsos cercanos (n_min=5) -> SI cuenta (1 ráfaga)
    close_8 = np.arange(8) * 0.004
    assert count_bursts(close_8, tau=tau, n_min=5) == 1


def test_count_bursts_multiple_separated_bursts():
    tau = 0.010
    burst_a = np.arange(5) * 0.004                       # 0.000 .. 0.016
    burst_b = burst_a + 1.0                                # separada 1s de la anterior
    ts = np.concatenate([burst_a, burst_b])
    assert count_bursts(ts, tau=tau, n_min=5) == 2


def test_tasa_pulsos_and_tasa_energia_require_positive_t_w():
    with pytest.raises(AssertionError):
        get_metric("tasa_pulsos").compute(MetricContext(timestamps=np.array([0.0]), T_w=0.0))


# --- Normalización perezosa de la matriz de señales (Fase 7, optimización de §9.2) ---


def _contexto_perezoso(**kwargs):
    """Contexto cuya matriz solo existe si alguien la lee; ``llamadas`` cuenta cuántas
    veces se materializó de verdad."""
    llamadas = {"n": 0}

    def factory():
        llamadas["n"] += 1
        return np.array([[1.0, 2.0], [3.0, 4.0]])

    return MetricContext(signal_matrix_factory=factory, **kwargs), llamadas


def test_metrica_que_solo_usa_timestamps_no_materializa_la_matriz():
    ctx, llamadas = _contexto_perezoso(timestamps=np.arange(300, dtype=np.float64), T_w=60.0)
    assert np.isclose(get_metric("tasa_pulsos").compute(ctx), 5.0)
    assert llamadas["n"] == 0  # normalizar aquí es trabajo puro desperdiciado


def test_metrica_que_usa_la_matriz_la_materializa_una_sola_vez():
    ctx, llamadas = _contexto_perezoso(T_w=60.0)
    get_metric("tasa_energia").compute(ctx)
    _ = ctx.signal_matrix  # segunda lectura: debe salir de la memoización
    assert llamadas["n"] == 1


def test_matriz_explicita_sigue_teniendo_prioridad_y_no_usa_factory():
    ctx, llamadas = _contexto_perezoso(T_w=60.0)
    directo = MetricContext(signal_matrix=np.array([[10.0]]), T_w=60.0)
    assert directo.signal_matrix.tolist() == [[10.0]]
    assert llamadas["n"] == 0
