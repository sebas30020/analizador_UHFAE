import numpy as np

from data.readers.keysight_reader import KeysightSegmentedReader
from tests.conftest import KEYSIGHT_Y_DISP_RANGE, KEYSIGHT_Y_INC, KEYSIGHT_Y_ORG


def test_list_experiments_uses_model_and_date(synthetic_keysight_h5):
    with KeysightSegmentedReader(synthetic_keysight_h5) as r:
        assert r.list_experiments() == ["DSOS804A 19-Aug-2026 16:03:40"]


def test_list_experiments_falls_back_to_filename_without_frame(synthetic_keysight_h5_no_frame):
    with KeysightSegmentedReader(synthetic_keysight_h5_no_frame) as r:
        experiments = r.list_experiments()
    assert len(experiments) == 1
    assert experiments[0]  # no vacío -- usa el nombre de archivo como último recurso


def test_dataset_id_is_stable_for_same_file(synthetic_keysight_h5):
    with KeysightSegmentedReader(synthetic_keysight_h5) as r1:
        id1 = r1.dataset_id
    with KeysightSegmentedReader(synthetic_keysight_h5) as r2:
        id2 = r2.dataset_id
    assert id1 == id2


def test_available_sensors_is_uhf_ks_only(synthetic_keysight_h5):
    with KeysightSegmentedReader(synthetic_keysight_h5) as r:
        assert r.available_sensors() == ["UHF_KS"]


def test_iter_signal_batches_orders_by_segment_number_not_alphabetically(synthetic_keysight_h5):
    """Trampa verificada en los archivos reales (esquema_keysight_h5.md): el orden
    alfabético de "Seg1", "Seg10", "Seg11", "Seg12", "Seg2", ... contradice el orden
    cronológico -- el reader debe entregar las 12 señales en orden 1..12, no alfabético.
    """
    with KeysightSegmentedReader(synthetic_keysight_h5) as r:
        experiment = r.list_experiments()[0]
        batches = list(r.iter_signal_batches(experiment, "UHF_KS"))

    data = np.concatenate([b.data for b in batches], axis=0)
    timestamps = np.concatenate([b.timestamps for b in batches])

    assert data.shape[0] == 12
    # raw=n en cada segmento -> v = n*YInc + YOrg, exacto y creciente con n.
    expected = np.arange(1, 13) * KEYSIGHT_Y_INC + KEYSIGHT_Y_ORG
    np.testing.assert_allclose(data[:, 0], expected)
    assert np.all(data[:-1, 0] < data[1:, 0])
    # timestamps estrictamente crecientes (ya en orden cronológico correcto).
    assert np.all(np.diff(timestamps) > 0)


def test_iter_signal_batches_converts_int16_to_volts_exactly(synthetic_keysight_h5):
    with KeysightSegmentedReader(synthetic_keysight_h5) as r:
        experiment = r.list_experiments()[0]
        batch = next(r.iter_signal_batches(experiment, "UHF_KS"))
    # Toda fila es constante (raw=n en las 4 muestras) -- min y max deben coincidir.
    assert np.all(batch.data.min(axis=1) == batch.data.max(axis=1))


def test_iter_signal_batches_vrange_is_half_of_y_disp_range(synthetic_keysight_h5):
    with KeysightSegmentedReader(synthetic_keysight_h5) as r:
        experiment = r.list_experiments()[0]
        batch = next(r.iter_signal_batches(experiment, "UHF_KS"))
    np.testing.assert_allclose(batch.vrange, KEYSIGHT_Y_DISP_RANGE / 2.0, rtol=1e-6)


def test_iter_signal_batches_trigger_is_zero_and_finite(synthetic_keysight_h5):
    with KeysightSegmentedReader(synthetic_keysight_h5) as r:
        experiment = r.list_experiments()[0]
        batch = next(r.iter_signal_batches(experiment, "UHF_KS"))
    assert np.all(batch.trigger == 0.0)
    assert np.all(np.isfinite(batch.trigger))


def test_iter_signal_batches_timestamps_monotonic_and_absolute(synthetic_keysight_h5):
    with KeysightSegmentedReader(synthetic_keysight_h5) as r:
        experiment = r.list_experiments()[0]
        batch = next(r.iter_signal_batches(experiment, "UHF_KS"))
    # Ancladas a Frame.Date (epoch de 2026-08-19), no relativas a cero.
    assert np.all(batch.timestamps > 1_700_000_000.0)
    assert np.all(np.diff(batch.timestamps) > 0)


def test_iter_signal_batches_without_frame_uses_relative_timestamps(synthetic_keysight_h5_no_frame):
    with KeysightSegmentedReader(synthetic_keysight_h5_no_frame) as r:
        experiment = r.list_experiments()[0]
        batch = next(r.iter_signal_batches(experiment, "UHF_KS"))
    # Sin Frame.Date, epoch=0 -- los timestamps son los SegmentedTimeTag crudos.
    np.testing.assert_allclose(batch.timestamps, [0.0, 0.1, 0.2])


def test_iter_signal_batches_empty_for_other_sensors(synthetic_keysight_h5):
    with KeysightSegmentedReader(synthetic_keysight_h5) as r:
        experiment = r.list_experiments()[0]
        assert list(r.iter_signal_batches(experiment, "UHF")) == []
        assert list(r.iter_signal_batches(experiment, "AE")) == []


def test_environmental_and_events_are_empty(synthetic_keysight_h5):
    with KeysightSegmentedReader(synthetic_keysight_h5) as r:
        experiment = r.list_experiments()[0]
        env = r.get_environmental(experiment)
        events = r.get_events(experiment)
    assert env.timestamps.shape[0] == 0
    assert events.timestamps.shape[0] == 0


def test_multi_channel_uses_first_in_order_and_warns(synthetic_keysight_h5_multi_channel, caplog):
    with KeysightSegmentedReader(synthetic_keysight_h5_multi_channel) as r:
        experiment = r.list_experiments()[0]
        attrs = r.get_experiment_attrs(experiment)
        assert attrs["channel"] == "Channel 1"
        assert attrs["n_channels_available"] == 2
        with caplog.at_level("WARNING"):
            list(r.iter_signal_batches(experiment, "UHF_KS"))
    assert any("2 canales" in rec.message for rec in caplog.records)


def test_sensor_config_overrides_reflect_file_attrs(synthetic_keysight_h5):
    with KeysightSegmentedReader(synthetic_keysight_h5) as r:
        experiment = r.list_experiments()[0]
        overrides = r.sensor_config_overrides(experiment, "UHF_KS")
    assert overrides == {"fs_hz": 1.0 / 5e-11, "n_samples": 4}
    assert r.sensor_config_overrides(experiment, "UHF") == {}
