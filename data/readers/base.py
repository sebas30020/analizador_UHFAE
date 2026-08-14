"""Interfaz abstracta de lectura del origen (FASE0_DISENO_Analizador_UHF_AE.md §2.3, §4).

Es la única capa que puede conocer los detalles internos del formato de origen (en el
esquema real: HDF5 con chunks, ver ``esquema_med_5_ago_3.md``). Todo lo que esté aguas
arriba (``data/ingest.py`` en adelante) solo ve estos métodos — nunca abre h5py
directamente. Cambiar de motor de origen (§10.3, prueba de fuego #3 del PROMPT maestro)
implica escribir una nueva implementación de :class:`OriginReader`, sin tocar nada más.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator

import numpy as np

from core.models import EnvironmentalSeries, EventSeries, SensorName


@dataclass
class RawSignalBatch:
    """Lote crudo de señales de un sensor, tal como lo entrega el origen.

    Sin ordenar globalmente y sin ``valid_mask``/``minmax`` — esas responsabilidades son
    de ``data/ingest.py``, no del reader. El origen real entrega estos lotes por chunk,
    pero eso es un detalle de implementación del reader: el llamador solo ve una
    secuencia de lotes a concatenar.
    """

    data: np.ndarray        # (n, M) float32
    timestamps: np.ndarray  # (n,) float64
    trigger: np.ndarray     # (n,) float64
    vrange: np.ndarray      # (n,) float64


class OriginReader(ABC):
    """Interfaz de solo lectura sobre una base de datos origen.

    El origen se trata siempre como estrictamente de solo lectura (§8.2, §3.2 del
    PROMPT maestro) — ninguna implementación debe escribir sobre el archivo/BD origen.
    """

    @abstractmethod
    def __enter__(self) -> "OriginReader": ...

    @abstractmethod
    def __exit__(self, *exc_info: object) -> None: ...

    @property
    @abstractmethod
    def dataset_id(self) -> str:
        """Identificador estable del dataset origen, usado en la clave de caché (§7)."""

    @abstractmethod
    def list_experiments(self) -> list[str]:
        """Lista los experimentos ("test groups") contenidos en el origen.

        En el esquema real inspeccionado hay normalmente 1 por archivo, pero la
        interfaz soporta N para no asumir esa particularidad (§10 supuesto #1).
        """

    @abstractmethod
    def get_experiment_attrs(self, experiment: str) -> dict:
        """Metadatos del experimento (fecha, duración de chunk, descripción, versión...)."""

    @abstractmethod
    def iter_signal_batches(self, experiment: str, sensor: SensorName) -> Iterator[RawSignalBatch]:
        """Itera lotes crudos de señales de ``sensor`` dentro de ``experiment``.

        No garantiza orden cronológico global entre lotes — el llamador (``ingest.py``)
        debe ordenar explícitamente (FASE0_DISENO...md §5.1: "ordenamiento explícito,
        no asumido").
        """

    @abstractmethod
    def get_environmental(self, experiment: str) -> EnvironmentalSeries:
        """Serie ambiental completa del experimento (concatenada y ordenada por tiempo)."""

    @abstractmethod
    def get_events(self, experiment: str) -> EventSeries:
        """Serie de eventos completa del experimento (concatenada y ordenada por tiempo)."""
