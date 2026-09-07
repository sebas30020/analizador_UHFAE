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

import threading
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

    n = block.n_signals
    m = block.n_samples if n > 0 else config.n_samples
    row_chunk = _chunk_rows(n, m)

    data_ds = grp.create_dataset(
        "data", shape=(n, m), dtype=np.float32,
        chunks=(row_chunk, m) if n > 0 else None,
        compression="gzip", compression_opts=_GZIP_LEVEL,
    )
    block_n = config.block_n_signals
    for start in range(0, n, block_n):
        stop = min(start + block_n, n)
        data_ds[start:stop, :] = block.rows(start, stop)

    grp.create_dataset("timestamps", data=block.timestamps)
    grp.create_dataset("trigger", data=block.trigger)
    grp.create_dataset("vrange", data=block.vrange)
    grp.create_dataset("valid_mask", data=block.valid_mask.astype(np.uint8))
    grp.create_dataset("minmax", data=block.minmax, compression="gzip", compression_opts=_GZIP_LEVEL)
    grp.attrs["is_chronological"] = True


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
    """Acceso de solo lectura, perezoso por bloques y seguro ante multihilo,
    al archivo canónico escrito por :func:`write_canonical` o ingesta en streaming.
    Nunca carga ``data`` completo en RAM salvo pedido explícito.
    """

    def __init__(self, path: str | Path, *, auto_open: bool = False):
        self._path = Path(path)
        self._file: h5py.File | None = None
        self._lock = threading.RLock()
        self._auto_open = auto_open

    def __enter__(self) -> "CanonicalStore":
        with self._lock:
            if self._file is None:
                self._file = h5py.File(self._path, mode="r")
        return self

    def __exit__(self, *exc_info: object) -> None:
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None

    def close(self) -> None:
        """Cierra el archivo HDF5 subyacente si está abierto."""
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None

    @property
    def _f(self) -> h5py.File:
        with self._lock:
            if self._file is None:
                if not self._auto_open:
                    raise RuntimeError("CanonicalStore debe usarse como context manager (with CanonicalStore(...) as s:)")
                self._file = h5py.File(self._path, mode="r")
            return self._file

    @property
    def dataset_id(self) -> str:
        with self._lock:
            return str(self._f.attrs["dataset_id"])

    @property
    def experiment(self) -> str:
        with self._lock:
            return str(self._f.attrs["experiment"])

    @property
    def normalization_version(self) -> str:
        with self._lock:
            return str(self._f.attrs["normalization_version"])

    def available_sensors(self) -> list[SensorName]:
        with self._lock:
            return [name for name in _ALL_SENSORS if name in self._f]

    def n_signals(self, sensor: SensorName) -> int:
        with self._lock:
            return int(self._f[sensor]["timestamps"].shape[0])

    def get_signal_row(self, sensor: SensorName, index: int) -> tuple[np.ndarray, float, float, float]:
        """Traza cruda de una sola señal por índice cronológico global (acceso O(1))."""
        with self._lock:
            grp = self._f[sensor]
            phys_idx = int(grp["order"][index]) if "order" in grp else index
            return (
                grp["data"][phys_idx, :],
                float(grp["timestamps"][index]),
                float(grp["trigger"][index]),
                float(grp["vrange"][index]),
            )

    def get_data_rows(self, sensor: SensorName, start: int, stop: int) -> np.ndarray:
        """Lee las filas cronológicas [start, stop) de la matriz de datos."""
        with self._lock:
            grp = self._f[sensor]
            data_ds = grp["data"]
            n_tot = int(grp["timestamps"].shape[0])
            if start >= stop or start >= n_tot:
                m = int(data_ds.shape[1]) if data_ds.ndim == 2 else 0
                return np.empty((0, m), dtype=np.float32)

            start = max(0, start)
            stop = min(n_tot, stop)

            if "order" not in grp or bool(grp.attrs.get("is_chronological", False)):
                return data_ds[start:stop, :]

            order_ds = grp["order"]
            phys_indices = order_ds[start:stop]
            if phys_indices.size == 0:
                m = int(data_ds.shape[1]) if data_ds.ndim == 2 else 0
                return np.empty((0, m), dtype=np.float32)

            if len(phys_indices) == 1 or bool(np.all(np.diff(phys_indices) > 0)):
                return data_ds[phys_indices, :]

            sort_perm = np.argsort(phys_indices)
            increasing_indices = phys_indices[sort_perm]
            sorted_chunk = data_ds[increasing_indices, :]
            inv_perm = np.empty_like(sort_perm)
            inv_perm[sort_perm] = np.arange(len(sort_perm))
            return sorted_chunk[inv_perm, :]

    def get_data_indices(self, sensor: SensorName, indices: np.ndarray | list[int]) -> np.ndarray:
        """Lee una lista o vector arbitrario de índices cronológicos."""
        idx_arr = np.asarray(indices, dtype=np.int64)
        with self._lock:
            grp = self._f[sensor]
            data_ds = grp["data"]
            m = int(data_ds.shape[1]) if data_ds.ndim == 2 else 0
            if idx_arr.size == 0:
                return np.empty((0, m), dtype=np.float32)

            if "order" in grp and not bool(grp.attrs.get("is_chronological", False)):
                order_ds = grp["order"]
                if len(idx_arr) <= 1 or bool(np.all(np.diff(idx_arr) > 0)):
                    phys_indices = order_ds[idx_arr]
                else:
                    idx_sort = np.argsort(idx_arr)
                    idx_sorted = idx_arr[idx_sort]
                    idx_uniq, idx_rev = np.unique(idx_sorted, return_inverse=True)
                    raw_order = order_ds[idx_uniq][idx_rev]
                    inv_idx = np.empty_like(idx_sort)
                    inv_idx[idx_sort] = np.arange(len(idx_sort))
                    phys_indices = raw_order[inv_idx]
            else:
                phys_indices = idx_arr

            if len(phys_indices) == 1 or bool(np.all(np.diff(phys_indices) > 0)):
                return data_ds[phys_indices, :]

            sort_perm = np.argsort(phys_indices)
            increasing_indices = phys_indices[sort_perm]
            unique_indices, rev_map = np.unique(increasing_indices, return_inverse=True)
            chunk = data_ds[unique_indices, :]
            sorted_chunk = chunk[rev_map]
            inv_perm = np.empty_like(sort_perm)
            inv_perm[sort_perm] = np.arange(len(sort_perm))
            return sorted_chunk[inv_perm, :]

    def create_lazy_block(self, sensor: SensorName) -> SignalBlock:
        """Crea un SignalBlock perezoso respaldado en este CanonicalStore.

        Carga únicamente los vectores (N,) en RAM y delega la lectura de trazas
        al archivo HDF5 vía callbacks sincronizados.
        """
        with self._lock:
            self._auto_open = True
            grp = self._f[sensor]
            ts = grp["timestamps"][:]
            trig = grp["trigger"][:]
            vr = grp["vrange"][:]
            vm = grp["valid_mask"][:].astype(bool)
            mm = grp["minmax"][:]
            m = int(grp["data"].shape[1]) if grp["data"].ndim == 2 else 0

        return SignalBlock(
            data=None,
            timestamps=ts,
            trigger=trig,
            vrange=vr,
            valid_mask=vm,
            minmax=mm,
            row_source=lambda start, stop: self.get_data_rows(sensor, start, stop),
            single_row_source=lambda idx: self.get_signal_row(sensor, idx)[0],
            indices_source=lambda idxs: self.get_data_indices(sensor, idxs),
            n_samples=m,
        )

    def get_block(self, sensor: SensorName, start: int, stop: int) -> SignalBlock:
        """Bloque contiguo ``[start, stop)`` sin cargar el resto del dataset."""
        with self._lock:
            grp = self._f[sensor]
            raw_data = self.get_data_rows(sensor, start, stop)
            return SignalBlock(
                data=raw_data,
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
        with self._lock:
            return self._f[sensor]["timestamps"][:]

    def get_all_minmax(self, sensor: SensorName) -> np.ndarray:
        """Min/max completo por señal (liviano, alimenta la gráfica tipo #1)."""
        with self._lock:
            return self._f[sensor]["minmax"][:]

    def get_valid_mask(self, sensor: SensorName) -> np.ndarray:
        with self._lock:
            return self._f[sensor]["valid_mask"][:].astype(bool)

    def get_environmental(self) -> EnvironmentalSeries:
        with self._lock:
            grp = self._f["environmental"]
            return EnvironmentalSeries(
                timestamps=grp["timestamps"][:], temperature=grp["temperature"][:], humidity=grp["humidity"][:]
            )

    def get_events(self) -> EventSeries:
        with self._lock:
            grp = self._f["events"]
            # .asstr() decodifica el vlen-utf8 a str de Python; sin esto h5py 3.x retorna bytes.
            types = grp["type"].asstr()[:] if grp["type"].shape[0] > 0 else np.array([], dtype="<U16")
            return EventSeries(timestamps=grp["timestamps"][:], event_type=types)
