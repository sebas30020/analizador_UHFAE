import numpy as np
import pytest

from cache.backend import SqliteHdf5CacheBackend


@pytest.fixture
def cache(tmp_path):
    backend = SqliteHdf5CacheBackend(tmp_path / "cache")
    yield backend
    backend.close()


def test_get_on_missing_key_returns_none(cache):
    assert cache.get("no-existe") is None


def test_put_then_get_roundtrip(cache):
    ts = np.array([1.0, 2.0, 3.0])
    vals = np.array([10.0, 20.0, 30.0])
    cache.put("k1", '{"a":1}', "ds1", "UHF", "rms", 1, ts, vals)

    entry = cache.get("k1")
    assert entry is not None
    assert np.array_equal(entry.timestamps, ts)
    assert np.array_equal(entry.values, vals)
    assert entry.is_partial is None
    assert entry.dataset_id == "ds1"
    assert entry.sensor == "UHF"
    assert entry.metric_id == "rms"
    assert entry.metric_version == 1


def test_put_preserves_is_partial_flags(cache):
    ts = np.array([1.0, 2.0])
    vals = np.array([10.0, 20.0])
    is_partial = np.array([False, True])
    cache.put("k1", "{}", "ds1", "UHF", "tasa_pulsos", 1, ts, vals, is_partial=is_partial)

    entry = cache.get("k1")
    assert entry.is_partial is not None
    assert entry.is_partial.tolist() == [False, True]


def test_put_same_key_twice_overwrites_not_crashes(cache):
    cache.put("k1", "{}", "ds1", "UHF", "rms", 1, np.array([1.0]), np.array([100.0]))
    cache.put("k1", "{}", "ds1", "UHF", "rms", 1, np.array([2.0]), np.array([200.0]))

    entry = cache.get("k1")
    assert entry.timestamps.tolist() == [2.0]
    assert entry.values.tolist() == [200.0]
    assert cache.stats()["total_entries"] == 1  # no duplicó la fila del índice


def test_purge_orphaned_removes_unknown_dataset(cache):
    cache.put("k_old", "{}", "ds_deleted", "UHF", "rms", 1, np.array([1.0]), np.array([1.0]))
    cache.put("k_current", "{}", "ds_current", "UHF", "rms", 1, np.array([1.0]), np.array([1.0]))

    purged = cache.purge_orphaned(valid_dataset_ids={"ds_current"}, current_metric_versions={"rms": 1})

    assert purged == 1
    assert cache.get("k_old") is None
    assert cache.get("k_current") is not None


def test_purge_orphaned_removes_stale_metric_version(cache):
    # Simula que 'rms' subió de versión 1 -> 2 (cambio de fórmula) -- la entrada vieja
    # calculada con la v1 debe purgarse aunque el dataset siga existiendo.
    cache.put("k1", "{}", "ds1", "UHF", "rms", 1, np.array([1.0]), np.array([1.0]))

    purged = cache.purge_orphaned(valid_dataset_ids={"ds1"}, current_metric_versions={"rms": 2})

    assert purged == 1
    assert cache.get("k1") is None


def test_purge_orphaned_keeps_valid_entries(cache):
    cache.put("k1", "{}", "ds1", "UHF", "rms", 1, np.array([1.0]), np.array([1.0]))
    purged = cache.purge_orphaned(valid_dataset_ids={"ds1"}, current_metric_versions={"rms": 1})
    assert purged == 0
    assert cache.get("k1") is not None


def test_purge_orphaned_on_empty_cache_is_noop(cache):
    assert cache.purge_orphaned(valid_dataset_ids=set(), current_metric_versions={}) == 0


def test_stats_reflects_entry_count(cache):
    assert cache.stats()["total_entries"] == 0
    cache.put("k1", "{}", "ds1", "UHF", "rms", 1, np.array([1.0]), np.array([1.0]))
    cache.put("k2", "{}", "ds1", "UHF", "vpp", 1, np.array([1.0]), np.array([1.0]))
    assert cache.stats()["total_entries"] == 2


def test_reopening_backend_on_same_dir_sees_previous_entries(tmp_path):
    cache_dir = tmp_path / "cache"
    b1 = SqliteHdf5CacheBackend(cache_dir)
    b1.put("k1", "{}", "ds1", "UHF", "rms", 1, np.array([1.0]), np.array([42.0]))
    b1.close()

    b2 = SqliteHdf5CacheBackend(cache_dir)
    entry = b2.get("k1")
    assert entry is not None
    assert entry.values.tolist() == [42.0]
    b2.close()
