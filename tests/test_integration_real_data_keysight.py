"""Prueba de integración end-to-end contra una medición real Keysight (rama
``lectura_keysight``), en la misma línea que ``test_integration_real_data.py`` para
``med_5_ago_3.hdf5``: se salta si el archivo no está disponible en esta máquina.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from core.models import load_sensor_configs
from data.ingest import ingest_experiment
from data.readers.factory import open_reader
from data.readers.keysight_reader import KeysightSegmentedReader
from data.storage import CanonicalStore, write_canonical

REAL_FILE = Path(r"D:\data\data\main\ruido\test-1.h5")

pytestmark = pytest.mark.skipif(not REAL_FILE.exists(), reason=f"Archivo real no disponible: {REAL_FILE}")


def test_full_pipeline_against_real_keysight_measurement(tmp_path):
    with open_reader(REAL_FILE) as r:
        assert isinstance(r, KeysightSegmentedReader)
        experiments = r.list_experiments()
        assert len(experiments) == 1
        exp = experiments[0]

        # Cifras verificadas manualmente sobre este archivo (ver
        # archivos_md/PLAN_LECTURA_KEYSIGHT.md §1) -- si cambian, o cambió el archivo
        # origen o hay una regresión real en el reader.
        assert r.available_sensors() == ["UHF_KS"]
        overrides = r.sensor_config_overrides(exp, "UHF_KS")
        assert overrides == {"fs_hz": pytest.approx(2.0e10), "n_samples": 20000}

        result = ingest_experiment(r, exp, ["UHF_KS"])

    block = result.sensors["UHF_KS"]
    assert block.data.shape == (144, 20000)
    assert np.all(block.timestamps[:-1] <= block.timestamps[1:]), "UHF_KS no está ordenado"
    assert block.valid_mask.all()
    assert np.allclose(block.vrange, 0.4)
    assert np.all(block.trigger == 0.0)

    duration_s = block.timestamps[-1] - block.timestamps[0]
    assert duration_s == pytest.approx(82.909869, abs=1e-3)

    assert result.events.timestamps.shape[0] == 0
    assert result.environmental.timestamps.shape[0] == 0

    configs = load_sensor_configs("config/sensors.yaml")
    configs = {**configs, "UHF_KS": replace(configs["UHF_KS"], **overrides)}
    out_path = tmp_path / "canonical_real_keysight.h5"
    write_canonical(result, configs, out_path)

    with CanonicalStore(out_path) as s:
        assert s.n_signals("UHF_KS") == 144
        row, ts, _trigger, vr = s.get_signal_row("UHF_KS", 100)
        assert row.shape == (20000,)
        assert vr == pytest.approx(0.4)
