"""Pruebas de ``data/export.py`` (Fase 6, "Exportar datos filtrados…"): la partición
escrita coincide exactamente con la máscara, el archivo resultante se relee limpio con
``data.readers.factory.open_reader`` en las tres particiones, y el perfil efectivo por
sensor (ambientales/eventos incluidos) sobrevive al viaje de ida y vuelta.
"""
from __future__ import annotations

import numpy as np
import pytest

from core.models import load_sensor_configs
from data.export import export_filtered
from data.ingest import ingest_experiment
from data.readers.factory import open_reader
from data.readers.hdf5_reader import HDF5Reader
from tests.conftest import EXPERIMENT_NAME


def _ingest_synthetic(synthetic_hdf5):
    with HDF5Reader(synthetic_hdf5) as r:
        return ingest_experiment(r, EXPERIMENT_NAME, ["UHF", "AE"])


def _alternating_mask(n: int) -> np.ndarray:
    """Máscara determinista y no trivial: excluye los índices impares."""
    mask = np.ones(n, dtype=bool)
    mask[1::2] = False
    return mask


def test_export_partitions_match_the_mask_exactly(synthetic_hdf5, tmp_path):
    result = _ingest_synthetic(synthetic_hdf5)
    configs = load_sensor_configs("config/sensors.yaml")
    active_masks = {sensor: _alternating_mask(block.data.shape[0]) for sensor, block in result.sensors.items()}

    out_path = tmp_path / "export.hdf5"
    n_resultantes, n_filtradas = export_filtered(
        out_path, result.dataset_id, result.experiment, result.normalization.version,
        result.sensors, configs, active_masks, result.environmental, result.events,
    )

    total = sum(b.data.shape[0] for b in result.sensors.values())
    assert n_resultantes + n_filtradas == total
    assert n_resultantes == sum(int(m.sum()) for m in active_masks.values())

    with open_reader(out_path, partition="resultantes") as reader:
        assert reader.available_sensors() == ["UHF", "AE"]
        ingested = ingest_experiment(reader, reader.list_experiments()[0], ["UHF", "AE"])
    with open_reader(out_path, partition="filtradas") as reader:
        excluded = ingest_experiment(reader, reader.list_experiments()[0], ["UHF", "AE"])

    for sensor in ("UHF", "AE"):
        mask = active_masks[sensor]
        original = result.sensors[sensor]
        kept = ingested.sensors[sensor]
        dropped = excluded.sensors[sensor]

        assert kept.data.shape[0] == int(mask.sum())
        assert dropped.data.shape[0] == int((~mask).sum())
        # ingest_sensor ordena por timestamp: como el original ya venía ordenado, las
        # dos particiones deben coincidir con la selección booleana directa.
        assert np.allclose(kept.data, original.data[mask])
        assert np.allclose(dropped.data, original.data[~mask])
        assert np.array_equal(kept.timestamps, original.timestamps[mask])
        assert np.array_equal(dropped.timestamps, original.timestamps[~mask])
        assert np.array_equal(kept.valid_mask, original.valid_mask[mask])
        assert np.allclose(kept.minmax, original.minmax[mask])


def test_export_ambas_partition_reconstructs_the_full_original_set(synthetic_hdf5, tmp_path):
    result = _ingest_synthetic(synthetic_hdf5)
    configs = load_sensor_configs("config/sensors.yaml")
    active_masks = {sensor: _alternating_mask(block.data.shape[0]) for sensor, block in result.sensors.items()}

    out_path = tmp_path / "export.hdf5"
    export_filtered(
        out_path, result.dataset_id, result.experiment, result.normalization.version,
        result.sensors, configs, active_masks, result.environmental, result.events,
    )

    with open_reader(out_path, partition="ambas") as reader:
        combined = ingest_experiment(reader, reader.list_experiments()[0], ["UHF", "AE"])

    for sensor in ("UHF", "AE"):
        original = result.sensors[sensor]
        merged = combined.sensors[sensor]
        assert merged.data.shape[0] == original.data.shape[0]
        # ingest_sensor reordena por timestamp -- el original del fixture ya está
        # estrictamente ordenado (ver docstring de synthetic_hdf5), así que el
        # resultado recombinado debe coincidir fila a fila, no solo como conjunto.
        assert np.array_equal(merged.timestamps, original.timestamps)
        assert np.allclose(merged.data, original.data)
        assert np.array_equal(merged.valid_mask, original.valid_mask)


def test_export_dataset_id_differs_by_partition(synthetic_hdf5, tmp_path):
    result = _ingest_synthetic(synthetic_hdf5)
    configs = load_sensor_configs("config/sensors.yaml")
    active_masks = {sensor: np.ones(block.data.shape[0], dtype=bool) for sensor, block in result.sensors.items()}

    out_path = tmp_path / "export.hdf5"
    export_filtered(
        out_path, result.dataset_id, result.experiment, result.normalization.version,
        result.sensors, configs, active_masks, result.environmental, result.events,
    )

    with open_reader(out_path, partition="resultantes") as r1:
        id_resultantes = r1.dataset_id
    with open_reader(out_path, partition="filtradas") as r2:
        id_filtradas = r2.dataset_id
    with open_reader(out_path, partition="ambas") as r3:
        id_ambas = r3.dataset_id

    assert len({id_resultantes, id_filtradas, id_ambas}) == 3


def test_export_preserves_effective_sensor_profile_and_aux_series(synthetic_hdf5, tmp_path):
    result = _ingest_synthetic(synthetic_hdf5)
    configs = load_sensor_configs("config/sensors.yaml")
    active_masks = {sensor: np.ones(block.data.shape[0], dtype=bool) for sensor, block in result.sensors.items()}

    out_path = tmp_path / "export.hdf5"
    export_filtered(
        out_path, result.dataset_id, result.experiment, result.normalization.version,
        result.sensors, configs, active_masks, result.environmental, result.events,
    )

    with open_reader(out_path) as reader:  # default: "resultantes"
        experiment = reader.list_experiments()[0]
        overrides = reader.sensor_config_overrides(experiment, "UHF")
        env = reader.get_environmental(experiment)
        events = reader.get_events(experiment)

    assert overrides["fs_hz"] == configs["UHF"].fs_hz
    assert overrides["n_samples"] == configs["UHF"].n_samples
    assert overrides["axis_unit"] == configs["UHF"].axis_unit
    assert overrides["has_trigger_metadata"] == configs["UHF"].has_trigger_metadata
    assert env.timestamps.shape == result.environmental.timestamps.shape
    assert events.timestamps.shape == result.events.timestamps.shape
    assert set(events.event_type.tolist()) == set(result.events.event_type.tolist())


def test_export_handles_a_sensor_fully_excluded_by_the_filter(synthetic_hdf5, tmp_path):
    result = _ingest_synthetic(synthetic_hdf5)
    configs = load_sensor_configs("config/sensors.yaml")
    active_masks = {
        "UHF": np.zeros(result.sensors["UHF"].data.shape[0], dtype=bool),
        "AE": np.ones(result.sensors["AE"].data.shape[0], dtype=bool),
    }

    out_path = tmp_path / "export.hdf5"
    export_filtered(
        out_path, result.dataset_id, result.experiment, result.normalization.version,
        result.sensors, configs, active_masks, result.environmental, result.events,
    )

    with open_reader(out_path, partition="resultantes") as reader:
        ingested = ingest_experiment(reader, reader.list_experiments()[0], ["UHF", "AE"])

    assert ingested.sensors["UHF"].data.shape[0] == 0
    assert ingested.sensors["AE"].data.shape[0] == result.sensors["AE"].data.shape[0]


def test_open_reader_recognizes_filtered_export_file(synthetic_hdf5, tmp_path):
    from data.readers.filtered_export_reader import FilteredExportReader

    result = _ingest_synthetic(synthetic_hdf5)
    configs = load_sensor_configs("config/sensors.yaml")
    active_masks = {sensor: np.ones(block.data.shape[0], dtype=bool) for sensor, block in result.sensors.items()}

    out_path = tmp_path / "export.hdf5"
    export_filtered(
        out_path, result.dataset_id, result.experiment, result.normalization.version,
        result.sensors, configs, active_masks, result.environmental, result.events,
    )

    with open_reader(out_path) as reader:
        assert isinstance(reader, FilteredExportReader)
