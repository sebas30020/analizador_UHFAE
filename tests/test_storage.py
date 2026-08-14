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
