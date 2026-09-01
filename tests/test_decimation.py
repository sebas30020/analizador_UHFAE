import numpy as np

from viz.decimation import bin_reduce_minmax, build_vertical_segments, decimate_signal_by_pixel


def test_bin_reduce_minmax_is_exact_not_subsampled():
    # 4 puntos en 2 bins: bin0 con extremos [-5, 3], bin1 con extremos [1, 9] --
    # ninguno de esos extremos debe perderse por submuestreo.
    t = np.array([0.0, 1.0, 8.0, 9.0])
    y_min = np.array([-5.0, -1.0, 1.0, 2.0])
    y_max = np.array([3.0, 0.0, 5.0, 9.0])
    centers, mins, maxs = bin_reduce_minmax(t, y_min, y_max, n_bins=2)
    assert mins.tolist() == [-5.0, 1.0]
    assert maxs.tolist() == [3.0, 9.0]


def test_bin_reduce_minmax_omits_empty_bins():
    t = np.array([0.0, 0.1, 9.9, 10.0])  # todo en los extremos, bins del medio vacíos
    y_min = t.copy()
    y_max = t.copy()
    centers, mins, maxs = bin_reduce_minmax(t, y_min, y_max, n_bins=10)
    assert len(centers) < 10  # bins vacíos omitidos, no rellenados con 0


def test_bin_reduce_minmax_single_timestamp_collapses_to_one_bin():
    t = np.array([5.0, 5.0, 5.0])
    y_min = np.array([1.0, 2.0, 3.0])
    y_max = np.array([10.0, 20.0, 30.0])
    centers, mins, maxs = bin_reduce_minmax(t, y_min, y_max, n_bins=100)
    assert centers.tolist() == [5.0]
    assert mins.tolist() == [1.0]
    assert maxs.tolist() == [30.0]


def test_bin_reduce_minmax_empty_input():
    centers, mins, maxs = bin_reduce_minmax(np.array([]), np.array([]), np.array([]), n_bins=10)
    assert centers.shape[0] == 0


def test_decimate_signal_by_pixel_full_resolution_when_short():
    t = np.arange(100, dtype=np.float64)
    y = np.sin(t)
    t2, ymin, ymax = decimate_signal_by_pixel(t, y, n_pixels=2000)
    assert np.array_equal(t2, t)
    assert np.array_equal(ymin, y)
    assert np.array_equal(ymax, y)


def test_decimate_signal_by_pixel_preserves_peak_when_long():
    n = 10_000
    t = np.arange(n, dtype=np.float64)
    y = np.zeros(n)
    y[1234] = 500.0  # pico aislado que no debe desaparecer al diezmar
    y[7777] = -500.0

    t2, ymin, ymax = decimate_signal_by_pixel(t, y, n_pixels=200)
    assert len(t2) <= 200
    assert ymax.max() == 500.0
    assert ymin.min() == -500.0


def test_build_vertical_segments_shape_and_nan_separators():
    x = np.array([1.0, 2.0])
    y_min = np.array([-1.0, -2.0])
    y_max = np.array([1.0, 2.0])
    xs, ys = build_vertical_segments(x, y_min, y_max)
    assert xs.shape == (6,) and ys.shape == (6,)
    assert xs.dtype == np.float32
    assert ys.dtype == np.float32
    # separador cada 3 posiciones: corta la línea entre segmentos verticales
    assert np.isnan(xs[2]) and np.isnan(xs[5])
    assert np.isnan(ys[2]) and np.isnan(ys[5])
    assert ys[0] == -1.0 and ys[1] == 1.0
    assert ys[3] == -2.0 and ys[4] == 2.0


def test_build_vertical_segments_returns_float32_with_float64_inputs():
    # Entradas float64 típicas provenientes de SignalBlock.minmax y timestamps
    x = np.linspace(0.0, 100.0, 1000, dtype=np.float64)
    y_min = np.sin(x) - 1.0
    y_max = np.sin(x) + 1.0

    xs, ys = build_vertical_segments(x, y_min, y_max)

    assert xs.dtype == np.float32
    assert ys.dtype == np.float32
    assert xs.shape == (3000,)
    assert ys.shape == (3000,)

    # Separadores NaN regulares cada 3 elementos
    assert np.isnan(xs[2::3]).all()
    assert np.isnan(ys[2::3]).all()

    # Preservación de valores numéricos en float32
    np.testing.assert_allclose(xs[0::3], x.astype(np.float32), rtol=1e-6)
    np.testing.assert_allclose(xs[1::3], x.astype(np.float32), rtol=1e-6)
    np.testing.assert_allclose(ys[0::3], y_min.astype(np.float32), rtol=1e-6)
    np.testing.assert_allclose(ys[1::3], y_max.astype(np.float32), rtol=1e-6)


def test_build_vertical_segments_empty_input():
    x = np.array([], dtype=np.float64)
    y_min = np.array([], dtype=np.float64)
    y_max = np.array([], dtype=np.float64)

    xs, ys = build_vertical_segments(x, y_min, y_max)

    assert xs.dtype == np.float32
    assert ys.dtype == np.float32
    assert xs.shape == (0,)
    assert ys.shape == (0,)


def test_build_vertical_segments_large_dataset_fidelity():
    n = 20_000
    x = np.linspace(0.0, 500.0, n, dtype=np.float64)
    y_min = -np.random.rand(n)
    y_max = np.random.rand(n)

    xs, ys = build_vertical_segments(x, y_min, y_max)

    assert xs.dtype == np.float32
    assert ys.dtype == np.float32
    assert xs.shape == (3 * n,)
    assert ys.shape == (3 * n,)
    assert np.isnan(xs[2::3]).sum() == n
    assert np.isnan(ys[2::3]).sum() == n

