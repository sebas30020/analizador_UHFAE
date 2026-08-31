"""Exportación del dataset filtrado a un archivo HDF5 independiente (Fase 6, filtrado
cruzado): parte las señales de cada sensor en dos grupos según la máscara de filtrado
activa -- ``resultantes`` (activas) y ``filtradas`` (excluidas) -- y conserva todo lo
demás (ambientales, eventos, perfil efectivo por sensor) para que el archivo se pueda
releer con ``data/readers/factory.py::open_reader`` como un origen más
(``data/readers/filtered_export_reader.py``), igual que cualquier otro formato.

Capa de datos pura: sin Dash, sin caché, sin lock de ``AppState``. ``ui/state.py::
AppState.export_snapshot`` copia bajo lock lo mínimo necesario y el llamador (``ui/
callbacks/sensor_window_callbacks.py``) invoca esta función en un hilo aparte -- misma
separación de responsabilidades que ``data/storage.py::write_canonical``.

El caché **nunca** interviene aquí (docs/ARQUITECTURA.md §5: "el caché almacena
resultados sobre el conjunto completo de señales, nunca un subconjunto filtrado") --
esta exportación no lee ni escribe caché, solo particiona arrays ya en memoria.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import h5py
import numpy as np

from core.models import EnvironmentalSeries, EventSeries, SensorConfig, SensorName, SignalBlock
from utils.profiling import stage

SCHEMA_VERSION = 2
_GZIP_LEVEL = 4

# Firma de reconocimiento por contenido (mismo criterio que los otros dos formatos de
# origen, ``data/readers/factory.py``): un atributo raíz, no la extensión del archivo.
FILE_TYPE_ATTR = "AnalizadorUHFAE/FilteredExport"

RESULTANTES_GROUP = "resultantes"
FILTRADAS_GROUP = "filtradas"

# Partición a cargar al reabrir un archivo exportado con esta forma
# (``data/readers/filtered_export_reader.py``): solo las señales activas al momento de
# exportar, solo las excluidas, o ambas recombinadas (conjunto original completo).
ExportPartition = Literal["resultantes", "filtradas", "ambas"]


def chunk_rows(n_signals: int, n_samples: int, target_block_bytes: int = 8 * 1024 * 1024) -> int:
    if n_signals == 0 or n_samples == 0:
        return 1
    bytes_per_row = n_samples * 4
    return max(1, min(n_signals, target_block_bytes // bytes_per_row))


# Alias de retrocompatibilidad
_chunk_rows = chunk_rows


def write_sensor_group_attrs(grp: h5py.Group, config: SensorConfig) -> None:
    """Escribe los atributos de configuración de sensor en el grupo HDF5 correspondiente."""
    grp.attrs["fs_hz"] = config.fs_hz
    grp.attrs["n_samples"] = config.n_samples
    grp.attrs["freq_limit_hz"] = config.freq_limit_hz
    grp.attrs["axis_unit"] = config.axis_unit
    grp.attrs["axis_scale"] = config.axis_scale
    grp.attrs["target_block_bytes"] = config.target_block_bytes
    grp.attrs["decimate_full_view"] = config.decimate_full_view
    grp.attrs["has_trigger_metadata"] = config.has_trigger_metadata


def _write_partition(
    f: h5py.File,
    group_name: str,
    sensor: SensorName,
    block: SignalBlock,
    config: SensorConfig,
    indices: np.ndarray,
    block_n_signals: int,
) -> None:
    """Escribe la partición de ``sensor`` seleccionada por ``indices`` (índices
    cronológicos globales, ascendentes) bajo ``/<group_name>/<sensor>/``, en bloques de
    ``block_n_signals`` filas -- nunca materializa la matriz filtrada completa en RAM
    (requisito de rendimiento: ``UHF_KS`` completo son ~1,6 GB, filtrar de un tirón con
    fancy-indexing duplicaría esa memoria de golpe).

    Guarda ``source_index`` (Fase 6): el índice cronológico global original de cada
    señal en el dataset de origen, para no perder trazabilidad al partir el conjunto.
    """
    n = indices.shape[0]
    m = block.data.shape[1] if block.data.shape[0] > 0 else config.n_samples
    grp = f.create_group(f"{group_name}/{sensor}")
    write_sensor_group_attrs(grp, config)

    row_chunk = chunk_rows(n, m)
    data_ds = grp.create_dataset(
        "data", shape=(n, m), dtype=np.float32,
        chunks=(row_chunk, m) if n > 0 else None,
        compression="gzip", compression_opts=_GZIP_LEVEL,
    )
    for start in range(0, n, block_n_signals):
        stop = min(start + block_n_signals, n)
        rows = indices[start:stop]
        data_ds[start:stop, :] = block.data[rows, :]

    grp.create_dataset("timestamps", data=block.timestamps[indices])
    grp.create_dataset("trigger", data=block.trigger[indices])
    grp.create_dataset("vrange", data=block.vrange[indices])
    grp.create_dataset("valid_mask", data=block.valid_mask[indices].astype(np.uint8))
    grp.create_dataset("minmax", data=block.minmax[indices], compression="gzip", compression_opts=_GZIP_LEVEL)
    grp.create_dataset("source_index", data=indices.astype(np.int64))


def write_environmental(f: h5py.File, env: EnvironmentalSeries) -> None:
    grp = f.create_group("environmental")
    grp.create_dataset("timestamps", data=env.timestamps)
    grp.create_dataset("temperature", data=env.temperature)
    grp.create_dataset("humidity", data=env.humidity)


_write_environmental = write_environmental


def write_events(f: h5py.File, events: EventSeries) -> None:
    grp = f.create_group("events")
    grp.create_dataset("timestamps", data=events.timestamps)
    dt = h5py.special_dtype(vlen=str)
    grp.create_dataset("type", data=events.event_type.astype(object), dtype=dt)


_write_events = write_events


def export_filtered(
    path: str | Path,
    dataset_id: str,
    experiment: str,
    normalization_version: str,
    blocks: dict[SensorName, SignalBlock],
    sensor_configs: dict[SensorName, SensorConfig],
    active_masks: dict[SensorName, np.ndarray],
    environmental: EnvironmentalSeries,
    events: EventSeries,
) -> tuple[int, int]:
    """Escribe ``path`` con las dos particiones de cada sensor de ``blocks``, según
    ``active_masks`` (``True`` = resultante/activa). Un sensor sin máscara en
    ``active_masks`` se trata como completamente activo (ninguna señal filtrada).

    Conserva ambientales, eventos y el perfil efectivo (``sensor_configs``, ya resuelto
    con los overrides del origen -- docs/ARQUITECTURA.md §8.3) de cada sensor, para que
    el archivo resultante sea releíble con
    ``data.readers.filtered_export_reader.FilteredExportReader`` a través de
    ``data.readers.factory.open_reader``, sin depender del dataset original.

    Retorna ``(n_resultantes, n_filtradas)`` -- total de señales en cada partición,
    sumado sobre todos los sensores.
    """
    path = Path(path)
    with stage("export.filtrado", n_sensores=len(blocks)) as ctx:
        with h5py.File(path, mode="w") as f:
            f.attrs["file_type"] = FILE_TYPE_ATTR
            f.attrs["schema_version"] = SCHEMA_VERSION
            f.attrs["source_dataset_id"] = dataset_id
            f.attrs["experiment"] = experiment
            f.attrs["normalization_version"] = normalization_version
            f.attrs["exported_at"] = datetime.now(timezone.utc).isoformat()

            n_resultantes = 0
            n_filtradas = 0
            for sensor, block in blocks.items():
                config = sensor_configs[sensor]
                mask = active_masks.get(sensor)
                if mask is None:
                    mask = np.ones(block.data.shape[0], dtype=bool)
                keep = np.where(mask)[0]
                drop = np.where(~mask)[0]
                n_resultantes += keep.shape[0]
                n_filtradas += drop.shape[0]
                _write_partition(f, RESULTANTES_GROUP, sensor, block, config, keep, config.block_n_signals)
                _write_partition(f, FILTRADAS_GROUP, sensor, block, config, drop, config.block_n_signals)

            _write_environmental(f, environmental)
            _write_events(f, events)
            ctx["n_resultantes"] = n_resultantes
            ctx["n_filtradas"] = n_filtradas

    return n_resultantes, n_filtradas
