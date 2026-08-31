import numpy as np
import pytest

from cache.backend import SqliteHdf5CacheBackend
from cache.service import get_or_compute_group_intrinsic, get_or_compute_group_reduction, get_or_compute_puntual
from core.models import SensorConfig, SignalBlock
from metrics.registry import discover_metrics


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


def _block(data, timestamps) -> SignalBlock:
    data = np.asarray(data, dtype=np.float32)
    n = data.shape[0]
    timestamps = np.asarray(timestamps, dtype=np.float64)
    trigger = np.full(n, 0.01)
    vrange = np.full(n, 1.0)
    valid_mask = np.full(n, True)
    minmax = np.stack([data.min(axis=1), data.max(axis=1)], axis=1).astype(np.float32)
    return SignalBlock(data=data, timestamps=timestamps, trigger=trigger, vrange=vrange,
                        valid_mask=valid_mask, minmax=minmax)


# --- Consistencia frío vs. caliente (PROMPT §11): resultados idénticos bit a bit ---

def test_puntual_cold_vs_warm_bit_identical(cache, sensor_config):
    block = _block([[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]], [0.0, 1.0, 2.0])
    ts_cold, v_cold = get_or_compute_puntual(cache, block, sensor_config, "ds1", "rms")
    ts_warm, v_warm = get_or_compute_puntual(cache, block, sensor_config, "ds1", "rms")
    assert np.array_equal(ts_cold, ts_warm)
    assert np.array_equal(v_cold, v_warm)  # bit a bit, no allclose


def test_puntual_second_call_uses_cache_not_recompute(cache, sensor_config):
    block = _block([[1, 2, 3, 4], [5, 6, 7, 8]], [0.0, 1.0])
    ts1, v1 = get_or_compute_puntual(cache, block, sensor_config, "ds1", "rms")

    # Mutar la traza subyacente DESPUÉS del primer cálculo: si la segunda llamada
    # recalculara en vez de usar el caché, el resultado cambiaría.
    block.data[:] = 999.0

    ts2, v2 = get_or_compute_puntual(cache, block, sensor_config, "ds1", "rms")
    assert np.array_equal(v1, v2)  # sigue siendo el valor viejo (cacheado), no 999


def test_group_reduction_cold_vs_warm_bit_identical(cache, sensor_config):
    block = _block([[1, 1, 1, 1], [2, 2, 2, 2], [3, 3, 3, 3]], [0.0, 1.0, 2.0])
    t1, v1, p1 = get_or_compute_group_reduction(cache, block, sensor_config, "ds1", "vmax", "by_count", 3, reducer="median")
    t2, v2, p2 = get_or_compute_group_reduction(cache, block, sensor_config, "ds1", "vmax", "by_count", 3, reducer="median")
    assert np.array_equal(t1, t2)
    assert np.array_equal(v1, v2)
    assert np.array_equal(p1, p2)


def test_group_intrinsic_cold_vs_warm_bit_identical(cache, sensor_config):
    block = _block([[1, 1, 1, 1]] * 5, [0.0, 1.0, 2.0, 3.0, 4.0])
    t1, v1, p1 = get_or_compute_group_intrinsic(cache, block, sensor_config, "ds1", "tasa_pulsos", "by_time", 60.0)
    t2, v2, p2 = get_or_compute_group_intrinsic(cache, block, sensor_config, "ds1", "tasa_pulsos", "by_time", 60.0)
    assert np.array_equal(t1, t2)
    assert np.array_equal(v1, v2)
    assert np.array_equal(p1, p2)


# --- Distintos parámetros / distinta agrupación -> claves distintas, sin colisión ---

def test_different_metric_params_do_not_collide_in_cache(cache, sensor_config):
    block = _block([[-1, 0, 1, 2]], [0.0])
    ts_p, v_p = get_or_compute_puntual(cache, block, sensor_config, "ds1", "kurtosis", params={"type": "pearson"})
    ts_e, v_e = get_or_compute_puntual(cache, block, sensor_config, "ds1", "kurtosis", params={"type": "excess"})
    assert not np.array_equal(v_p, v_e)
    assert np.isclose(v_p[0] - v_e[0], 3.0)


def test_different_grouping_value_recomputes_not_reuses_stale_cache(cache, sensor_config):
    block = _block([[1, 1, 1, 1]] * 10, list(range(10)))
    t60, v60, _ = get_or_compute_group_intrinsic(cache, block, sensor_config, "ds1", "tasa_pulsos", "by_time", 60.0)
    t5, v5, _ = get_or_compute_group_intrinsic(cache, block, sensor_config, "ds1", "tasa_pulsos", "by_time", 5.0)
    assert not np.array_equal(v60, v5)
    assert cache.stats()["total_entries"] == 2


def test_puntual_vs_group_same_metric_id_cached_separately(cache, sensor_config):
    block = _block([[1, 1, 1, 1], [2, 2, 2, 2], [3, 3, 3, 3]], [0.0, 1.0, 2.0])
    ts_p, v_p = get_or_compute_puntual(cache, block, sensor_config, "ds1", "vmax")
    t_g, v_g, _ = get_or_compute_group_reduction(cache, block, sensor_config, "ds1", "vmax", "by_count", 3, reducer="median")
    assert v_p.shape[0] == 3   # puntual: un valor por señal
    assert v_g.shape[0] == 1   # grupo: un valor por grupo (mediana)
    assert cache.stats()["total_entries"] == 2


# --- active_mask (Fase 6, filtrado cruzado, PROMPT §7): nunca invalida ni ensucia el caché ---

def test_get_or_compute_puntual_active_mask_reuses_unfiltered_cache_entry(cache, sensor_config):
    # A diferencia de las funciones de grupo, el régimen puntual NO bypasea el caché:
    # sigue consultando/persistiendo el resultado SIN filtrar (§8.2), y el filtro se
    # aplica después, en memoria -- una sola entrada de caché para ambas llamadas.
    block = _block([[1, 1, 1, 1], [2, 2, 2, 2], [3, 3, 3, 3]], [0.0, 1.0, 2.0])
    active_mask = np.array([True, False, True])
    get_or_compute_puntual(cache, block, sensor_config, "ds1", "rms", active_mask=active_mask)
    assert cache.stats()["total_entries"] == 1

    get_or_compute_puntual(cache, block, sensor_config, "ds1", "rms")  # sin filtro
    assert cache.stats()["total_entries"] == 1  # misma clave, ninguna entrada nueva


def test_get_or_compute_puntual_active_mask_filters_post_hoc_without_touching_cached_entry(cache, sensor_config):
    block = _block([[1, 1, 1, 1], [2, 2, 2, 2], [3, 3, 3, 3]], [0.0, 1.0, 2.0])
    ts_full, v_full = get_or_compute_puntual(cache, block, sensor_config, "ds1", "rms")
    assert v_full.shape[0] == 3

    active_mask = np.array([True, False, True])
    ts_filtered, v_filtered = get_or_compute_puntual(cache, block, sensor_config, "ds1", "rms", active_mask=active_mask)
    assert np.allclose(ts_filtered, [0.0, 2.0])
    assert np.allclose(v_filtered, [1.0, 3.0])

    # La entrada cacheada (sin filtrar) sigue intacta -- el filtro nunca la reescribió.
    ts_again, v_again = get_or_compute_puntual(cache, block, sensor_config, "ds1", "rms")
    assert np.array_equal(v_again, v_full)
    assert cache.stats()["total_entries"] == 1


def test_get_or_compute_group_reduction_active_mask_bypasses_cache_both_ways(cache, sensor_config):
    block = _block([[1, 1, 1, 1], [2, 2, 2, 2], [3, 3, 3, 3]], [0.0, 1.0, 2.0])
    active_mask = np.array([True, True, False])

    # Con filtro activo, ni lee ni escribe -- sin entrada previa ni posterior.
    get_or_compute_group_reduction(
        cache, block, sensor_config, "ds1", "vmax", "by_count", 3, reducer="median", active_mask=active_mask
    )
    assert cache.stats()["total_entries"] == 0

    # Primero se cachea sin filtro...
    t_full, v_full, _ = get_or_compute_group_reduction(cache, block, sensor_config, "ds1", "vmax", "by_count", 3, reducer="median")
    assert cache.stats()["total_entries"] == 1

    # ...y una llamada posterior CON filtro no lee esa entrada cacheada (recalcula distinto).
    t_filtered, v_filtered, _ = get_or_compute_group_reduction(
        cache, block, sensor_config, "ds1", "vmax", "by_count", 3, reducer="median", active_mask=active_mask
    )
    assert not np.array_equal(v_filtered, v_full)
    assert cache.stats()["total_entries"] == 1  # sigue sin escribir la variante filtrada


def test_get_or_compute_group_intrinsic_active_mask_bypasses_cache_both_ways(cache, sensor_config):
    block = _block([[1, 1, 1, 1]] * 5, [0.0, 1.0, 2.0, 3.0, 4.0])
    active_mask = np.array([True, True, False, True, True])

    get_or_compute_group_intrinsic(
        cache, block, sensor_config, "ds1", "tasa_pulsos", "by_time", 60.0, active_mask=active_mask
    )
    assert cache.stats()["total_entries"] == 0

    t_full, v_full, _ = get_or_compute_group_intrinsic(cache, block, sensor_config, "ds1", "tasa_pulsos", "by_time", 60.0)
    assert np.isclose(v_full[0], 5 / 60.0)
    assert cache.stats()["total_entries"] == 1

    t_filtered, v_filtered, _ = get_or_compute_group_intrinsic(
        cache, block, sensor_config, "ds1", "tasa_pulsos", "by_time", 60.0, active_mask=active_mask
    )
    assert np.isclose(v_filtered[0], 4 / 60.0)
    assert cache.stats()["total_entries"] == 1


def test_group_reduction_all_true_mask_uses_cache_not_bypass(cache, sensor_config):
    block = _block([[1, 1, 1, 1], [2, 2, 2, 2], [3, 3, 3, 3]], [0.0, 1.0, 2.0])
    all_true_mask = np.array([True, True, True])

    # Primera llamada: calcula y persiste en caché (miss -> put)
    t1, v1, p1 = get_or_compute_group_reduction(
        cache, block, sensor_config, "ds_all_true_red", "vmax", "by_count", 3, reducer="median", active_mask=all_true_mask
    )
    assert cache.stats()["total_entries"] == 1

    # Segunda llamada: acierto de caché (hit)
    t2, v2, p2 = get_or_compute_group_reduction(
        cache, block, sensor_config, "ds_all_true_red", "vmax", "by_count", 3, reducer="median", active_mask=all_true_mask
    )
    assert cache.stats()["total_entries"] == 1
    np.testing.assert_array_equal(t1, t2)
    np.testing.assert_array_equal(v1, v2)
    np.testing.assert_array_equal(p1, p2)

    # Coincide exactamente con la llamada sin máscara (active_mask=None)
    t_none, v_none, p_none = get_or_compute_group_reduction(
        cache, block, sensor_config, "ds_all_true_red", "vmax", "by_count", 3, reducer="median", active_mask=None
    )
    assert cache.stats()["total_entries"] == 1
    np.testing.assert_array_equal(t1, t_none)
    np.testing.assert_array_equal(v1, v_none)
    np.testing.assert_array_equal(p1, p_none)


def test_group_intrinsic_all_true_mask_uses_cache_not_bypass(cache, sensor_config):
    block = _block([[1, 1, 1, 1]] * 5, [0.0, 1.0, 2.0, 3.0, 4.0])
    all_true_mask = np.array([True, True, True, True, True])

    t1, v1, p1 = get_or_compute_group_intrinsic(
        cache, block, sensor_config, "ds_all_true_int", "tasa_pulsos", "by_time", 60.0, active_mask=all_true_mask
    )
    assert cache.stats()["total_entries"] == 1

    t2, v2, p2 = get_or_compute_group_intrinsic(
        cache, block, sensor_config, "ds_all_true_int", "tasa_pulsos", "by_time", 60.0, active_mask=all_true_mask
    )
    assert cache.stats()["total_entries"] == 1
    np.testing.assert_array_equal(t1, t2)
    np.testing.assert_array_equal(v1, v2)

    t_none, v_none, p_none = get_or_compute_group_intrinsic(
        cache, block, sensor_config, "ds_all_true_int", "tasa_pulsos", "by_time", 60.0, active_mask=None
    )
    assert cache.stats()["total_entries"] == 1
    np.testing.assert_array_equal(t1, t_none)
    np.testing.assert_array_equal(v1, v_none)


def test_all_true_mask_result_is_identical_to_no_mask(cache, sensor_config):
    block = _block([[1, 2, 3, 4], [2, 3, 4, 5], [3, 4, 5, 6]], [0.0, 1.0, 2.0])
    mask = np.array([True, True, True])

    # 1. Puntual
    tp_mask, vp_mask = get_or_compute_puntual(cache, block, sensor_config, "ds_eq", "rms", active_mask=mask)
    tp_none, vp_none = get_or_compute_puntual(cache, block, sensor_config, "ds_eq", "rms", active_mask=None)
    np.testing.assert_array_equal(tp_mask, tp_none)
    np.testing.assert_array_equal(vp_mask, vp_none)

    # 2. Grupo reducción
    tr_mask, vr_mask, pr_mask = get_or_compute_group_reduction(
        cache, block, sensor_config, "ds_eq", "vmax", "by_count", 2, active_mask=mask
    )
    tr_none, vr_none, pr_none = get_or_compute_group_reduction(
        cache, block, sensor_config, "ds_eq", "vmax", "by_count", 2, active_mask=None
    )
    np.testing.assert_array_equal(tr_mask, tr_none)
    np.testing.assert_array_equal(vr_mask, vr_none)
    np.testing.assert_array_equal(pr_mask, pr_none)

    # 3. Grupo intrínseca
    ti_mask, vi_mask, pi_mask = get_or_compute_group_intrinsic(
        cache, block, sensor_config, "ds_eq", "tasa_pulsos", "by_time", 60.0, active_mask=mask
    )
    ti_none, vi_none, pi_none = get_or_compute_group_intrinsic(
        cache, block, sensor_config, "ds_eq", "tasa_pulsos", "by_time", 60.0, active_mask=None
    )
    np.testing.assert_array_equal(ti_mask, ti_none)
    np.testing.assert_array_equal(vi_mask, vi_none)
    np.testing.assert_array_equal(pi_mask, pi_none)


def test_empty_mask_is_treated_as_no_filter(cache, sensor_config):
    block = _block([[1, 1, 1, 1]], [0.0])
    empty_mask = np.array([], dtype=bool)

    # No debe levantar IndexError y debe normalizar a None
    t, v = get_or_compute_puntual(cache, block, sensor_config, "ds_empty", "rms", active_mask=empty_mask)
    assert v.shape[0] == 1

    t_g, v_g, _ = get_or_compute_group_reduction(
        cache, block, sensor_config, "ds_empty", "vmax", "by_count", 1, active_mask=empty_mask
    )
    assert v_g.shape[0] == 1


def test_partially_filtering_mask_still_bypasses_cache(cache, sensor_config):
    block = _block([[1, 1, 1, 1], [2, 2, 2, 2], [3, 3, 3, 3]], [0.0, 1.0, 2.0])
    partial_mask = np.array([True, False, True])

    # Llamada con filtro parcial: debe bypasear y no escribir en caché
    t, v, _ = get_or_compute_group_reduction(
        cache, block, sensor_config, "ds_partial", "vmax", "by_count", 3, reducer="median", active_mask=partial_mask
    )
    assert cache.stats()["total_entries"] == 0
