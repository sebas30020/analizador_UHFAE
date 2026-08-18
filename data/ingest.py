"""Desempaquetado de chunks -> matriz global (FASE0_DISENO_Analizador_UHF_AE.md §2.2, Fase 1).

El concepto de "chunk" desaparece aquí: todo lo aguas arriba de este módulo (motor de
métricas, agrupamiento, UI) opera exclusivamente sobre la :class:`~core.models.SignalBlock`
resultante, ordenada cronológicamente de forma estricta.

Nota de escalado (riesgo de rendimiento documentado): esta implementación construye la
matriz global concatenada en RAM antes de persistirla vía ``data/storage.py``. Es una
decisión razonable bajo el supuesto de máquina de desarrollo con >= 16 GB de RAM
(FASE0_DISENO...md §10, supuesto #7) y datasets del orden de cientos de MB a ~1 GB por
sensor (tamaño real observado en ``med_5_ago_3.hdf5``). Si en el futuro los datasets
superan el presupuesto de RAM disponible, este módulo debería pasar a un modo de
escritura incremental por bloques directamente contra ``storage.py`` sin ensamblar el
array completo en memoria — no necesario para el alcance de la Fase 1.
"""
from __future__ import annotations

import numpy as np

from core.models import EnvironmentalSeries, EventSeries, IngestResult, NormalizationInfo, SensorName, SignalBlock
from core.normalization import NORMALIZATION_VERSION, compute_valid_mask
from data.readers.base import OriginReader
from utils.profiling import stage


def compute_minmax(data: np.ndarray) -> np.ndarray:
    """Par (min, max) por señal — se calcula una sola vez en ingesta y se persiste
    (PROMPT maestro §5.1: "nunca se recalcula en tiempo de render")."""
    if data.shape[0] == 0:
        return np.empty((0, 2), dtype=np.float32)
    return np.stack([data.min(axis=1), data.max(axis=1)], axis=1).astype(np.float32)


def ingest_sensor(reader: OriginReader, experiment: str, sensor: SensorName) -> SignalBlock:
    """Concatena todos los lotes de ``sensor`` en ``experiment``, ordena por timestamp
    absoluto de forma estricta y calcula ``valid_mask``/``minmax``.

    El orden de llegada de los lotes del reader **no** se asume cronológico (aunque en el
    origen real coincide con el orden de almacenamiento de los chunks) — se ordena
    explícitamente aquí, tal como exige FASE0_DISENO...md §5.1.
    """
    with stage("ingesta.sensor", sensor=sensor) as ctx:
        data_parts: list[np.ndarray] = []
        ts_parts: list[np.ndarray] = []
        trigger_parts: list[np.ndarray] = []
        vrange_parts: list[np.ndarray] = []

        for batch in reader.iter_signal_batches(experiment, sensor):
            data_parts.append(batch.data)
            ts_parts.append(batch.timestamps)
            trigger_parts.append(batch.trigger)
            vrange_parts.append(batch.vrange)

        if not data_parts:
            ctx["n_senales"] = 0
            return SignalBlock(
                data=np.empty((0, 0), dtype=np.float32),
                timestamps=np.array([], dtype=np.float64),
                trigger=np.array([], dtype=np.float64),
                vrange=np.array([], dtype=np.float64),
                valid_mask=np.array([], dtype=bool),
                minmax=np.empty((0, 2), dtype=np.float32),
            )

        # np.concatenate de partes float32 ya devuelve float32 -- el .astype
        # incondicional era una copia completa regalada (ver plan de memoria). Y
        # data_parts se libera de inmediato: ya no hace falta durante el reordenamiento
        # y quedaba viva innecesariamente hasta el final de la función.
        data = np.concatenate(data_parts, axis=0)
        if data.dtype != np.float32:
            data = data.astype(np.float32)
        del data_parts
        timestamps = np.concatenate(ts_parts).astype(np.float64)
        trigger = np.concatenate(trigger_parts).astype(np.float64)
        vrange = np.concatenate(vrange_parts).astype(np.float64)

        order = np.argsort(timestamps, kind="stable")
        data = data[order]
        timestamps = timestamps[order]
        trigger = trigger[order]
        vrange = vrange[order]

        valid_mask = compute_valid_mask(trigger, vrange)
        minmax = compute_minmax(data)
        ctx["n_senales"] = data.shape[0]

        return SignalBlock(
            data=data,
            timestamps=timestamps,
            trigger=trigger,
            vrange=vrange,
            valid_mask=valid_mask,
            minmax=minmax,
        )


def ingest_environmental(reader: OriginReader, experiment: str) -> EnvironmentalSeries:
    """Serie ambiental completa del experimento (delegada al reader, ya ordenada)."""
    return reader.get_environmental(experiment)


def ingest_events(reader: OriginReader, experiment: str) -> EventSeries:
    """Serie de eventos completa del experimento (delegada al reader, ya ordenada)."""
    return reader.get_events(experiment)


def ingest_experiment(reader: OriginReader, experiment: str, sensors: list[SensorName]) -> IngestResult:
    """Orquesta la ingesta completa de un experimento: todas las matrices de sensor,
    ambientales y eventos, listas para persistir con ``data/storage.py``."""
    with stage("ingesta.experimento", experimento=experiment) as ctx:
        sensor_blocks = {sensor: ingest_sensor(reader, experiment, sensor) for sensor in sensors}
        ctx["n_senales"] = sum(b.data.shape[0] for b in sensor_blocks.values())
        return IngestResult(
            dataset_id=reader.dataset_id,
            experiment=experiment,
            sensors=sensor_blocks,
            environmental=ingest_environmental(reader, experiment),
            events=ingest_events(reader, experiment),
            normalization=NormalizationInfo(version=NORMALIZATION_VERSION),
        )
