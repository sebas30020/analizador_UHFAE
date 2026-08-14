"""Backend de caché de métricas (FASE0_DISENO_Analizador_UHF_AE.md §3.3, §8).

Decisión ya justificada en Fase 0: **SQLite para el índice** de claves (lookup puntual
por clave compuesta, transaccional, portable) + **HDF5 para los payloads** (arrays de
puntos, evita BLOBs grandes dentro de SQLite). La base origen y el archivo canónico de
Fase 1 nunca se tocan desde aquí -- este caché es un artefacto totalmente aparte.

``CacheBackend`` es una interfaz abstracta (inyección de dependencias, PROMPT §10.1):
cambiar de motor de caché en el futuro implica una nueva implementación, sin tocar
``cache/service.py`` ni nada aguas arriba.
"""
from __future__ import annotations

import sqlite3
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np


@dataclass
class CacheEntry:
    timestamps: np.ndarray
    values: np.ndarray
    is_partial: np.ndarray | None
    dataset_id: str
    sensor: str
    metric_id: str
    metric_version: int
    created_at: str


class CacheBackend(ABC):
    @abstractmethod
    def get(self, cache_key: str) -> CacheEntry | None: ...

    @abstractmethod
    def put(
        self,
        cache_key: str,
        canonical_json: str,
        dataset_id: str,
        sensor: str,
        metric_id: str,
        metric_version: int,
        timestamps: np.ndarray,
        values: np.ndarray,
        is_partial: np.ndarray | None = None,
    ) -> None: ...

    @abstractmethod
    def purge_orphaned(self, valid_dataset_ids: set[str], current_metric_versions: dict[str, int]) -> int:
        """Elimina entradas cuyo ``dataset_id`` ya no exista en disco, o cuya
        ``(metric_id, metric_version)`` ya no coincida con el registro de métricas
        activo. Retorna el número de entradas purgadas."""

    @abstractmethod
    def stats(self) -> dict[str, int]: ...

    @abstractmethod
    def close(self) -> None: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_entries (
    cache_key TEXT PRIMARY KEY,
    canonical_json TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    sensor TEXT NOT NULL,
    metric_id TEXT NOT NULL,
    metric_version INTEGER NOT NULL,
    n_points INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cache_dataset_id ON cache_entries(dataset_id);
CREATE INDEX IF NOT EXISTS idx_cache_metric ON cache_entries(metric_id, metric_version);
"""


class SqliteHdf5CacheBackend(CacheBackend):
    """Índice SQLite (``cache_index.sqlite``) + payloads HDF5 (``cache_payload.h5``),
    ambos dentro de ``cache_dir``. Se crean si no existen.

    Segura para usarse desde múltiples hilos con la **misma** instancia -- necesario
    porque el servidor de desarrollo de Dash/Flask atiende cada petición HTTP en un hilo
    del pool, no siempre el mismo en el que se creó ``AppState`` (``ui/state.py``). Bug
    real encontrado al probar la Fase 4 con el servidor corriendo de verdad
    (``sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in
    that same thread``): ``sqlite3.connect`` con ``check_same_thread=False`` por sí solo
    no basta, porque no serializa el acceso, así que además se protege cada operación
    con un ``threading.Lock`` (cubre también el acceso al HDF5 de payload, cuya librería
    C subyacente tampoco es segura para hilos concurrentes sin coordinación explícita).
    """

    def __init__(self, cache_dir: str | Path):
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._dir / "cache_index.sqlite"
        self._hdf5_path = self._dir / "cache_payload.h5"
        self._lock = threading.Lock()

        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def get(self, cache_key: str) -> CacheEntry | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT dataset_id, sensor, metric_id, metric_version, created_at "
                "FROM cache_entries WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if row is None:
                return None
            dataset_id, sensor, metric_id, metric_version, created_at = row

            if not self._hdf5_path.exists():
                return None
            with h5py.File(self._hdf5_path, mode="r") as f:
                if cache_key not in f:
                    return None
                grp = f[cache_key]
                timestamps = grp["timestamps"][:]
                values = grp["values"][:]
                is_partial = grp["is_partial"][:].astype(bool) if "is_partial" in grp else None

        return CacheEntry(
            timestamps=timestamps,
            values=values,
            is_partial=is_partial,
            dataset_id=dataset_id,
            sensor=sensor,
            metric_id=metric_id,
            metric_version=metric_version,
            created_at=created_at,
        )

    def put(
        self,
        cache_key: str,
        canonical_json: str,
        dataset_id: str,
        sensor: str,
        metric_id: str,
        metric_version: int,
        timestamps: np.ndarray,
        values: np.ndarray,
        is_partial: np.ndarray | None = None,
    ) -> None:
        with self._lock:
            with h5py.File(self._hdf5_path, mode="a") as f:
                if cache_key in f:
                    del f[cache_key]  # recálculo con la misma clave (no debería ocurrir,
                                      # se sobrescribe por robustez en vez de fallar)
                grp = f.create_group(cache_key)
                grp.create_dataset("timestamps", data=timestamps, compression="gzip", compression_opts=4)
                grp.create_dataset("values", data=values, compression="gzip", compression_opts=4)
                if is_partial is not None:
                    grp.create_dataset("is_partial", data=is_partial.astype(np.uint8))

            created_at = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "INSERT OR REPLACE INTO cache_entries "
                "(cache_key, canonical_json, dataset_id, sensor, metric_id, metric_version, n_points, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (cache_key, canonical_json, dataset_id, sensor, metric_id, metric_version, int(len(values)), created_at),
            )
            self._conn.commit()

    def purge_orphaned(self, valid_dataset_ids: set[str], current_metric_versions: dict[str, int]) -> int:
        with self._lock:
            rows = self._conn.execute(
                "SELECT cache_key, dataset_id, metric_id, metric_version FROM cache_entries"
            ).fetchall()
            orphaned_keys = [
                key
                for key, dataset_id, metric_id, metric_version in rows
                if dataset_id not in valid_dataset_ids or current_metric_versions.get(metric_id) != metric_version
            ]
            if not orphaned_keys:
                return 0

            if self._hdf5_path.exists():
                with h5py.File(self._hdf5_path, mode="a") as f:
                    for key in orphaned_keys:
                        if key in f:
                            del f[key]

            self._conn.executemany("DELETE FROM cache_entries WHERE cache_key = ?", [(k,) for k in orphaned_keys])
            self._conn.commit()
            return len(orphaned_keys)

    def stats(self) -> dict[str, int]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]
            return {"total_entries": total}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "SqliteHdf5CacheBackend":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
