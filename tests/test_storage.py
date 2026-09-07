import numpy as np

from core.models import load_sensor_configs
from data.ingest import ingest_experiment
from data.readers.hdf5_reader import HDF5Reader
from data.storage import CanonicalStore, write_canonical
from tests.conftest import EXPERIMENT_NAME


def _ingest_synthetic(synthetic_hdf5):
    with HDF5Reader(synthetic_hdf5) as r:
        return ingest_experiment(r, EXPERIMENT_NAME, ["UHF", "AE"])


def test_write_and_read_roundtrip(synthetic_hdf5, tmp_path):
    result = _ingest_synthetic(synthetic_hdf5)
    configs = load_sensor_configs("config/sensors.yaml")
    # Los tamaños del fixture (M=16/32) no coinciden con config/sensors.yaml (3000/10000);
    # el store no depende de esos valores para leer/escribir arrays reales, solo los usa
    # para anotar atributos (fs, freq_limit) -- se pasa igual para probar esa ruta.
    out_path = tmp_path / "canonical.h5"
    write_canonical(result, configs, out_path)

    with CanonicalStore(out_path) as s:
        assert s.dataset_id == result.dataset_id
        assert s.experiment == EXPERIMENT_NAME
        assert s.normalization_version == "v1_divide_by_vrange"
        assert set(s.available_sensors()) == {"UHF", "AE"}

        for sensor in ("UHF", "AE"):
            block = result.sensors[sensor]
            assert s.n_signals(sensor) == block.data.shape[0]

            row, ts, trig, vr = s.get_signal_row(sensor, 0)
            assert np.allclose(row, block.data[0])
            assert ts == block.timestamps[0]
            # np.isclose con equal_nan=True: la fila 0 de AE tiene trigger=NaN a propósito
            # (metadato faltante, ver conftest.py) y debe preservarse como NaN, no como error.
            assert np.isclose(trig, block.trigger[0], equal_nan=True)
            assert vr == block.vrange[0]

            full_block = s.get_block(sensor, 0, block.data.shape[0])
            assert np.allclose(full_block.data, block.data)
            assert np.array_equal(full_block.valid_mask, block.valid_mask)
            assert np.allclose(full_block.minmax, block.minmax)

        env = s.get_environmental()
        assert env.timestamps.shape == result.environmental.timestamps.shape

        events = s.get_events()
        assert events.timestamps.shape == result.events.timestamps.shape
        assert set(events.event_type.tolist()) == set(result.events.event_type.tolist())
        assert all(isinstance(t, str) for t in events.event_type)


def test_iter_blocks_covers_all_rows_without_overlap(synthetic_hdf5, tmp_path):
    result = _ingest_synthetic(synthetic_hdf5)
    configs = load_sensor_configs("config/sensors.yaml")
    out_path = tmp_path / "canonical.h5"
    write_canonical(result, configs, out_path)

    with CanonicalStore(out_path) as s:
        seen_timestamps = []
        for block in s.iter_blocks("UHF", block_n_signals=1):
            seen_timestamps.extend(block.timestamps.tolist())

        assert seen_timestamps == result.sensors["UHF"].timestamps.tolist()


def test_create_lazy_block_and_row_access(synthetic_hdf5, tmp_path):
    result = _ingest_synthetic(synthetic_hdf5)
    configs = load_sensor_configs("config/sensors.yaml")
    out_path = tmp_path / "canonical.h5"
    write_canonical(result, configs, out_path)

    with CanonicalStore(out_path) as s:
        lazy_uhf = s.create_lazy_block("UHF")
        assert lazy_uhf.n_signals == 4
        assert lazy_uhf.n_samples == result.sensors["UHF"].n_samples

        # Slicing via rows
        rows_0_2 = lazy_uhf.rows(0, 2)
        assert rows_0_2.shape == (2, lazy_uhf.n_samples)
        assert np.allclose(rows_0_2, result.sensors["UHF"].data[:2])

        # Single row
        row_3 = lazy_uhf.row(3)
        assert np.allclose(row_3, result.sensors["UHF"].data[3])

        # Arbitrary indices
        rows_sel = lazy_uhf.rows_by_indices(np.array([3, 0, 2]))
        assert np.allclose(rows_sel, result.sensors["UHF"].data[[3, 0, 2]])


def test_canonical_store_multithreaded_concurrent_reads(synthetic_hdf5, tmp_path):
    import concurrent.futures
    result = _ingest_synthetic(synthetic_hdf5)
    configs = load_sensor_configs("config/sensors.yaml")
    out_path = tmp_path / "canonical.h5"
    write_canonical(result, configs, out_path)

    with CanonicalStore(out_path) as s:
        def read_worker(idx: int) -> float:
            row, ts, trig, vr = s.get_signal_row("UHF", idx % 4)
            data_rows = s.get_data_rows("UHF", 0, 3)
            return float(row.sum() + data_rows.sum())

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(read_worker, i) for i in range(50)]
            results = [f.result() for f in futures]
            assert len(results) == 50
            assert all(r is not None for r in results)


def test_canonical_store_non_increasing_indices_and_order(tmp_path):
    import h5py
    test_file = tmp_path / "test_order.h5"
    with h5py.File(test_file, "w") as f:
        grp = f.create_group("UHF")
        grp.attrs["is_chronological"] = False
        raw_mat = np.array([[10, 11], [20, 21], [30, 31], [40, 41]], dtype=np.float32)
        # Permutation: chronological 0 -> physical 3, 1 -> physical 1, 2 -> physical 0, 3 -> physical 2
        order = np.array([3, 1, 0, 2], dtype=np.int64)
        grp.create_dataset("data", data=raw_mat)
        grp.create_dataset("order", data=order)
        grp.create_dataset("timestamps", data=np.array([1.0, 2.0, 3.0, 4.0]))
        grp.create_dataset("trigger", data=np.zeros(4))
        grp.create_dataset("vrange", data=np.ones(4))
        grp.create_dataset("valid_mask", data=np.ones(4, dtype=np.uint8))
        grp.create_dataset("minmax", data=np.zeros((4, 2), dtype=np.float32))

    with CanonicalStore(test_file) as s:
        # Chronological index 0 is physical row 3 ([40, 41])
        row0, _, _, _ = s.get_signal_row("UHF", 0)
        assert np.allclose(row0, [40, 41])

        # Chronological range [0, 4) -> physical [3, 1, 0, 2]
        # In h5py, [3, 1, 0, 2] is non-increasing! get_data_rows must handle it properly
        rows = s.get_data_rows("UHF", 0, 4)
        assert np.allclose(rows, [[40, 41], [20, 21], [10, 11], [30, 31]])

        # Test non-increasing indices explicitly in get_data_indices
        indices = np.array([3, 0, 2, 1], dtype=np.int64)
        subset = s.get_data_indices("UHF", indices)
        expected = np.array([[30, 31], [40, 41], [10, 11], [20, 21]])
        assert np.allclose(subset, expected)

