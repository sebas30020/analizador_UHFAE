import h5py
import numpy as np
import pytest

from core.models import load_sensor_configs
from data.export import export_filtered
from data.ingest import ingest_experiment
from data.readers.factory import open_reader
from data.readers.filtered_export_reader import FilteredExportReader
from data.readers.hdf5_reader import HDF5Reader
from data.readers.keysight_reader import KeysightSegmentedReader
from tests.conftest import EXPERIMENT_NAME


def test_open_reader_recognizes_chunked_experiment(synthetic_hdf5):
    with open_reader(synthetic_hdf5) as r:
        assert isinstance(r, HDF5Reader)


def test_open_reader_recognizes_keysight_file(synthetic_keysight_h5):
    with open_reader(synthetic_keysight_h5) as r:
        assert isinstance(r, KeysightSegmentedReader)


def test_open_reader_recognizes_filtered_export_file(synthetic_hdf5, tmp_path):
    with HDF5Reader(synthetic_hdf5) as r:
        result = ingest_experiment(r, EXPERIMENT_NAME, ["UHF", "AE"])
    configs = load_sensor_configs("config/sensors.yaml")
    active_masks = {sensor: np.ones(block.data.shape[0], dtype=bool) for sensor, block in result.sensors.items()}

    out_path = tmp_path / "export.hdf5"
    export_filtered(
        out_path, result.dataset_id, result.experiment, result.normalization.version,
        result.sensors, configs, active_masks, result.environmental, result.events,
    )

    with open_reader(out_path) as r:
        assert isinstance(r, FilteredExportReader)


def test_open_reader_rejects_unknown_schema(tmp_path):
    path = tmp_path / "ajeno.h5"
    with h5py.File(path, mode="w") as f:
        grp = f.create_group("algo_que_no_es_ninguno_de_los_dos_esquemas")
        grp.create_dataset("x", data=np.array([1, 2, 3]))

    with pytest.raises(ValueError, match="No se reconoce el esquema"):
        open_reader(path)
