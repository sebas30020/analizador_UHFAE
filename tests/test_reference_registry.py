import numpy as np

from ui.reference_registry import ReferenceSeriesRegistry


def test_mean_until_returns_none_for_unknown_option():
    registry = ReferenceSeriesRegistry()
    assert registry.mean_until("puntual:rms", 5.0) is None


def test_mean_until_computes_correct_mean_for_registered_series():
    registry = ReferenceSeriesRegistry()
    t = np.array([0.0, 1.0, 2.0, 3.0])
    v = np.array([10.0, 20.0, 30.0, 40.0])
    registry.replace_all({"puntual:rms": (t, v)})

    assert registry.mean_until("puntual:rms", 2.0) == np.mean([10.0, 20.0, 30.0])


def test_replace_all_discards_previous_generation():
    registry = ReferenceSeriesRegistry()
    registry.replace_all({"puntual:rms": (np.array([0.0]), np.array([100.0]))})
    registry.replace_all({"puntual:feq": (np.array([0.0]), np.array([5.0]))})

    # La serie de la generación anterior ya no está -- registro reemplazado, no apilado.
    assert registry.mean_until("puntual:rms", 5.0) is None
    assert registry.mean_until("puntual:feq", 5.0) == 5.0


def test_clear_empties_the_registry():
    registry = ReferenceSeriesRegistry()
    registry.replace_all({"puntual:rms": (np.array([0.0]), np.array([100.0]))})
    registry.clear()

    assert registry.mean_until("puntual:rms", 5.0) is None


def test_prefix_sums_reused_across_calls_not_rebuilt_each_time():
    # Recalcular la MEDIA en cada t es esperado; recalcular las SUMAS DE PREFIJO no.
    registry = ReferenceSeriesRegistry()
    t = np.array([0.0, 1.0, 2.0])
    v = np.array([1.0, 2.0, 3.0])
    registry.replace_all({"puntual:rms": (t, v)})

    registry.mean_until("puntual:rms", 1.0)
    first_prefix = registry._prefix_sums["puntual:rms"]
    registry.mean_until("puntual:rms", 2.0)
    second_prefix = registry._prefix_sums["puntual:rms"]

    assert first_prefix is second_prefix
