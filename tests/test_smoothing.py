import numpy as np

from viz.smoothing import auto_gap_threshold, smooth, split_on_gaps


def test_rolling_mean_matches_naive_reference_on_small_array():
    # Referencia ingenua: promedio de todos los puntos dentro de +-window/2 de cada
    # abscisa de consulta, calculada con un bucle -- exactamente lo que la
    # implementación vectorizada de producción no debe hacer, pero es válida como
    # oráculo de prueba.
    x = np.array([0.0, 1.0, 2.0, 3.0, 10.0, 11.0, 12.0])
    y = np.array([1.0, 2.0, 3.0, 4.0, 100.0, 200.0, 300.0])
    window = 2.5

    x_out, y_out = smooth(x, y, method="media_movil_temporal", window=window, x_query=x)

    expected = []
    for xq in x:
        mask = np.abs(x - xq) <= window / 2.0
        expected.append(y[mask].mean())
    assert np.array_equal(x_out, x)
    np.testing.assert_allclose(y_out, expected)


def test_rolling_mean_handles_irregular_spacing():
    # Dos racimos densos separados por un hueco grande: una ventana temporal de 1.0 no
    # debe mezclar el racimo de la izquierda con el de la derecha.
    x = np.array([0.0, 0.1, 0.2, 50.0, 50.1, 50.2])
    y = np.array([10.0, 10.0, 10.0, 1000.0, 1000.0, 1000.0])
    _, y_out = smooth(x, y, method="media_movil_temporal", window=1.0, x_query=x)
    assert np.allclose(y_out[:3], 10.0)
    assert np.allclose(y_out[3:], 1000.0)


def test_rolling_mean_edges_shrink_without_artifact():
    # En los bordes la ventana se encoge (menos vecinos disponibles) en vez de
    # rellenar o recortar -- el primer y último punto deben coincidir consigo mismos
    # si están más lejos que window/2 de cualquier otro punto.
    x = np.array([0.0, 100.0, 200.0])
    y = np.array([5.0, 50.0, 500.0])
    _, y_out = smooth(x, y, method="media_movil_temporal", window=1.0, x_query=x)
    assert y_out.tolist() == [5.0, 50.0, 500.0]


def test_rolling_mean_excludes_nan_without_propagating():
    x = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    y = np.array([10.0, np.nan, 10.0, 10.0, 10.0])
    _, y_out = smooth(x, y, method="media_movil_temporal", window=10.0, x_query=x)
    # el promedio ignora el NaN de entrada (se excluye, no se trata como 0)
    assert np.isclose(y_out[0], 10.0)
    assert not np.isnan(y_out[0])


def test_rolling_mean_all_nan_window_yields_nan_not_zero():
    x = np.array([0.0, 1.0])
    y = np.array([np.nan, np.nan])
    _, y_out = smooth(x, y, method="media_movil_temporal", window=10.0, x_query=x)
    assert np.all(np.isnan(y_out))


def test_rolling_mean_x_query_reduces_point_count():
    x = np.arange(5000, dtype=np.float64)
    y = np.sin(x / 100.0)
    x_out, y_out = smooth(x, y, method="media_movil_temporal", window=50.0)
    assert x_out.shape[0] <= 2000
    assert x_out.shape[0] == y_out.shape[0]


def test_rolling_median_is_robust_to_spike():
    y = np.array([1.0, 1.0, 1.0, 1000.0, 1.0, 1.0, 1.0])
    x = np.arange(y.shape[0], dtype=np.float64)
    _, y_out = smooth(x, y, method="mediana_movil_puntos", window=5.0)
    # la mediana en torno al pico no debe arrastrarse hacia 1000
    assert y_out[3] < 10.0


def test_smooth_unknown_method_raises():
    x = np.array([0.0, 1.0])
    y = np.array([1.0, 2.0])
    try:
        smooth(x, y, method="no_existe", window=1.0)  # type: ignore[arg-type]
        assert False, "debía lanzar ValueError"
    except ValueError:
        pass


def test_smooth_empty_input_does_not_raise():
    x = np.array([])
    y = np.array([])
    x_out, y_out = smooth(x, y, method="media_movil_temporal", window=1.0)
    assert x_out.shape[0] == 0 and y_out.shape[0] == 0


def test_smooth_single_point_does_not_raise():
    x = np.array([5.0])
    y = np.array([42.0])
    x_out, y_out = smooth(x, y, method="media_movil_temporal", window=1.0)
    assert y_out.tolist() == [42.0]


def test_auto_gap_threshold_regular_series_is_large_relative_to_step():
    x = np.arange(0.0, 100.0, 1.0)
    threshold = auto_gap_threshold(x)
    assert threshold == 5.0  # 5 * mediana(diff) = 5 * 1.0


def test_split_on_gaps_regular_series_unchanged():
    x = np.arange(0.0, 10.0, 1.0)
    y = np.arange(10.0)
    x_out, y_out = split_on_gaps(x, y)
    assert x_out.shape[0] == x.shape[0]
    assert not np.any(np.isnan(y_out))


def test_split_on_gaps_inserts_one_nan_per_gap():
    # Dos huecos grandes en una serie por lo demás regular (paso 1.0): deben insertarse
    # exactamente dos separadores NaN, uno por hueco.
    x = np.array([0.0, 1.0, 2.0, 100.0, 101.0, 102.0, 500.0, 501.0])
    y = np.arange(x.shape[0], dtype=np.float64)
    x_out, y_out = split_on_gaps(x, y)
    assert np.sum(np.isnan(y_out)) == 2
    # el orden se conserva y el tamaño crece exactamente en el número de huecos
    assert x_out.shape[0] == x.shape[0] + 2
    assert np.all(np.diff(x_out) >= 0)


def test_split_on_gaps_explicit_threshold_overrides_auto():
    x = np.array([0.0, 1.0, 3.0])  # hueco de 2.0 entre el 2do y 3er punto
    y = np.array([1.0, 2.0, 3.0])
    x_out, y_out = split_on_gaps(x, y, max_gap=1.5)
    assert np.sum(np.isnan(y_out)) == 1
    x_out2, y_out2 = split_on_gaps(x, y, max_gap=10.0)
    assert np.sum(np.isnan(y_out2)) == 0


def test_split_on_gaps_empty_input_does_not_raise():
    x_out, y_out = split_on_gaps(np.array([]), np.array([]))
    assert x_out.shape[0] == 0
