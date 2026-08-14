"""Prueba de integración end-to-end contra una medición real (no sintética).

Se salta automáticamente si el archivo no está disponible en esta máquina (p. ej. en un
entorno de CI sin acceso a D:\\data\\data\\main). Objetivo: dar confianza de que el
pipeline completo (reader -> ingest -> storage) funciona sobre datos reales grandes y
"sucios" (metadatos, chunks vacíos, tipos de evento variados), no solo sobre el fixture
sintético controlado del resto de la suite.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core.models import load_sensor_configs
from data.ingest import ingest_experiment
from data.readers.hdf5_reader import HDF5Reader
from data.storage import CanonicalStore, write_canonical

REAL_FILE = Path(r"D:\data\data\main\med_5_ago_3.hdf5")

pytestmark = pytest.mark.skipif(not REAL_FILE.exists(), reason=f"Archivo real no disponible: {REAL_FILE}")


def test_full_pipeline_against_real_measurement(tmp_path):
    with HDF5Reader(REAL_FILE) as r:
        experiments = r.list_experiments()
        assert len(experiments) == 1
        exp = experiments[0]
        result = ingest_experiment(r, exp, ["UHF", "AE"])

    # Cifras verificadas manualmente sobre este archivo durante el diseño (ver
    # esquema_med_5_ago_3.md) -- si cambian, o cambió el archivo origen o hay una
    # regresión real en el pipeline de ingesta.
    assert result.sensors["UHF"].data.shape == (12484, 3000)
    assert result.sensors["AE"].data.shape == (20574, 10000)
    assert result.events.timestamps.shape[0] == 98
    assert set(result.events.event_type.tolist()) == {"SHOT", "PA", "FO"}

    for sensor in ("UHF", "AE"):
        block = result.sensors[sensor]
        assert np.all(block.timestamps[:-1] <= block.timestamps[1:]), f"{sensor} no está ordenado"
        assert block.valid_mask.all(), f"{sensor} tiene señales marcadas inválidas inesperadamente"

    configs = load_sensor_configs("config/sensors.yaml")
    out_path = tmp_path / "canonical_real.h5"
    write_canonical(result, configs, out_path)

    with CanonicalStore(out_path) as s:
        assert s.n_signals("UHF") == 12484
        assert s.n_signals("AE") == 20574

        # Acceso aleatorio a una fila lejana, sin cargar el dataset completo.
        row, ts, _, vr = s.get_signal_row("AE", 15000)
        assert row.shape == (10000,)
        assert vr > 0

        # Recorrido por bloques cubre exactamente todas las señales, sin overlap ni huecos.
        cfg = configs["UHF"]
        total = sum(b.data.shape[0] for b in s.iter_blocks("UHF", cfg.block_n_signals))
        assert total == 12484
