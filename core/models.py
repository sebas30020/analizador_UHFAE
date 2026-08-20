"""Modelo de datos canónico e independiente del origen (FASE0_DISENO_Analizador_UHF_AE.md §5).

Ninguna dimensión temporal está cableada aquí: fs, M y freq_limit se leen de
``config/sensors.yaml`` a través de :func:`load_sensor_configs`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import yaml

SensorName = Literal["UHF", "AE", "UHF_KS"]


@dataclass(frozen=True)
class SensorConfig:
    """Perfil de configuración de un sensor, leído de ``config/sensors.yaml``.

    ``decimate_full_view``/``has_trigger_metadata`` son propiedades del origen físico,
    no literales de presentación: qué sensor diezma en vista completa (gráfica #2) y
    cuál trae nivel de disparo por señal varía según el instrumento, así que viven aquí
    en vez de estar cableadas por nombre de sensor en ``ui/``.
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


@dataclass
class SignalBlock:
    """Bloque contiguo de señales de un sensor, ya en orden cronológico.

    Es la unidad de E/S entre ``data/ingest.py``, ``data/storage.py`` y el motor de
    métricas: nunca se carga la matriz global completa en RAM (§5.1, §9.1 del PROMPT).
    """

    data: np.ndarray            # (n, M) float32, cruda (sin normalizar)
    timestamps: np.ndarray      # (n,) float64
    trigger: np.ndarray         # (n,) float64
    vrange: np.ndarray          # (n,) float64
    valid_mask: np.ndarray      # (n,) bool
    minmax: np.ndarray          # (n, 2) float32 -> [min, max] por señal

    def __post_init__(self) -> None:
        n = self.data.shape[0]
        for name in ("timestamps", "trigger", "vrange", "valid_mask"):
            arr = getattr(self, name)
            if arr.shape[0] != n:
                raise ValueError(f"SignalBlock.{name} tiene {arr.shape[0]} filas, esperaba {n}")
        if self.minmax.shape != (n, 2):
            raise ValueError(f"SignalBlock.minmax debe tener forma ({n}, 2), tiene {self.minmax.shape}")


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
