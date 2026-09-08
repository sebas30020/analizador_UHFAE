"""Modelo de datos canónico e independiente del origen (FASE0_DISENO_Analizador_UHF_AE.md §5).

Ninguna dimensión temporal está cableada aquí: fs, M y freq_limit se leen de
``config/sensors.yaml`` a través de :func:`load_sensor_configs`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

import numpy as np
import yaml

SensorName = Literal["UHF", "AE", "UHF_KS"]


@dataclass(frozen=True)
class SensorConfig:
    """Perfil de configuración de un sensor, leído de ``config/sensors.yaml``.

    ``decimate_full_view``/``full_resolution_span``/``has_trigger_metadata`` son
    propiedades del origen físico, no literales de presentación: qué sensor diezma en
    vista completa (gráfica #2), bajo qué ventana temporal (en su unidad natural
    ``axis_unit``) se restaura la resolución completa y cuál trae nivel de disparo por
    señal varía según el instrumento, así que viven aquí en vez de estar cableadas por
    nombre de sensor en ``ui/``.
    """

    name: SensorName
    hdf5_group: str
    fs_hz: float
    n_samples: int
    freq_limit_hz: float
    axis_unit: str
    axis_scale: float
    target_block_bytes: int
    decimate_full_view: bool = False
    has_trigger_metadata: bool = True
    full_resolution_span: float | None = None

    @property
    def duration_s(self) -> float:
        """Duración de una traza completa, en segundos (``M / fs``)."""
        return self.n_samples / self.fs_hz

    @property
    def dt_s(self) -> float:
        """Paso de muestreo intra-señal, en segundos (``1 / fs``)."""
        return 1.0 / self.fs_hz

    @property
    def freq_resolution_hz(self) -> float:
        """Resolución espectral ``Δf = fs / M`` de una FFT de la traza completa."""
        return self.fs_hz / self.n_samples

    @property
    def bytes_per_signal(self) -> int:
        """Peso en bytes de una traza cruda en ``float32``."""
        return self.n_samples * 4

    @property
    def block_n_signals(self) -> int:
        """Tamaño de bloque de lote (nº de señales) para el presupuesto de E/S configurado."""
        return max(1, self.target_block_bytes // self.bytes_per_signal)

    def time_axis(self) -> np.ndarray:
        """Eje de tiempos intra-señal, reconstruido bajo demanda (``t0=0``, paso ``dt_s``).

        No se almacena por señal (§5.1 de FASE0_DISENO...md): es constante por sensor.
        """
        return np.arange(self.n_samples, dtype=np.float64) * self.dt_s


@dataclass(frozen=True)
class NormalizationInfo:
    """Identifica la regla de normalización usada; participa en la clave de caché.

    Ver AUDITORIA_FORMULAS_PDF_vs_metricas.md §1: se implementa únicamente la división
    por escala vertical (``x_norm = x_raw / vrange``). No se aplica corrección de offset
    de línea base (Ecuación 4 del PDF de referencia) — decisión explícita del usuario
    (Opción C), documentada como desviación consciente frente al informe de referencia.
    """

    version: str


RowSource = Callable[[int, int], np.ndarray]
SingleRowSource = Callable[[int], np.ndarray]
IndicesSource = Callable[[np.ndarray], np.ndarray]


class LazyDataProxy:
    """Proxy liviano para compatibilidad de inspección sobre SignalBlock perezoso.

    Permite acceder a .shape, len(), .ndim, .dtype e indexación básica
    sin materializar la matriz completa en RAM.
    """

    def __init__(self, block: "SignalBlock") -> None:
        self._block = block

    @property
    def shape(self) -> tuple[int, int]:
        return (self._block.n_signals, self._block.n_samples)

    @property
    def ndim(self) -> int:
        return 2

    @property
    def dtype(self) -> np.dtype:
        return np.dtype(np.float32)

    def __len__(self) -> int:
        return self._block.n_signals

    def __array__(self, dtype: Any = None) -> np.ndarray:
        arr = self._block.rows(0, self._block.n_signals)
        if dtype is not None:
            return arr.astype(dtype, copy=False)
        return arr

    def __getitem__(self, item: Any) -> np.ndarray:
        if isinstance(item, (int, np.integer)):
            return self._block.row(int(item))
        if isinstance(item, slice):
            start = 0 if item.start is None else int(item.start)
            stop = self._block.n_signals if item.stop is None else int(item.stop)
            if item.step is not None and item.step != 1:
                indices = np.arange(start, stop, item.step)
                return self._block.rows_by_indices(indices)
            return self._block.rows(start, stop)
        if isinstance(item, tuple):
            row_item = item[0]
            col_item = item[1] if len(item) > 1 else slice(None)
            if isinstance(row_item, (int, np.integer)):
                row_arr = self._block.row(int(row_item))
                return row_arr[col_item]
            if isinstance(row_item, slice):
                start = 0 if row_item.start is None else int(row_item.start)
                stop = self._block.n_signals if row_item.stop is None else int(row_item.stop)
                if row_item.step is not None and row_item.step != 1:
                    indices = np.arange(start, stop, row_item.step)
                    rows_arr = self._block.rows_by_indices(indices)
                else:
                    rows_arr = self._block.rows(start, stop)
                return rows_arr[:, col_item] if rows_arr.ndim == 2 else rows_arr[col_item]
            if isinstance(row_item, (np.ndarray, list)):
                rows_arr = self._block.rows_by_indices(np.asarray(row_item))
                return rows_arr[:, col_item] if rows_arr.ndim == 2 else rows_arr[col_item]
        if isinstance(item, (np.ndarray, list)):
            arr_key = np.asarray(item)
            if arr_key.dtype == bool:
                indices = np.where(arr_key)[0]
                return self._block.rows_by_indices(indices)
            return self._block.rows_by_indices(arr_key)
        raise TypeError(f"Indexación no soportada en LazyDataProxy: {type(item)}")


class SignalBlock:
    """Bloque contiguo de señales de un sensor, ya en orden cronológico.

    Soporta modalidad perezosa (fuera de núcleo) y modalidad residente en RAM
    (para tests unitarios y operaciones en memoria).
    """

    def __init__(
        self,
        data: np.ndarray | None = None,
        timestamps: np.ndarray | None = None,
        trigger: np.ndarray | None = None,
        vrange: np.ndarray | None = None,
        valid_mask: np.ndarray | None = None,
        minmax: np.ndarray | None = None,
        *,
        row_source: RowSource | None = None,
        single_row_source: SingleRowSource | None = None,
        indices_source: IndicesSource | None = None,
        n_samples: int | None = None,
        order: np.ndarray | None = None,
    ) -> None:
        if (
            timestamps is None
            or trigger is None
            or vrange is None
            or valid_mask is None
            or minmax is None
        ):
            raise ValueError(
                "timestamps, trigger, vrange, valid_mask y minmax son obligatorios en SignalBlock."
            )

        self.timestamps: np.ndarray = np.asarray(timestamps, dtype=np.float64)
        self.trigger: np.ndarray = np.asarray(trigger, dtype=np.float64)
        self.vrange: np.ndarray = np.asarray(vrange, dtype=np.float64)
        self.valid_mask: np.ndarray = np.asarray(valid_mask, dtype=bool)
        self.minmax: np.ndarray = np.asarray(minmax, dtype=np.float32)

        n = self.timestamps.shape[0]
        for name, arr in (("trigger", self.trigger), ("vrange", self.vrange), ("valid_mask", self.valid_mask)):
            if arr.shape[0] != n:
                raise ValueError(f"SignalBlock.{name} tiene {arr.shape[0]} filas, esperaba {n}")
        if self.minmax.shape != (n, 2):
            raise ValueError(f"SignalBlock.minmax debe tener forma ({n}, 2), tiene {self.minmax.shape}")

        self._data: np.ndarray | None = None
        if data is not None:
            data_arr = np.asarray(data, dtype=np.float32)
            if data_arr.shape[0] != n:
                raise ValueError(f"SignalBlock.data tiene {data_arr.shape[0]} filas, esperaba {n}")
            self._data = data_arr
            self._n_samples = int(data_arr.shape[1]) if n > 0 else (n_samples or 0)
        else:
            self._n_samples = n_samples if n_samples is not None else 0

        self._row_source = row_source
        self._single_row_source = single_row_source
        self._indices_source = indices_source
        self._order = np.asarray(order, dtype=np.int64) if order is not None else None

    @property
    def n_signals(self) -> int:
        return int(self.timestamps.shape[0])

    @property
    def n_samples(self) -> int:
        return int(self._n_samples)

    def __len__(self) -> int:
        return self.n_signals

    @property
    def data(self) -> Any:
        if self._data is not None and self._order is None:
            return self._data
        return LazyDataProxy(self)

    @data.setter
    def data(self, value: np.ndarray) -> None:
        self._data = np.asarray(value, dtype=np.float32)
        self._n_samples = self._data.shape[1] if self._data.shape[0] > 0 else self._n_samples
        self._order = None

    def rows(self, start: int, stop: int) -> np.ndarray:
        """Retorna las filas [start, stop) en orden cronológico (float32, 2D)."""
        if start >= stop or start >= self.n_signals:
            return np.empty((0, self.n_samples), dtype=np.float32)
        start = max(0, start)
        stop = min(self.n_signals, stop)
        if self._data is not None:
            if self._order is None:
                return self._data[start:stop]
            return self._data[self._order[start:stop]]
        if self._row_source is not None:
            return self._row_source(start, stop)
        raise RuntimeError("SignalBlock no posee matriz en memoria ni row_source configurado.")

    def row(self, index: int) -> np.ndarray:
        """Retorna la traza individual en el índice cronológico global (float32, 1D)."""
        if index < 0 or index >= self.n_signals:
            raise IndexError(f"Índice {index} fuera de rango [0, {self.n_signals})")
        if self._data is not None:
            if self._order is None:
                return self._data[index]
            return self._data[self._order[index]]
        if self._single_row_source is not None:
            return self._single_row_source(index)
        if self._row_source is not None:
            return self._row_source(index, index + 1)[0]
        raise RuntimeError("SignalBlock no posee matriz en memoria ni single_row_source configurado.")

    def rows_by_indices(self, indices: np.ndarray | list[int]) -> np.ndarray:
        """Retorna las filas correspondientes a una secuencia de índices cronológicos (float32, 2D)."""
        idx_arr = np.asarray(indices, dtype=np.int64)
        if idx_arr.size == 0:
            return np.empty((0, self.n_samples), dtype=np.float32)
        if self._data is not None:
            if self._order is None:
                return self._data[idx_arr]
            return self._data[self._order[idx_arr]]
        if self._indices_source is not None:
            return self._indices_source(idx_arr)
        return np.stack([self.row(int(i)) for i in idx_arr], axis=0)

    def __repr__(self) -> str:
        mode = "resident" if self._data is not None else "lazy"
        return f"SignalBlock(n_signals={self.n_signals}, n_samples={self.n_samples}, mode='{mode}')"


@dataclass
class EnvironmentalSeries:
    """Serie ambiental (temperatura, humedad) con su propia cadencia de muestreo."""

    timestamps: np.ndarray   # (k,) float64
    temperature: np.ndarray  # (k,) float64
    humidity: np.ndarray     # (k,) float64


@dataclass
class IngestResult:
    """Resultado completo de ingerir un experimento: matrices globales por sensor
    (ya desempaquetadas de sus chunks, ordenadas cronológicamente), series auxiliares
    y metadatos de trazabilidad para la clave de caché (§7)."""

    dataset_id: str
    experiment: str
    sensors: dict[SensorName, "SignalBlock"]
    environmental: "EnvironmentalSeries"
    events: "EventSeries"
    normalization: NormalizationInfo


@dataclass
class EventSeries:
    """Marcas de evento. ``event_type`` se preserva como metadato opcional (ver §0.2
    de FASE0_DISENO...md): el PROMPT maestro asume timestamp puro, pero los datos
    reales traen un tipo (SHOT/PA/FO). Tratamiento genérico: todas las marcas se
    renderizan igual, sin diferenciar por tipo.
    """

    timestamps: np.ndarray             # (k,) float64
    event_type: np.ndarray             # (k,) <U... (string), puede estar vacío


def load_sensor_configs(path: str | Path) -> dict[SensorName, SensorConfig]:
    """Carga el perfil de sensores desde un YAML con la forma de ``config/sensors.yaml``."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    configs: dict[SensorName, SensorConfig] = {}
    for sensor_name, cfg in raw["sensors"].items():
        configs[sensor_name] = SensorConfig(
            name=sensor_name,
            hdf5_group=cfg["hdf5_group"],
            fs_hz=float(cfg["fs_hz"]),
            n_samples=int(cfg["n_samples"]),
            freq_limit_hz=float(cfg["freq_limit_hz"]),
            axis_unit=cfg["axis_unit"],
            axis_scale=float(cfg["axis_scale"]),
            target_block_bytes=int(cfg["target_block_bytes"]),
            decimate_full_view=bool(cfg.get("decimate_full_view", False)),
            has_trigger_metadata=bool(cfg.get("has_trigger_metadata", True)),
            full_resolution_span=(
                float(cfg["full_resolution_span"]) if cfg.get("full_resolution_span") is not None else None
            ),
        )
    return configs


def load_normalization_info(path: str | Path) -> NormalizationInfo:
    """Carga la versión de la regla de normalización desde ``config/sensors.yaml``."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return NormalizationInfo(version=raw["normalization"]["version"])


def resolve_worker_count(path: str | Path) -> int:
    """Resuelve el nivel de paralelismo por defecto (``os.cpu_count() - 1``, mínimo 1)."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    policy = raw.get("parallelism", {}).get("default_workers", "cpu_count_minus_one")
    if policy == "cpu_count_minus_one":
        cpu_count = os.cpu_count() or 2
        return max(1, cpu_count - 1)
    return int(policy)
