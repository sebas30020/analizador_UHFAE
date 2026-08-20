"""``viz/maps.py`` — datos de los mapas de separación #4/#5 (``archivos_md/
prompt-mapas2d3d.md``). Sin Dash, sin Plotly: solo la combinación de ejes puntuales ya
calculados vía la misma fachada de caché que usa la gráfica tipo #3.
"""
import numpy as np
import pytest

from cache.backend import SqliteHdf5CacheBackend
from core.models import SensorConfig, SignalBlock
from metrics.registry import discover_metrics
from viz.maps import MapDataset, build_map_dataset, resolve_map_highlight_coords


@pytest.fixture(autouse=True, scope="module")
def _ensure_registered():
    discover_metrics()


@pytest.fixture
def cache(tmp_path):
    backend = SqliteHdf5CacheBackend(tmp_path / "cache")
    yield backend
    backend.close()


@pytest.fixture
def sensor_config() -> SensorConfig:
    return SensorConfig(
        name="UHF", hdf5_group="signals", fs_hz=1e9, n_samples=4, freq_limit_hz=1e8,
        axis_unit="us", axis_scale=1e6, target_block_bytes=1024,
    )


def _block(data, timestamps, valid_mask=None) -> SignalBlock:
    data = np.asarray(data, dtype=np.float32)
    n = data.shape[0]
    timestamps = np.asarray(timestamps, dtype=np.float64)
    trigger = np.full(n, 0.01)
    vrange = np.full(n, 1.0)
    valid_mask = np.full(n, True) if valid_mask is None else np.asarray(valid_mask, dtype=bool)
    minmax = np.stack([data.min(axis=1), data.max(axis=1)], axis=1).astype(np.float32)
    return SignalBlock(data=data, timestamps=timestamps, trigger=trigger, vrange=vrange,
                        valid_mask=valid_mask, minmax=minmax)


# --- Alineación de ejes ---------------------------------------------------------------

def test_two_axes_aligned_by_signal_not_recomputed(cache, sensor_config):
    block = _block([[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]], [0.0, 1.0, 2.0])
    result = build_map_dataset(cache, block, sensor_config, "ds1", {"x": "vmax", "y": "rms"})
    assert np.array_equal(result.signal_indices, [0, 1, 2])
    assert np.allclose(result.coords["x"], [4.0, 8.0, 12.0])  # vmax = max de cada fila
    assert result.coords["y"].shape == (3,)
    assert result.omitted_counts == {"x": 0, "y": 0}


def test_three_axes_for_3d_map(cache, sensor_config):
    block = _block([[1, 2, 3, 4], [5, 6, 7, 8]], [0.0, 1.0])
    result = build_map_dataset(cache, block, sensor_config, "ds1", {"x": "vmax", "y": "rms", "z": "vpp"})
    assert set(result.coords) == {"x", "y", "z"}
    assert result.signal_indices.shape == (2,)
    for values in result.coords.values():
        assert values.shape == (2,)


def test_axis_labels_include_unit(cache, sensor_config):
    block = _block([[1, 2, 3, 4]], [0.0])
    result = build_map_dataset(cache, block, sensor_config, "ds1", {"x": "vmax"})
    assert result.axis_labels["x"] == "Vmax (V)"


def test_same_metric_on_two_axes_is_allowed(cache, sensor_config):
    # El prompt permite repetir métrica en dos ejes (con advertencia discreta, resuelta
    # en la capa de UI) -- aquí solo se verifica que no falla y que ambos ejes son
    # idénticos.
    block = _block([[1, 2, 3, 4], [5, 6, 7, 8]], [0.0, 1.0])
    result = build_map_dataset(cache, block, sensor_config, "ds1", {"x": "vmax", "y": "vmax"})
    assert np.array_equal(result.coords["x"], result.coords["y"])


# --- Índice de señal: valid_mask + active_mask -----------------------------------------

def test_signal_indices_exclude_invalid_signals(cache, sensor_config):
    block = _block(
        [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]], [0.0, 1.0, 2.0],
        valid_mask=[True, False, True],
    )
    result = build_map_dataset(cache, block, sensor_config, "ds1", {"x": "vmax", "y": "rms"})
    assert np.array_equal(result.signal_indices, [0, 2])


def test_active_mask_filters_live_without_recomputing_cache(cache, sensor_config):
    block = _block([[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]], [0.0, 1.0, 2.0])
    active_mask = np.array([True, False, True])

    full = build_map_dataset(cache, block, sensor_config, "ds1", {"x": "vmax", "y": "rms"})
    assert np.array_equal(full.signal_indices, [0, 1, 2])

    filtered = build_map_dataset(
        cache, block, sensor_config, "ds1", {"x": "vmax", "y": "rms"}, active_mask=active_mask
    )
    assert np.array_equal(filtered.signal_indices, [0, 2])
    assert np.allclose(filtered.coords["x"], [4.0, 12.0])

    # El régimen puntual nunca bypasea el caché (cache/service.py) -- una sola entrada
    # por métrica, sin filtrar, reutilizada por ambas llamadas.
    assert cache.stats()["total_entries"] == 2


# --- NaN / inf: se omiten y se cuentan -------------------------------------------------

def test_nan_axis_drops_the_point_and_is_counted(cache, sensor_config):
    # Kurtosis es NaN para una señal de varianza cero (metrics/time_domain/kurtosis.py):
    # caso real de "métrica no disponible para una señal", no un valor inyectado a mano.
    block = _block(
        [[1, 1, 1, 1], [1, 2, 3, 4], [5, 6, 7, 8]], [0.0, 1.0, 2.0],
    )
    result = build_map_dataset(cache, block, sensor_config, "ds1", {"x": "vmax", "y": "kurtosis"})
    assert result.omitted_counts["y"] == 1
    assert result.omitted_counts["x"] == 0
    assert np.array_equal(result.signal_indices, [1, 2])  # la señal 0 (constante) se omite
    assert result.coords["x"].shape == (2,)
    assert result.coords["y"].shape == (2,)


def test_nan_in_any_axis_drops_the_row_from_all_axes(cache, sensor_config):
    block = _block(
        [[1, 1, 1, 1], [1, 2, 3, 4]], [0.0, 1.0],
    )
    result = build_map_dataset(cache, block, sensor_config, "ds1", {"x": "vmax", "y": "kurtosis", "z": "rms"})
    # La fila 0 se omite en TODOS los ejes, no solo en "y" (una coordenada indefinida
    # invalida el punto completo).
    assert result.signal_indices.shape == (1,)
    for values in result.coords.values():
        assert values.shape == (1,)


# --- Validación de régimen --------------------------------------------------------------

def test_group_regime_metric_is_rejected(cache, sensor_config):
    block = _block([[1, 2, 3, 4], [5, 6, 7, 8]], [0.0, 1.0])
    with pytest.raises(ValueError, match="régimen"):
        build_map_dataset(cache, block, sensor_config, "ds1", {"x": "vmax", "y": "tasa_pulsos"})


def test_empty_metric_ids_raises(cache, sensor_config):
    block = _block([[1, 2, 3, 4]], [0.0])
    with pytest.raises(ValueError):
        build_map_dataset(cache, block, sensor_config, "ds1", {})


# --- resolve_map_highlight_coords (resaltado bidireccional, §5.3 del prompt) ------------

def _highlight_dataset() -> MapDataset:
    return MapDataset(
        signal_indices=np.array([10, 20, 30]),
        coords={"x": np.array([1.0, 2.0, 3.0]), "y": np.array([4.0, 5.0, 6.0])},
        omitted_counts={"x": 0, "y": 0},
        axis_labels={"x": "Vmax (V)", "y": "RMS (V)"},
    )


def test_resolve_map_highlight_coords_signal_present_returns_one_row():
    dataset = _highlight_dataset()
    result = resolve_map_highlight_coords(dataset, 20)
    assert result == {"x": [2.0], "y": [5.0]}


def test_resolve_map_highlight_coords_signal_absent_returns_empty():
    dataset = _highlight_dataset()
    result = resolve_map_highlight_coords(dataset, 999)
    assert result == {"x": [], "y": []}


def test_resolve_map_highlight_coords_no_selection_returns_empty():
    dataset = _highlight_dataset()
    result = resolve_map_highlight_coords(dataset, None)
    assert result == {"x": [], "y": []}


def test_resolve_map_highlight_coords_matches_dataset_axis_keys():
    # El mapa 3D tiene un eje más -- la función no distingue 2D de 3D, solo sigue las
    # claves que trae dataset.coords.
    dataset = MapDataset(
        signal_indices=np.array([5]),
        coords={"x": np.array([1.0]), "y": np.array([2.0]), "z": np.array([3.0])},
        omitted_counts={"x": 0, "y": 0, "z": 0},
        axis_labels={"x": "a", "y": "b", "z": "c"},
    )
    assert resolve_map_highlight_coords(dataset, 5) == {"x": [1.0], "y": [2.0], "z": [3.0]}
