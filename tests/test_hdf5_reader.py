import numpy as np

from data.readers.hdf5_reader import HDF5Reader
from tests.conftest import EXPERIMENT_NAME


def test_list_experiments(synthetic_hdf5):
    with HDF5Reader(synthetic_hdf5) as r:
        assert r.list_experiments() == [EXPERIMENT_NAME]


def test_dataset_id_is_stable_for_same_file(synthetic_hdf5):
    with HDF5Reader(synthetic_hdf5) as r1:
        id1 = r1.dataset_id
    with HDF5Reader(synthetic_hdf5) as r2:
        id2 = r2.dataset_id
    assert id1 == id2


def test_iter_signal_batches_skips_empty_chunks(synthetic_hdf5):
    with HDF5Reader(synthetic_hdf5) as r:
        batches = list(r.iter_signal_batches(EXPERIMENT_NAME, "UHF"))
    # chunk_000000 y chunk_000001 tienen UHF; chunk_000002 no -> 2 lotes, no 3.
    assert len(batches) == 2
    total_signals = sum(b.data.shape[0] for b in batches)
    assert total_signals == 4


def test_iter_signal_batches_ae_includes_invalid_metadata_rows(synthetic_hdf5):
    # El reader NO filtra invalidas -- eso es responsabilidad de ingest/normalization.
    with HDF5Reader(synthetic_hdf5) as r:
        batches = list(r.iter_signal_batches(EXPERIMENT_NAME, "AE"))
    total = sum(b.data.shape[0] for b in batches)
    assert total == 2  # una en cada chunk con AE


def test_get_events_defaults_missing_type_to_shot(synthetic_hdf5):
    with HDF5Reader(synthetic_hdf5) as r:
        events = r.get_events(EXPERIMENT_NAME)
    # 2 eventos en total: uno con type='SHOT' explicito, otro sin dataset type (default 'SHOT')
    assert events.timestamps.shape[0] == 2
    assert set(events.event_type.tolist()) == {"SHOT"}
    # ordenados por timestamp: 0.5 (chunk1) antes que 104.0 (chunk0)
    assert events.timestamps.tolist() == sorted(events.timestamps.tolist())


def test_get_environmental_concatenates_and_sorts_across_chunks(synthetic_hdf5):
    with HDF5Reader(synthetic_hdf5) as r:
        env = r.get_environmental(EXPERIMENT_NAME)
    # 2 (chunk0) + 1 (chunk1) + 1 (chunk2) = 4 muestras ambientales
    assert env.timestamps.shape[0] == 4
    assert np.all(env.timestamps[:-1] <= env.timestamps[1:])
