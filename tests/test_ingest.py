import numpy as np

from data.ingest import ingest_experiment, ingest_sensor
from data.readers.hdf5_reader import HDF5Reader
from tests.conftest import EXPERIMENT_NAME


def test_ingest_sorts_chronologically_even_when_chunks_are_not(synthetic_hdf5):
    # chunk_000000 tiene timestamps 105,106 (UHF) y chunk_000001 tiene 1,2 (UHF) --
    # deliberadamente al reves del orden alfabetico de chunks (ver conftest.py).
    with HDF5Reader(synthetic_hdf5) as r:
        block = ingest_sensor(r, EXPERIMENT_NAME, "UHF")

    assert block.timestamps.tolist() == sorted(block.timestamps.tolist())
    # La primera fila cronológica debe ser la señal de valor 3.0 (chunk_000001, t=1.0)
    assert block.timestamps[0] == 1.0
    assert np.allclose(block.data[0], 3.0)


def test_ingest_computes_valid_mask_from_metadata(synthetic_hdf5):
    with HDF5Reader(synthetic_hdf5) as r:
        block = ingest_sensor(r, EXPERIMENT_NAME, "UHF")

    # De las 4 señales UHF, una tiene vrange=0 (invalida). Las demas son validas.
    assert block.valid_mask.sum() == 3
    assert block.valid_mask.shape[0] == 4

    invalid_idx = np.where(~block.valid_mask)[0]
    assert invalid_idx.shape[0] == 1
    assert block.vrange[invalid_idx[0]] == 0.0


def test_ingest_ae_excludes_nan_trigger_from_valid_mask(synthetic_hdf5):
    with HDF5Reader(synthetic_hdf5) as r:
        block = ingest_sensor(r, EXPERIMENT_NAME, "AE")

    assert block.valid_mask.sum() == 1  # de 2 señales AE, 1 tiene trigger=NaN
    assert block.data.shape[0] == 2


def test_ingest_minmax_matches_row_extrema(synthetic_hdf5):
    with HDF5Reader(synthetic_hdf5) as r:
        block = ingest_sensor(r, EXPERIMENT_NAME, "UHF")

    for i in range(block.data.shape[0]):
        assert block.minmax[i, 0] == block.data[i].min()
        assert block.minmax[i, 1] == block.data[i].max()


def test_ingest_sensor_with_no_signals_returns_empty_block(synthetic_hdf5):
    # No hay tercer sensor en el fixture; simulamos "sin datos" iterando un experimento
    # ficticio no vacío pero pidiendo un sensor sin señales en absoluto no es posible
    # con el reader real (solo UHF/AE) -- se prueba el caso vacio directamente sobre
    # ingest_sensor con un reader que no produce lotes.
    class _EmptyReader:
        dataset_id = "empty"

        def iter_signal_batches(self, experiment, sensor):
            return iter(())

    block = ingest_sensor(_EmptyReader(), "exp", "UHF")
    assert block.data.shape == (0, 0)
    assert block.timestamps.shape[0] == 0
    assert block.valid_mask.shape[0] == 0
    assert block.minmax.shape == (0, 2)


def test_ingest_experiment_orchestrates_both_sensors(synthetic_hdf5):
    with HDF5Reader(synthetic_hdf5) as r:
        result = ingest_experiment(r, EXPERIMENT_NAME, ["UHF", "AE"])

    assert result.experiment == EXPERIMENT_NAME
    assert set(result.sensors.keys()) == {"UHF", "AE"}
    assert result.sensors["UHF"].data.shape[0] == 4
    assert result.sensors["AE"].data.shape[0] == 2
    assert result.environmental.timestamps.shape[0] == 4
    assert result.events.timestamps.shape[0] == 2
    assert result.normalization.version == "v1_divide_by_vrange"


def test_ingest_sensor_streaming_to_h5_group(synthetic_hdf5, tmp_path):
    import h5py
    from data.readers.hdf5_reader import HDF5Reader
    from data.ingest import ingest_sensor
    from data.storage import CanonicalStore

    out_file = tmp_path / "streamed_sensor.h5"
    with HDF5Reader(synthetic_hdf5) as r:
        with h5py.File(out_file, "w") as f:
            grp = f.create_group("UHF")
            ingest_sensor(r, EXPERIMENT_NAME, "UHF", h5_group=grp)

    with CanonicalStore(out_file) as store:
        assert store.n_signals("UHF") == 4
        # Timestamps are sorted chronologically
        ts = store.get_all_timestamps("UHF")
        assert ts.tolist() == sorted(ts.tolist())
        assert ts[0] == 1.0

        # Data rows accessed chronologically
        row0, t0, trig0, vr0 = store.get_signal_row("UHF", 0)
        assert np.allclose(row0, 3.0)
        assert t0 == 1.0

        lazy_blk = store.create_lazy_block("UHF")
        assert lazy_blk.n_signals == 4
        assert np.allclose(lazy_blk.row(0), 3.0)


def test_ingest_experiment_with_canonical_store_path(synthetic_hdf5, tmp_path):
    from data.readers.hdf5_reader import HDF5Reader
    from data.ingest import ingest_experiment
    from core.models import LazyDataProxy

    out_file = tmp_path / "canonical_experiment.h5"
    with HDF5Reader(synthetic_hdf5) as r:
        result = ingest_experiment(r, EXPERIMENT_NAME, ["UHF", "AE"], canonical_store_path=out_file)

    assert result.experiment == EXPERIMENT_NAME
    assert set(result.sensors.keys()) == {"UHF", "AE"}

    uhf = result.sensors["UHF"]
    assert uhf.n_signals == 4
    # Underlying data is lazy proxy, not full in-RAM matrix
    assert isinstance(uhf.data, LazyDataProxy)
    assert uhf.data.shape == (4, uhf.n_samples)

    # First chronological signal is 3.0
    assert np.allclose(uhf.row(0), 3.0)
    assert np.allclose(uhf.rows(0, 2)[0], 3.0)

    ae = result.sensors["AE"]
    assert ae.n_signals == 2
    assert isinstance(ae.data, LazyDataProxy)
    assert ae.data.shape == (2, ae.n_samples)

