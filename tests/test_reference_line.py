import numpy as np

from viz.reference_line import build_prefix_sums, mean_until


def test_mean_until_matches_naive_reference_on_small_array():
    # Referencia ingenua: máscara + np.nanmean, exactamente lo que la implementación
    # vectorizada de producción no debe hacer en cada cambio de t, pero es válida como
    # oráculo de prueba.
    x = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    y = np.array([10.0, 20.0, 30.0, 40.0, 50.0, 60.0])
    prefix = build_prefix_sums(x, y)

    for t in (0.0, 1.0, 2.5, 3.0, 5.0, 100.0):
        expected = np.nanmean(y[x <= t]) if np.any(x <= t) else None
        result = mean_until(prefix, t)
        if expected is None:
            assert result is None
        else:
            assert result == expected


def test_upper_bound_is_inclusive():
    x = np.array([0.0, 1.0, 2.0])
    y = np.array([1.0, 2.0, 3.0])
    prefix = build_prefix_sums(x, y)

    # t == 1.0 debe incluir la muestra en x=1.0 (side="right").
    assert mean_until(prefix, 1.0) == np.mean([1.0, 2.0])


def test_nan_values_excluded_not_treated_as_zero():
    x = np.array([0.0, 1.0, 2.0, 3.0])
    y = np.array([10.0, np.nan, 30.0, np.nan])
    prefix = build_prefix_sums(x, y)

    # Si el NaN se tratara como cero, la media hasta t=3 sería (10+0+30+0)/4 = 10.0.
    # Excluyéndolo correctamente es (10+30)/2 = 20.0.
    assert mean_until(prefix, 3.0) == 20.0


def test_t_less_or_equal_zero_returns_none():
    x = np.array([1.0, 2.0, 3.0])
    y = np.array([10.0, 20.0, 30.0])
    prefix = build_prefix_sums(x, y)

    assert mean_until(prefix, 0.0) is None
    assert mean_until(prefix, -5.0) is None


def test_t_greater_than_experiment_duration_uses_all_data():
    x = np.array([0.0, 1.0, 2.0])
    y = np.array([10.0, 20.0, 30.0])
    prefix = build_prefix_sums(x, y)

    assert mean_until(prefix, 1000.0) == np.mean(y)


def test_empty_series_returns_none():
    x = np.array([])
    y = np.array([])
    prefix = build_prefix_sums(x, y)

    assert mean_until(prefix, 5.0) is None


def test_single_sample_in_interval_is_valid():
    x = np.array([0.5])
    y = np.array([42.0])
    prefix = build_prefix_sums(x, y)

    assert mean_until(prefix, 0.5) == 42.0
    assert mean_until(prefix, 0.4) is None


def test_all_nan_in_interval_returns_none():
    x = np.array([0.0, 1.0])
    y = np.array([np.nan, np.nan])
    prefix = build_prefix_sums(x, y)

    assert mean_until(prefix, 1.0) is None
