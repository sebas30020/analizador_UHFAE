"""Persistencia de la matriz global canónica en HDF5 (FASE0_DISENO_Analizador_UHF_AE.md §3.2, §5).

Decisión de formato justificada en el diseño de Fase 0: HDF5 (h5py), chunked y comprimido,
con acceso aleatorio por fila sin cargar el dataset completo en RAM. El archivo canónico es
un artefacto **nuevo e independiente** del origen — nunca se escribe sobre él (§3.2).

Esquema del archivo canónico:

```
/<SENSOR>/                    ("UHF" | "AE", uno o ambos grupos según lo ingerido)
    data          (N, M) float32, chunked, gzip   # cruda, sin normalizar (§3.1: la
                                                    # normalización se aplica al leer,
                                                    # nunca se persiste una copia normalizada)
    timestamps    (N,) float64
    trigger       (N,) float64
    vrange        (N,) float64
    valid_mask    (N,) bool (almacenado como uint8)
    minmax        (N, 2) float32
    attrs: fs_hz, n_samples, freq_limit_hz, dt_s

/environmental/
    timestamps, temperature, humidity   (K,) float64

/events/
    timestamps  (E,) float64
    type        (E,) string (vlen utf-8)

root attrs: dataset_id, experiment, normalization_version, schema_version
```
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import h5py
import numpy as np

from core.models import EnvironmentalSeries, EventSeries, IngestResult, SensorConfig, SensorName, SignalBlock

SCHEMA_VERSION = 1
_GZIP_LEVEL = 4
_ALL_SENSORS: tuple[SensorName, ...] = ("UHF", "AE", "UHF_KS")


def write_canonical(
    result: IngestResult,
    sensor_configs: dict[SensorName, SensorConfig],
    path: str | Path,
) -> None:
    """Escribe un :class:`~core.models.IngestResult` al archivo HDF5 canónico en ``path``.

    Sobrescribe si ya existe (es un artefacto derivado, regenerable desde el origen).
    """
    path = Path(path)
    with h5py.File(path, mode="w") as f:
        f.attrs["dataset_id"] = result.dataset_id
        f.attrs["experiment"] = result.experiment
        f.attrs["normalization_version"] = result.normalization.version
        f.attrs["schema_version"] = SCHEMA_VERSION

        for sensor, block in result.sensors.items():
            _write_sensor_block(f, sensor, block, sensor_configs[sensor])

        _write_environmental(f, result.environmental)
        _write_events(f, result.events)


def _chunk_rows(n_signals: int, n_samples: int, target_block_bytes: int = 8 * 1024 * 1024) -> int:
    if n_signals == 0 or n_samples == 0:
        return 1
    bytes_per_row = n_samples * 4
    rows = max(1, min(n_signals, target_block_bytes // bytes_per_row))
    return rows


def _write_sensor_block(f: h5py.File, sensor: SensorName, block: SignalBlock, config: SensorConfig) -> None:
    grp = f.create_group(sensor)
    grp.attrs["fs_hz"] = config.fs_hz
    grp.attrs["n_samples"] = config.n_samples
    grp.attrs["freq_limit_hz"] = config.freq_limit_hz
    grp.attrs["dt_s"] = config.dt_s

    n = block.data.shape[0]
    m = block.data.shape[1] if n > 0 else config.n_samples
    row_chunk = _chunk_rows(n, m)

    grp.create_dataset(
        "data", data=block.data, chunks=(row_chunk, m) if n > 0 else None,
        compression="gzip", compression_opts=_GZIP_LEVEL,
    )
    grp.create_dataset("timestamps", data=block.timestamps)
    grp.create_dataset("trigger", data=block.trigger)
    grp.create_dataset("vrange", data=block.vrange)
    grp.create_dataset("valid_mask", data=block.valid_mask.astype(np.uint8))
    grp.create_dataset("minmax", data=block.minmax, compression="gzip", compression_opts=_GZIP_LEVEL)


def _write_environmental(f: h5py.File, env: EnvironmentalSeries) -> None:
    grp = f.create_group("environmental")
    grp.create_dataset("timestamps", data=env.timestamps)
    grp.create_dataset("temperature", data=env.temperature)
    grp.create_dataset("humidity", data=env.humidity)


def _write_events(f: h5py.File, events: EventSeries) -> None:
    grp = f.create_group("events")
    grp.create_dataset("timestamps", data=events.timestamps)
    dt = h5py.special_dtype(vlen=str)
    grp.create_dataset("type", data=events.event_type.astype(object), dtype=dt)


class CanonicalStore:
    """Acceso de solo lectura, perezoso por bloques, al archivo canónico escrito por
    :func:`write_canonical`. Nunca carga ``data`` completo en RAM salvo pedido explícito.
    """

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._file: h5py.File | None = None

    def __enter__(self) -> "CanonicalStore":
        self._file = h5py.File(self._path, mode="r")
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    @property
    def _f(self) -> h5py.File:
        if self._file is None:
            raise RuntimeError("CanonicalStore debe usarse como context manager (with CanonicalStore(...) as s:)")
        return self._file

    @property
    def dataset_id(self) -> str:
        return str(self._f.attrs["dataset_id"])

    @property
    def experiment(self) -> str:
        return str(self._f.attrs["experiment"])

    @property
    def normalization_version(self) -> str:
        return str(self._f.attrs["normalization_version"])

    def available_sensors(self) -> list[SensorName]:
        return [name for name in _ALL_SENSORS if name in self._f]

    def n_signals(self, sensor: SensorName) -> int:
        return int(self._f[sensor]["timestamps"].shape[0])

    def get_signal_row(self, sensor: SensorName, index: int) -> tuple[np.ndarray, float, float, float]:
        """Traza cruda de una sola señal por índice cronológico global (acceso O(1))."""
        grp = self._f[sensor]
        return (
            grp["data"][index, :],
            float(grp["timestamps"][index]),
            float(grp["trigger"][index]),
            float(grp["vrange"][index]),
        )

    def get_block(self, sensor: SensorName, start: int, stop: int) -> SignalBlock:
        """Bloque contiguo ``[start, stop)`` sin cargar el resto del dataset."""
        grp = self._f[sensor]
        return SignalBlock(
            data=grp["data"][start:stop, :],
            timestamps=grp["timestamps"][start:stop],
            trigger=grp["trigger"][start:stop],
            vrange=grp["vrange"][start:stop],
            valid_mask=grp["valid_mask"][start:stop].astype(bool),
            minmax=grp["minmax"][start:stop, :],
        )

    def iter_blocks(self, sensor: SensorName, block_n_signals: int) -> Iterator[SignalBlock]:
        """Recorre el sensor completo en bloques de tamaño acotado (carga perezosa,
        §9.1 del PROMPT maestro: "la matriz global no se carga entera en RAM")."""
        n = self.n_signals(sensor)
        for start in range(0, n, block_n_signals):
            yield self.get_block(sensor, start, min(start + block_n_signals, n))

    def get_all_timestamps(self, sensor: SensorName) -> np.ndarray:
        """Vector completo de timestamps (liviano: 8 bytes/señal, necesario para
        navegación/agrupamiento) — no confundir con cargar ``data`` completo."""
        return self._f[sensor]["timestamps"][:]

    def get_all_minmax(self, sensor: SensorName) -> np.ndarray:
        """Min/max completo por señal (liviano, alimenta la gráfica tipo #1)."""
        return self._f[sensor]["minmax"][:]

    def get_valid_mask(self, sensor: SensorName) -> np.ndarray:
        return self._f[sensor]["valid_mask"][:].astype(bool)

    def get_environmental(self) -> EnvironmentalSeries:
        grp = self._f["environmental"]
        return EnvironmentalSeries(
            timestamps=grp["timestamps"][:], temperature=grp["temperature"][:], humidity=grp["humidity"][:]
        )

    def get_events(self) -> EventSeries:
        grp = self._f["events"]
        # .asstr() decodifica el vlen-utf8 a str de Python; sin esto h5py 3.x retorna bytes.
        types = grp["type"].asstr()[:] if grp["type"].shape[0] > 0 else np.array([], dtype="<U16")
        return EventSeries(timestamps=grp["timestamps"][:], event_type=types)
