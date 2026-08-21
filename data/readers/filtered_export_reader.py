"""Reader del archivo escrito por ``data/export.py``: permite reabrir el resultado de
"Exportar datos filtrados…" como un origen más, sin que nada aguas arriba de la capa de
datos sepa que viene de un export en vez de un origen real (docs/ARQUITECTURA.md §8.3,
prueba de fuego #3: "cambiar el motor de origen no toca nada aguas arriba de la capa de
datos").

Tres particiones seleccionables al abrir (``ExportPartition``, ``data/export.py``):

- ``"resultantes"`` (por defecto): solo las señales que sobrevivieron al filtro activo
  en el momento de exportar.
- ``"filtradas"``: solo las excluidas.
- ``"ambas"``: las dos, recombinadas -- ``data/ingest.py::ingest_sensor`` las reordena
  por timestamp de todos modos, así que el resultado es indistinguible del conjunto
  original sin filtrar.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import h5py
import numpy as np

from core.models import EnvironmentalSeries, EventSeries, SensorName
from data.export import FILE_TYPE_ATTR, FILTRADAS_GROUP, RESULTANTES_GROUP, ExportPartition
from data.readers.base import OriginReader, RawSignalBatch, stable_file_dataset_id

_ALL_SENSORS: tuple[SensorName, ...] = ("UHF", "AE", "UHF_KS")


def _decode(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def is_filtered_export_file(f: h5py.File) -> bool:
    """Sniffing por contenido (mismo criterio que los otros dos formatos,
    ``data/readers/factory.py``): el atributo raíz ``file_type`` es la firma, no la
    extensión del archivo."""
    return _decode(f.attrs.get("file_type", "")) == FILE_TYPE_ATTR


class FilteredExportReader(OriginReader):
    """Lee un archivo escrito por :func:`data.export.export_filtered`."""

    def __init__(self, path: str | Path, partition: ExportPartition = "resultantes"):
        self._path = Path(path)
        self._partition = partition
        self._file: h5py.File | None = None

    def __enter__(self) -> "FilteredExportReader":
        self._file = h5py.File(self._path, mode="r")
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    @property
    def _f(self) -> h5py.File:
        if self._file is None:
            raise RuntimeError(
                "FilteredExportReader debe usarse como context manager (with FilteredExportReader(...) as r:)"
            )
        return self._file

    @property
    def dataset_id(self) -> str:
        # Sufijo de partición (docs/ARQUITECTURA.md §7): dos particiones del mismo
        # archivo son conjuntos de señales distintos y no pueden compartir clave de
        # caché, aunque ``ruta:tamaño:mtime_ns`` sea idéntico para ambas.
        return f"{stable_file_dataset_id(self._path)}:particion={self._partition}"

    def list_experiments(self) -> list[str]:
        return [_decode(self._f.attrs["experiment"])]

    def get_experiment_attrs(self, experiment: str) -> dict:
        return {
            "source_dataset_id": _decode(self._f.attrs["source_dataset_id"]),
            "normalization_version": _decode(self._f.attrs["normalization_version"]),
            "exported_at": _decode(self._f.attrs["exported_at"]),
            "partition": self._partition,
        }

    def available_sensors(self) -> list[SensorName]:
        # Cada sensor ingerido en el momento de exportar tiene grupo en AMBAS
        # particiones, aunque una de las dos haya quedado vacía (todo activo o todo
        # filtrado) -- "resultantes" siempre alcanza para enumerar los sensores
        # disponibles, sea cual sea la partición efectivamente seleccionada.
        if RESULTANTES_GROUP not in self._f:
            return []
        return [s for s in _ALL_SENSORS if s in self._f[RESULTANTES_GROUP]]

    def sensor_config_overrides(self, experiment: str, sensor: SensorName) -> dict:
        """Perfil efectivo guardado al exportar (ya resuelto con los overrides del
        origen original, docs/ARQUITECTURA.md §8.3) -- idéntico en ambas particiones."""
        attrs = self._f[RESULTANTES_GROUP][sensor].attrs
        return {
            "fs_hz": float(attrs["fs_hz"]),
            "n_samples": int(attrs["n_samples"]),
            "freq_limit_hz": float(attrs["freq_limit_hz"]),
            "axis_unit": _decode(attrs["axis_unit"]),
            "axis_scale": float(attrs["axis_scale"]),
            "target_block_bytes": int(attrs["target_block_bytes"]),
            "decimate_full_view": bool(attrs["decimate_full_view"]),
            "has_trigger_metadata": bool(attrs["has_trigger_metadata"]),
        }

    def _partition_groups(self) -> list[str]:
        if self._partition == "ambas":
            return [RESULTANTES_GROUP, FILTRADAS_GROUP]
        return [RESULTANTES_GROUP if self._partition == "resultantes" else FILTRADAS_GROUP]

    def iter_signal_batches(self, experiment: str, sensor: SensorName) -> Iterator[RawSignalBatch]:
        for group_name in self._partition_groups():
            root = self._f.get(group_name)
            if root is None or sensor not in root:
                continue
            grp = root[sensor]
            data = grp["data"]
            if data.shape[0] == 0:
                continue
            yield RawSignalBatch(
                data=data[:],
                timestamps=grp["timestamps"][:],
                trigger=grp["trigger"][:],
                vrange=grp["vrange"][:],
            )

    def get_environmental(self, experiment: str) -> EnvironmentalSeries:
        grp = self._f["environmental"]
        return EnvironmentalSeries(
            timestamps=grp["timestamps"][:], temperature=grp["temperature"][:], humidity=grp["humidity"][:]
        )

    def get_events(self, experiment: str) -> EventSeries:
        grp = self._f["events"]
        # .asstr() decodifica el vlen-utf8 a str de Python; sin esto h5py 3.x retorna bytes.
        types = grp["type"].asstr()[:] if grp["type"].shape[0] > 0 else np.array([], dtype="<U16")
        return EventSeries(timestamps=grp["timestamps"][:], event_type=types)
