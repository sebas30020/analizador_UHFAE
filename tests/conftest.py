"""Fixtures compartidas para la suite de Fase 1.

``synthetic_hdf5`` construye un archivo HDF5 pequeño y rápido de crear que replica el
esquema real (``esquema_med_5_ago_3.md``), pero con casos borde deliberados que el
archivo real de prueba no garantiza tener en una posición conocida:
- Chunks entregados en orden **no cronológico** (para probar que ``ingest.py`` ordena
  explícitamente y no asume el orden de llegada del reader).
- Una señal UHF con ``vrange = 0`` (metadato inválido -> debe excluirse).
- Una señal AE con ``trigger = NaN`` (metadato inválido -> debe excluirse).
- Un chunk sin datos de ningún sensor (solo ambiental).
- Un chunk de eventos sin dataset ``type`` (para probar el default a "SHOT").
"""
from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

EXPERIMENT_NAME = "Test - synthetic"
UHF_M = 16
AE_M = 32


def _write_signal_group(chunk_grp: h5py.Group, name: str, data, timestamps, trigger, vrange) -> None:
    grp = chunk_grp.create_group(name)
    grp.create_dataset("data", data=np.asarray(data, dtype=np.float32))
    grp.create_dataset("timestamps", data=np.asarray(timestamps, dtype=np.float64))
    grp.create_dataset("triggers", data=np.asarray(trigger, dtype=np.float64))
    grp.create_dataset("vranges", data=np.asarray(vrange, dtype=np.float64))


@pytest.fixture
def synthetic_hdf5(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic.hdf5"
    with h5py.File(path, mode="w") as f:
        exp = f.create_group(EXPERIMENT_NAME)
        exp.attrs["date"] = "2026-01-01T00-00-00Z"
        exp.attrs["chunk_duration_s"] = 10.0
        exp.attrs["initial_dead_time_s"] = 0.0
        exp.attrs["description"] = "fixture de prueba"
        exp.attrs["version"] = 2

        # --- chunk_000001 (timestamps MAS TARDIOS, pero se escribe/enumera antes que
        #     chunk_000000 en orden alfabetico normal NO -> forzamos lo contrario:
        #     chunk_000000 tiene timestamps mayores que chunk_000001, para que un
        #     ingest ingenuo que confie en el orden alfabetico de chunks quede mal
        #     ordenado si no ordena explicitamente por timestamp). ---
        c0 = exp.create_group("chunk_000000")
        c0.attrs.update(dict(is_baseline=False, chunk_index=0, start_time=100.0, end_time=110.0,
                              n_signals=2, signal_offset=0, n_ae_signals=1, ae_signal_offset=0))
        _write_signal_group(
            c0, "signals",
            data=[[1.0] * UHF_M, [2.0] * UHF_M],
            timestamps=[105.0, 106.0],
            trigger=[0.01, 0.02],
            vrange=[0.5, 0.5],
        )
        _write_signal_group(
            c0, "ae_signals",
            data=[[10.0] * AE_M],
            timestamps=[105.5],
            trigger=[0.01],
            vrange=[1.0],
        )
        ev0 = c0.create_group("events")
        ev0.create_dataset("timestamps", data=np.array([104.0]))
        ev0.create_dataset("type", data=np.array(["SHOT"], dtype=object), dtype=h5py.special_dtype(vlen=str))
        hum0 = c0.create_group("humidity")
        hum0.create_dataset("timestamps", data=np.array([101.0, 108.0]))
        hum0.create_dataset("temperature", data=np.array([22.0, 22.5]))
        hum0.create_dataset("humidity", data=np.array([40.0, 41.0]))

        # --- chunk_000001: timestamps MAS TEMPRANOS que chunk_000000 a proposito. ---
        c1 = exp.create_group("chunk_000001")
        c1.attrs.update(dict(is_baseline=True, chunk_index=1, start_time=0.0, end_time=10.0,
                              n_signals=2, signal_offset=2, n_ae_signals=1, ae_signal_offset=1))
        _write_signal_group(
            c1, "signals",
            # segunda señal con vrange=0 -> invalida
            data=[[3.0] * UHF_M, [4.0] * UHF_M],
            timestamps=[1.0, 2.0],
            trigger=[0.03, 0.04],
            vrange=[0.5, 0.0],
        )
        _write_signal_group(
            c1, "ae_signals",
            # trigger NaN -> invalida
            data=[[20.0] * AE_M],
            timestamps=[1.5],
            trigger=[np.nan],
            vrange=[1.0],
        )
        # eventos SIN dataset 'type' (para probar default a "SHOT")
        ev1 = c1.create_group("events")
        ev1.create_dataset("timestamps", data=np.array([0.5]))
        hum1 = c1.create_group("humidity")
        hum1.create_dataset("timestamps", data=np.array([0.2]))
        hum1.create_dataset("temperature", data=np.array([21.0]))
        hum1.create_dataset("humidity", data=np.array([39.0]))

        # --- chunk_000002: sin senales de ningun sensor, solo ambiental. ---
        c2 = exp.create_group("chunk_000002")
        c2.attrs.update(dict(is_baseline=False, chunk_index=2, start_time=110.0, end_time=120.0,
                              n_signals=0, signal_offset=4, n_ae_signals=0, ae_signal_offset=2))
        hum2 = c2.create_group("humidity")
        hum2.create_dataset("timestamps", data=np.array([115.0]))
        hum2.create_dataset("temperature", data=np.array([23.0]))
        hum2.create_dataset("humidity", data=np.array([42.0]))

    return path
