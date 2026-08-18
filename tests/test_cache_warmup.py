import time

import numpy as np
import pytest

from cache.backend import SqliteHdf5CacheBackend
from cache.warmup import WarmupSpec, default_warmup_specs, start_background_warmup, warm_cache
from core.models import SensorConfig, SignalBlock
from metrics.registry import discover_metrics


@pytest.fixture(autouse=True, scope="module")
def _ensure_registered():
    discover_metrics()


@pytest.fixture
def sensor_config() -> SensorConfig:
    return SensorConfig(
        name="UHF", hdf5_group="signals", fs_hz=1e9, n_samples=4, freq_limit_hz=1e8,
        axis_unit="us", axis_scale=1e6, target_block_bytes=1024,
    )


@pytest.fixture
def block() -> SignalBlock:
    rng = np.random.default_rng(0)
    n = 50
    data = rng.normal(size=(n, 4)).astype(np.float32)
    timestamps = np.sort(rng.uniform(0, 300, size=n))
    trigger = np.full(n, 0.01)
    vrange = np.full(n, 1.0)
    valid_mask = np.full(n, True)
    minmax = np.stack([data.min(axis=1), data.max(axis=1)], axis=1).astype(np.float32)
    return SignalBlock(data=data, timestamps=timestamps, trigger=trigger, vrange=vrange,
                        valid_mask=valid_mask, minmax=minmax)


def test_default_warmup_specs_covers_puntual_and_grupo():
    specs = default_warmup_specs("UHF")
    regimenes = {s.regimen for s in specs}
    assert "puntual" in regimenes
    assert "grupo_intrinseca" in regimenes
    assert all(s.sensor == "UHF" for s in specs)


def test_warm_cache_populates_all_specs(tmp_path, sensor_config, block):
    cache = SqliteHdf5CacheBackend(tmp_path / "cache")
    specs = default_warmup_specs("UHF", grouping_mode="by_time", grouping_value=60.0)

    done = warm_cache(cache, {"UHF": block}, {"UHF": sensor_config}, "ds1", specs)

    assert done == [s.metric_id for s in specs]
    assert cache.stats()["total_entries"] == len(specs)
    cache.close()


def test_warm_cache_skips_specs_for_missing_sensor(tmp_path, sensor_config, block):
    cache = SqliteHdf5CacheBackend(tmp_path / "cache")
    specs = [WarmupSpec(sensor="AE", metric_id="rms", regimen="puntual")]  # AE no está en blocks

    done = warm_cache(cache, {"UHF": block}, {"UHF": sensor_config}, "ds1", specs)

    assert done == []
    cache.close()


def test_warm_cache_is_idempotent(tmp_path, sensor_config, block):
    cache = SqliteHdf5CacheBackend(tmp_path / "cache")
    specs = default_warmup_specs("UHF")

    warm_cache(cache, {"UHF": block}, {"UHF": sensor_config}, "ds1", specs)
    n_after_first = cache.stats()["total_entries"]
    warm_cache(cache, {"UHF": block}, {"UHF": sensor_config}, "ds1", specs)
    n_after_second = cache.stats()["total_entries"]

    assert n_after_first == n_after_second  # sin duplicados
    cache.close()


def test_warm_cache_continues_after_one_spec_fails(tmp_path, sensor_config, block, monkeypatch):
    # Regresión: antes, el try/except envolvía TODO el bucle en start_background_warmup
    # (no warm_cache), así que una métrica que fallara (p. ej. MemoryError en un dataset
    # grande) abortaba silenciosamente todas las specs restantes, incluidas las del otro
    # sensor. warm_cache debe registrar el fallo y seguir con la siguiente spec.
    import cache.warmup as warmup_module

    real = warmup_module.get_or_compute_puntual

    def _flaky(cache, block, cfg, dataset_id, metric_id, **kwargs):
        if metric_id == "rms":
            raise MemoryError("simulado")
        return real(cache, block, cfg, dataset_id, metric_id, **kwargs)

    monkeypatch.setattr(warmup_module, "get_or_compute_puntual", _flaky)

    cache = SqliteHdf5CacheBackend(tmp_path / "cache")
    specs = [
        WarmupSpec(sensor="UHF", metric_id="rms", regimen="puntual"),
        WarmupSpec(sensor="UHF", metric_id="vmax", regimen="puntual"),
    ]

    done = warm_cache(cache, {"UHF": block}, {"UHF": sensor_config}, "ds1", specs)

    assert done == ["vmax"]  # rms falló y se omitió, vmax se calculó igual
    cache.close()


def test_start_background_warmup_does_not_block_caller(tmp_path, sensor_config, block):
    completed: list[list[str]] = []

    t0 = time.time()
    thread = start_background_warmup(
        tmp_path / "cache",
        {"UHF": block},
        {"UHF": sensor_config},
        "ds1",
        default_warmup_specs("UHF"),
        on_complete=completed.append,
    )
    elapsed_to_return = time.time() - t0

    assert elapsed_to_return < 0.5  # la llamada retorna casi de inmediato, no espera al cálculo
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert len(completed) == 1
    assert len(completed[0]) == len(default_warmup_specs("UHF"))


def test_background_warmup_result_is_readable_from_a_fresh_backend(tmp_path, sensor_config, block):
    cache_dir = tmp_path / "cache"
    thread = start_background_warmup(
        cache_dir, {"UHF": block}, {"UHF": sensor_config}, "ds1", default_warmup_specs("UHF"),
    )
    thread.join(timeout=30)

    reader = SqliteHdf5CacheBackend(cache_dir)
    assert reader.stats()["total_entries"] == len(default_warmup_specs("UHF"))
    reader.close()
