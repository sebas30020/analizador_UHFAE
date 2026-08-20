import h5py
import numpy as np
import pytest

from data.readers.factory import open_reader
from data.readers.hdf5_reader import HDF5Reader
from data.readers.keysight_reader import KeysightSegmentedReader


def test_open_reader_recognizes_chunked_experiment(synthetic_hdf5):
    with open_reader(synthetic_hdf5) as r:
        assert isinstance(r, HDF5Reader)


def test_open_reader_recognizes_keysight_file(synthetic_keysight_h5):
    with open_reader(synthetic_keysight_h5) as r:
        assert isinstance(r, KeysightSegmentedReader)


def test_open_reader_rejects_unknown_schema(tmp_path):
    path = tmp_path / "ajeno.h5"
    with h5py.File(path, mode="w") as f:
        grp = f.create_group("algo_que_no_es_ninguno_de_los_dos_esquemas")
        grp.create_dataset("x", data=np.array([1, 2, 3]))

    with pytest.raises(ValueError, match="No se reconoce el esquema"):
        open_reader(path)
