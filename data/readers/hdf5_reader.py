"""Reader concreto para el esquema HDF5 real documentado en ``esquema_med_5_ago_3.md``.

```
<experiment>/                        (attrs: date, chunk_duration_s, initial_dead_time_s,
                                       description, version)
  chunk_NNNNNN/                      (attrs: is_baseline, chunk_index, start_time, end_time,
                                       n_signals, signal_offset, n_ae_signals, ae_signal_offset)
    signals/      {data, timestamps, triggers, vranges}      # UHF
    ae_signals/   {data, timestamps, triggers, vranges}      # AE
    events/       {timestamps, type}                          # puede estar vacío
    humidity/     {humidity, temperature, timestamps}         # largo variable por chunk
```

Trata el archivo origen como estrictamente de solo lectura (se abre con ``mode="r"``).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import h5py
import numpy as np

from core.models import EnvironmentalSeries, EventSeries, SensorName
from data.readers.base import OriginReader, RawSignalBatch


def _decode(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


class HDF5Reader(OriginReader):
    """Lee el esquema real de ``main/*.hdf5`` (un archivo = un experimento independiente,
    ver FASE0_DISENO...md §10 supuesto #6)."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._file: h5py.File | None = None

    def __enter__(self) -> "HDF5Reader":
        self._file = h5py.File(self._path, mode="r")
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    @property
    def _f(self) -> h5py.File:
        if self._file is None:
            raise RuntimeError("HDF5Reader debe usarse como context manager (with HDF5Reader(...) as r:)")
        return self._file

    @property
    def dataset_id(self) -> str:
        """``ruta:tamaño:mtime_ns`` — estable mientras el archivo no cambie, barato de calcular
        (evita hashear archivos de cientos de MB en cada apertura)."""
        stat = os.stat(self._path)
        return f"{self._path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"

    def list_experiments(self) -> list[str]:
        return list(self._f.keys())

    def get_experiment_attrs(self, experiment: str) -> dict:
        return {k: (_decode(v) if isinstance(v, bytes) else v) for k, v in self._f[experiment].attrs.items()}

    def _sorted_chunk_names(self, experiment: str) -> list[str]:
        return sorted(self._f[experiment].keys())

    def iter_signal_batches(self, experiment: str, sensor: SensorName) -> Iterator[RawSignalBatch]:
        from core.models import load_sensor_configs

        # El nombre del grupo HDF5 ("signals"/"ae_signals") es config, no un literal cableado.
        config_path = Path(__file__).resolve().parents[2] / "config" / "sensors.yaml"
        group_name = load_sensor_configs(config_path)[sensor].hdf5_group

        experiment_group = self._f[experiment]
        for chunk_name in self._sorted_chunk_names(experiment):
            chunk = experiment_group[chunk_name]
            sensor_group = chunk.get(group_name)
            if sensor_group is None or "data" not in sensor_group:
                continue
            data = sensor_group["data"]
            if data.shape[0] == 0:
                continue

            vrange = sensor_group["vranges"][:] if "vranges" in sensor_group else np.zeros(data.shape[0])
            yield RawSignalBatch(
                data=data[:],
                timestamps=sensor_group["timestamps"][:],
                trigger=sensor_group["triggers"][:],
                vrange=vrange,
            )

    def get_environmental(self, experiment: str) -> EnvironmentalSeries:
        timestamps_parts: list[np.ndarray] = []
        temperature_parts: list[np.ndarray] = []
        humidity_parts: list[np.ndarray] = []

        experiment_group = self._f[experiment]
        for chunk_name in self._sorted_chunk_names(experiment):
            hum_group = experiment_group[chunk_name].get("humidity")
            if hum_group is None or "timestamps" not in hum_group:
                continue
            if hum_group["timestamps"].shape[0] == 0:
                continue
            timestamps_parts.append(hum_group["timestamps"][:])
            temperature_parts.append(hum_group["temperature"][:])
            humidity_parts.append(hum_group["humidity"][:])

        if not timestamps_parts:
            empty = np.array([], dtype=np.float64)
            return EnvironmentalSeries(timestamps=empty, temperature=empty.copy(), humidity=empty.copy())

        timestamps = np.concatenate(timestamps_parts)
        temperature = np.concatenate(temperature_parts)
        humidity = np.concatenate(humidity_parts)
        order = np.argsort(timestamps, kind="stable")
        return EnvironmentalSeries(
            timestamps=timestamps[order], temperature=temperature[order], humidity=humidity[order]
        )

    def get_events(self, experiment: str) -> EventSeries:
        timestamps_parts: list[np.ndarray] = []
        type_parts: list[np.ndarray] = []

        experiment_group = self._f[experiment]
        for chunk_name in self._sorted_chunk_names(experiment):
            ev_group = experiment_group[chunk_name].get("events")
            if ev_group is None or "timestamps" not in ev_group:
                continue
            ts = ev_group["timestamps"][:]
            if ts.shape[0] == 0:
                continue
            if "type" in ev_group:
                types = np.array([_decode(v) for v in ev_group["type"][:]])
            else:
                types = np.array(["SHOT"] * ts.shape[0])
            timestamps_parts.append(ts)
            type_parts.append(types)

        if not timestamps_parts:
            return EventSeries(timestamps=np.array([], dtype=np.float64), event_type=np.array([], dtype="<U16"))

        timestamps = np.concatenate(timestamps_parts)
        event_type = np.concatenate(type_parts)
        order = np.argsort(timestamps, kind="stable")
        return EventSeries(timestamps=timestamps[order], event_type=event_type[order])
