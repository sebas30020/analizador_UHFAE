import numpy as np
import pytest

from core.grouping import resolve_groups
from core.models import SensorConfig, SignalBlock
from core.normalization import compute_valid_mask
from metrics.engine import compute_group_intrinsic, compute_group_reduction, compute_puntual
from metrics.registry import discover_metrics


@pytest.fixture(autouse=True, scope="module")
def _ensure_registered():
    discover_metrics()


@pytest.fixture
def sensor_config() -> SensorConfig:
    return SensorConfig(
        name="UHF",
        hdf5_group="signals",
        fs_hz=1e9,
        n_samples=4,
        freq_limit_hz=1e8,
        axis_unit="us",
        axis_scale=1e6,
        target_block_bytes=1024,
    )


def _block(data, timestamps, trigger=None, vrange=None, valid_mask=None) -> SignalBlock:
    data = np.asarray(data, dtype=np.float32)
    n = data.shape[0]
    timestamps = np.asarray(timestamps, dtype=np.float64)
    trigger = np.asarray(trigger if trigger is not None else [0.01] * n, dtype=np.float64)
    vrange = np.asarray(vrange if vrange is not None else [1.0] * n, dtype=np.float64)
    # Igual que en producción (data/ingest.py): valid_mask se deriva de trigger/vrange,
    # no se asume True por defecto -- si el test pasa vrange=0 para simular metadatos
    # faltantes, debe reflejarse en valid_mask sin que el caller lo repita a mano.
    valid_mask = (
        np.asarray(valid_mask, dtype=bool) if valid_mask is not None else compute_valid_mask(trigger, vrange)
    )
    minmax = np.stack([data.min(axis=1), data.max(axis=1)], axis=1).astype(np.float32)
    return SignalBlock(data=data, timestamps=timestamps, trigger=trigger, vrange=vrange,
                        valid_mask=valid_mask, minmax=minmax)


def test_compute_puntual_excludes_invalid_signals(sensor_config):
    block = _block(
        data=[[1, 1, 1, 1], [2, 2, 2, 2], [3, 3, 3, 3]],
        timestamps=[0.0, 1.0, 2.0],
        vrange=[1.0, 0.0, 1.0],  # la señal 1 (vrange=0) es inválida
    )
    ts, vals = compute_puntual(block, sensor_config, "rms")
    assert ts.shape[0] == 2
    assert np.allclose(ts, [0.0, 2.0])
    assert np.allclose(vals, [1.0, 3.0])


def test_compute_puntual_normalizes_by_own_vrange(sensor_config):
    block = _block(
        data=[[2, 2, 2, 2], [10, 10, 10, 10]],
        timestamps=[0.0, 1.0],
        vrange=[2.0, 5.0],  # normalizado: [1,1,1,1] y [2,2,2,2]
    )
    ts, vals = compute_puntual(block, sensor_config, "vmax")
    assert np.allclose(vals, [1.0, 2.0])


def test_compute_puntual_parallel_matches_serial(sensor_config):
    rng = np.random.default_rng(42)
    n = 37
    data = rng.normal(size=(n, 4)).astype(np.float32)
    timestamps = np.sort(rng.uniform(0, 100, size=n))
    block = _block(data=data, timestamps=timestamps, vrange=[1.0] * n)

    ts1, v1 = compute_puntual(block, sensor_config, "rms", n_workers=1)
    ts4, v4 = compute_puntual(block, sensor_config, "rms", n_workers=4)

    assert np.allclose(ts1, ts4)
    assert np.allclose(v1, v4)


def test_compute_puntual_empty_when_no_valid_signals(sensor_config):
    block = _block(data=[[1, 1, 1, 1]], timestamps=[0.0], vrange=[0.0])
    ts, vals = compute_puntual(block, sensor_config, "rms")
    assert ts.shape[0] == 0
    assert vals.shape[0] == 0


def test_group_reduction_delta_t_respects_cross_group_boundary(sensor_config):
    # Regresión del bug encontrado al escribir esta prueba: reducir delta_t "en frío"
    # dentro de cada grupo resetea Δt=0 al inicio de cada ventana, perdiendo el pulso
    # real inmediatamente anterior (que puede estar en el grupo previo).
    timestamps = [0.0, 0.01, 0.03, 0.06]  # Δt globales: [0(conv), 0.01, 0.02, 0.03]
    block = _block(data=[[1, 1, 1, 1]] * 4, timestamps=timestamps)
    groups = resolve_groups(block.timestamps, mode="by_count", value=2)  # [0,1] y [2,3]
    assert len(groups) == 2

    t, v, partial = compute_group_reduction(block, sensor_config, "delta_t", groups, reducer="median")
    assert len(v) == 2
    # grupo0: Δt=[0, 0.01] -> mediana 0.005 (coincide con o sin el bug, es el primer grupo)
    assert np.isclose(v[0], 0.005)
    # grupo1: Δt correctos=[0.02, 0.03] -> mediana 0.025. Con el bug (recompute local
    # tratando el índice 2 como "primer pulso"): [0, 0.03] -> mediana 0.015 (INCORRECTO).
    assert np.isclose(v[1], 0.025)


def test_group_reduction_generic_median_matches_pdf_style_example(sensor_config):
    # Mismo espíritu que el ejemplo del PDF para K_w (Ec. 28: "la mediana evita que el
    # único valor 20.0 domine el diagnóstico") -- aquí con RMS, fácil de controlar con
    # un outlier, para probar la propiedad de robustez de la mediana del reductor genérico.
    block = _block(
        data=[[1, 1, 1, 1], [2, 2, 2, 2], [3, 3, 3, 3], [100, 100, 100, 100]],
        timestamps=[0.0, 1.0, 2.0, 3.0],
    )
    groups = resolve_groups(block.timestamps, mode="by_count", value=4)  # 1 solo grupo
    t, v, partial = compute_group_reduction(block, sensor_config, "rms", groups, reducer="median")
    assert len(v) == 1
    assert np.isclose(v[0], 2.5)  # mediana de [1,2,3,100], no la media (~26.5)


def test_group_reduction_omits_groups_without_valid_signals(sensor_config):
    block = _block(
        data=[[1, 1, 1, 1], [2, 2, 2, 2]],
        timestamps=[0.0, 100.0],
        vrange=[1.0, 1.0],
    )
    # Ventana de 1s ancladas en t=0: grupo0=[0,1) contiene la señal 0; hay un hueco
    # grande antes de la señal en t=100 -> solo 2 grupos no vacíos.
    groups = resolve_groups(block.timestamps, mode="by_time", value=1.0)
    t, v, partial = compute_group_reduction(block, sensor_config, "vmax", groups, reducer="median")
    assert len(v) == 2  # ninguna ventana vacía intermedia genera punto


def test_group_intrinsic_tasa_pulsos_uses_declared_t_w(sensor_config):
    block = _block(
        data=[[1, 1, 1, 1]] * 5,
        timestamps=[0.0, 1.0, 2.0, 3.0, 4.0],
    )
    groups = resolve_groups(block.timestamps, mode="by_time", value=60.0)  # 1 ventana de 60s
    t, v, partial = compute_group_intrinsic(block, sensor_config, "tasa_pulsos", groups)
    assert len(v) == 1
    assert np.isclose(v[0], 5 / 60.0)  # NO 5 / (span observado de 4s)


def test_group_intrinsic_skips_degenerate_zero_duration_groups(sensor_config):
    # by_count con k=1 produce grupos de una sola señal -> T_w=0 -> se omiten.
    block = _block(data=[[1, 1, 1, 1], [2, 2, 2, 2]], timestamps=[0.0, 1.0])
    groups = resolve_groups(block.timestamps, mode="by_count", value=1)
    t, v, partial = compute_group_intrinsic(block, sensor_config, "tasa_pulsos", groups)
    assert len(v) == 0


def test_group_reduction_rejects_grupo_regimen_metric(sensor_config):
    block = _block(data=[[1, 1, 1, 1]], timestamps=[0.0])
    groups = resolve_groups(block.timestamps, mode="by_count", value=1)
    with pytest.raises(ValueError):
        compute_group_reduction(block, sensor_config, "tasa_pulsos", groups)


def test_group_intrinsic_rejects_puntual_regimen_metric(sensor_config):
    block = _block(data=[[1, 1, 1, 1]], timestamps=[0.0])
    groups = resolve_groups(block.timestamps, mode="by_count", value=1)
    with pytest.raises(ValueError):
        compute_group_intrinsic(block, sensor_config, "rms", groups)


# --- extra_mask (Fase 6, filtrado cruzado, PROMPT §7) -----------------------------


def test_compute_puntual_extra_mask_none_matches_no_mask(sensor_config):
    block = _block(data=[[1, 1, 1, 1], [2, 2, 2, 2], [3, 3, 3, 3]], timestamps=[0.0, 1.0, 2.0])
    ts1, v1 = compute_puntual(block, sensor_config, "rms")
    ts2, v2 = compute_puntual(block, sensor_config, "rms", extra_mask=None)
    assert np.array_equal(ts1, ts2)
    assert np.array_equal(v1, v2)


def test_compute_puntual_extra_mask_excludes_signal(sensor_config):
    block = _block(data=[[1, 1, 1, 1], [2, 2, 2, 2], [3, 3, 3, 3]], timestamps=[0.0, 1.0, 2.0])
    extra_mask = np.array([True, False, True])
    ts, v = compute_puntual(block, sensor_config, "rms", extra_mask=extra_mask)
    assert np.allclose(ts, [0.0, 2.0])
    assert np.allclose(v, [1.0, 3.0])


def test_compute_puntual_delta_t_extra_mask_reattributes_across_excluded_signal(sensor_config):
    block = _block(data=[[1, 1, 1, 1]] * 5, timestamps=[0.0, 1.0, 2.0, 3.0, 4.0])
    extra_mask = np.array([True, True, False, True, True])  # excluye índice 2 (t=2.0)
    ts, v = compute_puntual(block, sensor_config, "delta_t", extra_mask=extra_mask)
    assert np.allclose(ts, [0.0, 1.0, 3.0, 4.0])
    # Δt de t=3 es contra t=1 (2.0), no contra el t=2 excluido (que daría 1.0)
    assert np.allclose(v, [0.0, 1.0, 2.0, 1.0])


def test_compute_group_reduction_delta_t_extra_mask_reattributes_across_group_boundary(sensor_config):
    # Regresión del riesgo de desincronización identificado en el diseño: si
    # global_valid_idx no se construye con la MISMA máscara que global_values, esta
    # línea revienta con IndexError (5 vs 4 elementos) o desalinea los valores.
    block = _block(data=[[1, 1, 1, 1]] * 5, timestamps=[0.0, 1.0, 2.0, 3.0, 4.0])
    extra_mask = np.array([True, True, False, True, True])
    groups = resolve_groups(block.timestamps, mode="by_count", value=5)  # 1 solo grupo
    t, v, partial = compute_group_reduction(block, sensor_config, "delta_t", groups, reducer="median", extra_mask=extra_mask)
    assert len(v) == 1
    # Δt efectivos = [0, 1, 2, 1] (t=1:1-0; t=3:3-1=2, salta el excluido; t=4:4-3=1)
    assert np.isclose(v[0], 1.0)


def test_compute_group_reduction_extra_mask_group_with_no_active_signals_is_skipped(sensor_config):
    block = _block(data=[[1, 1, 1, 1], [2, 2, 2, 2]], timestamps=[0.0, 1.0])
    extra_mask = np.array([False, False])  # excluye ambas señales del único grupo
    groups = resolve_groups(block.timestamps, mode="by_count", value=2)
    t, v, partial = compute_group_reduction(block, sensor_config, "rms", groups, extra_mask=extra_mask)
    assert len(v) == 0


def test_compute_group_intrinsic_extra_mask_excludes_signal_from_rate(sensor_config):
    block = _block(data=[[1, 1, 1, 1]] * 5, timestamps=[0.0, 1.0, 2.0, 3.0, 4.0])
    extra_mask = np.array([True, True, False, True, True])  # 4 señales activas de 5
    groups = resolve_groups(block.timestamps, mode="by_time", value=60.0)  # 1 ventana de 60s
    t, v, partial = compute_group_intrinsic(block, sensor_config, "tasa_pulsos", groups, extra_mask=extra_mask)
    assert len(v) == 1
    assert np.isclose(v[0], 4 / 60.0)  # 4 señales activas, no 5
